"""
Orchestrator — central coordinator of GhostPipe.

This is the single function the CLI calls. It:
  1. Validates config (API key present, dirs exist)
  2. Classifies the user's intent via Groq/Llama 3.1
  3. Resolves a start URL from the intent (or a Google search fallback)
  4. Launches a stealth Playwright browser and navigates to the target
  5. Runs the obstacle handler (login walls, cookie banners, gate pages)
  6. Routes to the correct pipeline (binary or text/RAG)
  7. Returns a structured GhostPipeResult — never raises

All browser and pipeline errors are caught and surfaced in the result
so the CLI/dashboard layer can decide how to display them.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

from groq import Groq

import config
from core.intent_parser import classify_intent
from core.router import choose_pipeline, Pipeline
from browser.navigator import Navigator
from browser.obstacle_handler import handle_obstacles, ObstacleResult
from pipelines import binary_pipeline, text_pipeline
from pipelines.text_pipeline import IngestResult
from transfer.aria2_manager import DownloadResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #

@dataclass
class GhostPipeResult:
    success: bool
    pipeline: str                        # "binary" | "text" | "unknown"
    intent: dict = field(default_factory=dict)

    # Binary pipeline output (set when pipeline == "binary")
    download: DownloadResult | None = None

    # Text/RAG pipeline output (set when pipeline == "text")
    ingest: IngestResult | None = None

    # Obstacle handling summary
    obstacle: ObstacleResult | None = None

    error: str | None = None

    # --- Convenience properties -----------------------------------------

    @property
    def filepath(self) -> Path | None:
        """Shortcut: path to the downloaded file (binary pipeline only)."""
        return self.download.filepath if self.download else None

    @property
    def chunks_stored(self) -> int:
        """Shortcut: number of RAG chunks stored (text pipeline only)."""
        return self.ingest.chunks_stored if self.ingest else 0


# --------------------------------------------------------------------------- #
# URL resolution
# --------------------------------------------------------------------------- #

_DOMAIN_RE = re.compile(r"^[\w.-]+\.[a-z]{2,}$", re.I)


def _resolve_start_url(intent: dict) -> str | None:
    """
    Derive the starting URL to navigate to from the parsed intent.

    Priority:
      1. target_site if it already has a scheme (http/https)
      2. target_site as a bare domain → prepend https://
      3. search_hint → Google search as starting point
      4. None — caller must handle this case
    """
    site = (intent.get("target_site") or "").strip()

    if site:
        if site.startswith(("http://", "https://")):
            return site
        if _DOMAIN_RE.match(site):
            return f"https://{site}"

    hint = (intent.get("search_hint") or "").strip()
    if hint:
        return f"https://www.google.com/search?q={quote_plus(hint)}"

    return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

async def run(
    user_request: str,
    credentials: dict | None = None,
    dest_dir: str | Path | None = None,
    headless: bool | None = None,
) -> GhostPipeResult:
    """
    Run the full GhostPipe pipeline for a natural-language request.

    Args:
        user_request:
            Raw NL instruction, e.g.
            "Get the Q3 earnings PDF from Apple's investor portal"
        credentials:
            Optional {"username": ..., "password": ...} passed to the
            obstacle handler for login walls.
        dest_dir:
            Override download output directory (binary pipeline only).
            Defaults to config.DOWNLOAD_DIR.
        headless:
            Override headless mode. Reads config.HEADLESS if None.

    Returns:
        GhostPipeResult — always returned, never raises.
        Check .success and .error first, then .download or .ingest
        depending on .pipeline.
    """

    # --- 0. Validate config -------------------------------------------
    errors = config.validate()
    if errors:
        return GhostPipeResult(
            success=False,
            pipeline="unknown",
            error="; ".join(errors),
        )
    config.ensure_dirs()

    groq_client = Groq(api_key=config.GROQ_API_KEY)

    # --- 1. Intent classification --------------------------------------
    logger.info("─── GhostPipe starting ─────────────────────────────────")
    logger.info("Request: %r", user_request)

    intent = classify_intent(user_request, client=groq_client)
    logger.info(
        "Intent → type=%s  site=%s  confidence=%.2f  desc=%s",
        intent["target_type"],
        intent.get("target_site"),
        intent["confidence"],
        intent.get("description"),
    )

    if intent.get("error"):
        logger.warning("Intent parser warning: %s", intent["error"])

    # --- 2. Resolve start URL -----------------------------------------
    start_url = _resolve_start_url(intent)
    if not start_url:
        return GhostPipeResult(
            success=False,
            pipeline=intent["target_type"],
            intent=intent,
            error=(
                "Could not determine where to navigate. "
                "Try including a website name or URL in your request."
            ),
        )

    logger.info("Start URL: %s", start_url)

    # --- 3. Browser session -------------------------------------------
    async with Navigator(headless=headless) as nav:

        # Navigate to start
        try:
            await nav.goto(start_url)
        except Exception as e:
            return GhostPipeResult(
                success=False,
                pipeline=intent["target_type"],
                intent=intent,
                error=f"Navigation failed for {start_url}: {e}",
            )

        logger.info("Landed on: %s", nav.current_url)

        # --- 4. Obstacle handling -------------------------------------
        obstacle_result = await handle_obstacles(
            page=nav.page,
            goal=intent["description"],
            credentials=credentials,
            groq_client=groq_client,
        )

        if obstacle_result.cleared:
            logger.info("Obstacles cleared ✓")
        else:
            logger.warning("Obstacle not fully cleared: %s", obstacle_result.error)
            # Don't abort — partial clears (e.g. cookie banner dismissed but
            # login wall still up) still let the text pipeline run usefully

        # --- 5. Route to pipeline ------------------------------------
        pipeline = choose_pipeline(
            intent=intent,
            current_url=nav.current_url or "",
        )
        logger.info("Pipeline: %s", pipeline.value)

        # --- 6a. Binary pipeline -------------------------------------
        if pipeline == Pipeline.BINARY:
            try:
                dl_result = await binary_pipeline.run(
                    page=nav.page,
                    goal=intent["description"],
                    filename_hint=intent.get("filename_hint"),
                    dest_dir=dest_dir,
                    groq_client=groq_client,
                )
            except Exception as e:
                logger.exception("Binary pipeline error: %s", e)
                dl_result = DownloadResult(
                    success=False, gid="", error=str(e)
                )

            return GhostPipeResult(
                success=dl_result.success,
                pipeline=pipeline.value,
                intent=intent,
                download=dl_result,
                obstacle=obstacle_result,
                error=dl_result.error,
            )

        # --- 6b. Text / RAG pipeline ---------------------------------
        else:
            try:
                ingest_result = await text_pipeline.run(
                    page=nav.page,
                    source_url=nav.current_url,
                )
            except Exception as e:
                logger.exception("Text pipeline error: %s", e)
                ingest_result = IngestResult(
                    success=False,
                    source_url=nav.current_url or "",
                    chunks_stored=0,
                    char_count=0,
                    error=str(e),
                )

            return GhostPipeResult(
                success=ingest_result.success,
                pipeline=pipeline.value,
                intent=intent,
                ingest=ingest_result,
                obstacle=obstacle_result,
                error=ingest_result.error,
            )


# --------------------------------------------------------------------------- #
# Sync wrapper for CLI / non-async callers
# --------------------------------------------------------------------------- #

def run_sync(
    user_request: str,
    credentials: dict | None = None,
    dest_dir: str | Path | None = None,
    headless: bool | None = None,
) -> GhostPipeResult:
    """
    Synchronous wrapper around run() for CLI use.

    Identical signature — just blocks until the async coroutine completes.
    """
    return asyncio.run(run(
        user_request=user_request,
        credentials=credentials,
        dest_dir=dest_dir,
        headless=headless,
    ))

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("🚀 Running FULL GhostPipe End-to-End Test!")
    
    # We are passing a raw English command. The Orchestrator has to figure out 
    # what to do, which website to visit, and which pipeline to trigger!
    prompt = "Download the 100KB test PDF from https://freetestdata.com/document-files/pdf/"
    
    # run_sync is the magic function our CLI will eventually use
    result = run_sync(prompt, headless=False)

    print("\n" + "="*40)
    print("🎉 END-TO-END RESULT 🎉")
    print("="*40)
    print(f"Success:  {result.success}")
    print(f"Pipeline: {result.pipeline.upper()}")
    
    if result.pipeline == "binary" and result.download:
        print(f"Saved to: {result.filepath}")
    elif result.pipeline == "text" and result.ingest:
        print(f"Chunks Stored: {result.chunks_stored}")
        
    if result.error:
        print(f"Error: {result.error}")
    print("="*40)
