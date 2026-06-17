"""
Binary Pipeline — resolves an authenticated download URL from a rendered
browser page, then hands it to aria2c for the actual transfer.

Flow:
  1. Receive an active, obstacle-cleared Playwright Page.
  2. Ask Groq/Llama 3.1 to inspect the DOM and identify the final
     download trigger (button, link, or already-resolved URL).
  3. If a trigger element exists, click it and intercept the resulting
     network request to capture the real URL + any redirect headers.
  4. Extract session cookies from the Playwright context.
  5. Hand URL + cookies + headers to Aria2Manager.add_download().
  6. Stream progress back to the caller via Aria2Manager.watch().

The binary data NEVER passes through Python memory — only the URL and
auth material do.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from groq import Groq
from playwright.async_api import Page, Request, Response

import config
from transfer.aria2_manager import Aria2Manager, DownloadResult

logger = logging.getLogger(__name__)

# How long to wait for a network request after clicking a download trigger
INTERCEPT_TIMEOUT_MS = 12_000
# File extensions we treat as binary/download resources
BINARY_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage",
    ".iso", ".img",
    ".bin", ".dat",
    ".mp4", ".mkv", ".avi", ".mov",
    ".pdf",
    ".pt", ".safetensors", ".gguf", ".ckpt",   # model weights
    ".csv", ".parquet", ".jsonl",               # datasets
}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class ResolvedDownload:
    url: str
    cookies: list[dict]
    headers: dict                 # extra headers (Referer, etc.)
    filename_hint: str | None
    method: str                   # "direct" | "intercept" | "llm_trigger"


# --------------------------------------------------------------------------- #
# LLM: identify the download trigger on the page
# --------------------------------------------------------------------------- #

_TRIGGER_SYSTEM_PROMPT = """You are the download-resolution module of \
GhostPipe, an autonomous data-ingestion agent.

Given a DOM snapshot of visible interactive elements and the current URL, \
identify how to obtain the actual binary download resource.

Respond ONLY with a JSON object:
{
  "strategy": "direct_url" | "click_trigger" | "already_downloading",
  "url": "the direct download URL if strategy is direct_url, else null",
  "selector": "CSS/text selector of the element to click if strategy is \
click_trigger, else null",
  "filename_hint": "expected filename or null",
  "reason": "one sentence explanation"
}

Strategies:
- "direct_url": the URL itself (or a link visible in the DOM) is the \
  direct download link — no clicking needed.
- "click_trigger": a button/link must be clicked to initiate or reveal \
  the real download URL.
- "already_downloading": the page has already started the download \
  (e.g. browser auto-download triggered).
"""

_DOM_SNAPSHOT_JS = """
() => {
    const sel = ['a[href]','button','[role="button"]','input[type="submit"]',
                  '[class*="download"]','[class*="btn"]','[id*="download"]'];
    const els = document.querySelectorAll(sel.join(','));
    const out = [];
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        out.push({
            tag:  el.tagName.toLowerCase(),
            id:   el.id || null,
            cls:  (el.className || '').toString().trim().slice(0, 80),
            text: (el.innerText || '').trim().slice(0, 80),
            href: el.href || null,
        });
        if (out.length >= 30) break;
    }
    return out;
}
"""


async def _identify_trigger(
    page: Page,
    goal: str,
    client: Groq,
) -> dict:
    """Ask the LLM how to get the download URL from the current page."""
    elements = await page.evaluate(_DOM_SNAPSHOT_JS)
    dom_json = json.dumps(elements, indent=2)

    user_content = (
        f"GOAL: {goal}\n\n"
        f"CURRENT URL: {page.url}\n\n"
        f"DOM SNAPSHOT:\n{dom_json}"
    )

    try:
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": _TRIGGER_SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.warning("LLM trigger identification failed: %s", e)
        return {
            "strategy": "direct_url",
            "url": page.url,
            "selector": None,
            "filename_hint": None,
            "reason": f"LLM failed, falling back to current URL: {e}",
        }


# --------------------------------------------------------------------------- #
# URL resolution helpers
# --------------------------------------------------------------------------- #

def _looks_binary(url: str) -> bool:
    """Heuristic: does this URL path end with a known binary extension?"""
    path = url.split("?")[0].lower()
    return any(path.endswith(ext) for ext in BINARY_EXTENSIONS)


async def _resolve_via_intercept(
    page: Page,
    selector: str,
) -> str | None:
    """
    Click `selector` and intercept the first network request that looks
    like a binary download. Returns the intercepted URL or None on timeout.
    """
    resolved_url: list[str] = []   # mutable container for closure

    async def _on_request(request: Request) -> None:
        url = request.url
        if not resolved_url and (
            _looks_binary(url)
            or "download" in url.lower()
            or request.resource_type in ("fetch", "xhr", "document")
        ):
            resolved_url.append(url)

    page.on("request", _on_request)
    try:
        await page.wait_for_selector(selector, timeout=10000)
        await page.click(selector, timeout=6000)
        deadline = asyncio.get_event_loop().time() + INTERCEPT_TIMEOUT_MS / 1000
        while not resolved_url and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)
    except Exception as e:
        logger.warning("Click on %r failed: %s", selector, e)
    finally:
        page.remove_listener("request", _on_request)

    return resolved_url[0] if resolved_url else None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

async def run(
    page: Page,
    goal: str,
    filename_hint: str | None = None,
    dest_dir: str | Path | None = None,
    groq_client: Groq | None = None,
    aria2_manager: Aria2Manager | None = None,
) -> DownloadResult:
    """
    Run the full binary pipeline for an obstacle-cleared page.

    Args:
        page:          Active Playwright Page, already past any login/gates.
        goal:          Natural-language description of what we're downloading.
        filename_hint: Optional expected filename (from intent parser).
        dest_dir:      Where to save the file (defaults to config.DOWNLOAD_DIR).
        groq_client:   Optional pre-built Groq client.
        aria2_manager: Optional pre-built Aria2Manager (must already be started).

    Returns:
        DownloadResult with .success, .filepath, and optional .error.
    """
    client = groq_client or Groq(api_key=config.GROQ_API_KEY)
    dest_dir = Path(dest_dir or config.DOWNLOAD_DIR)

    # 1. Ask LLM how to get the download URL
    logger.info("Binary pipeline: identifying download trigger on %s", page.url)
    trigger = await _identify_trigger(page, goal, client)
    strategy      = trigger.get("strategy", "direct_url")
    direct_url    = trigger.get("url")
    selector      = trigger.get("selector")
    fn_hint       = filename_hint or trigger.get("filename_hint")

    logger.info("Trigger strategy=%s  url=%s  selector=%s", strategy, direct_url, selector)

    # 2. Resolve the actual download URL
    download_url: str | None = None
    method = "direct"

    if strategy == "direct_url" and direct_url:
        download_url = direct_url
        method = "direct"

    elif strategy == "click_trigger" and selector:
        intercepted = await _resolve_via_intercept(page, selector)
        if intercepted:
            download_url = intercepted
            method = "intercept"
        elif direct_url:
            # Fall back to LLM-provided URL if intercept missed
            download_url = direct_url
            method = "llm_trigger"

    elif strategy == "already_downloading":
        # Nothing more to do — the browser already triggered it.
        # We can't hand this to aria2c cleanly; report as a limitation.
        logger.warning("Page is already downloading — cannot intercept for aria2c handoff")
        return DownloadResult(
            success=False,
            gid="",
            error="Browser already started the download — aria2c handoff not possible. "
                  "Try a direct link instead.",
        )

    if not download_url:
        return DownloadResult(
            success=False,
            gid="",
            error=f"Could not resolve a download URL (strategy={strategy})",
        )

    # 3. Extract session cookies + basic headers
    cookies = await page.context.cookies()
    headers = {"Referer": page.url}

    resolved = ResolvedDownload(
        url=download_url,
        cookies=cookies,
        headers=headers,
        filename_hint=fn_hint,
        method=method,
    )
    logger.info("Resolved download: %s  method=%s", resolved.url, resolved.method)

    # 4. Hand off to aria2c
    own_manager = aria2_manager is None
    mgr = aria2_manager or Aria2Manager()

    try:
        if own_manager:
            await mgr.start()

        gid = await mgr.add_download(
            url=resolved.url,
            cookies=resolved.cookies,
            headers=resolved.headers,
            dest_dir=dest_dir,
            filename=resolved.filename_hint,
        )

        result = await mgr.wait_for_completion(gid)
        return result

    finally:
        if own_manager:
            await mgr.stop()
