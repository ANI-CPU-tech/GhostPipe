"""
Text/RAG Pipeline — render → extract → chunk → embed → store.

Flow:
  1. Receive an active, obstacle-cleared Playwright Page.
  2. Grab the fully rendered HTML (JS already executed by the browser).
  3. Pass it to Trafilatura to strip navigation, ads, and boilerplate
     — producing clean Markdown/plain-text.
  4. Chunk the clean text into embedding-sized passages (rag.chunker).
  5. Embed + upsert all chunks into ChromaDB (rag.chroma_store).
  6. Return an IngestResult summarising what was stored.

After this pipeline completes, the caller can immediately run semantic
search over the ingested content via ChromaStore.query().
"""

import logging
from dataclasses import dataclass, field

import trafilatura
from playwright.async_api import Page

from rag.chunker import Chunk, chunk_text
from rag.chroma_store import ChromaStore, QueryResult

logger = logging.getLogger(__name__)

# Trafilatura extraction settings
_TRAF_OPTS = dict(
    include_comments=False,
    include_tables=True,
    no_fallback=False,          # use fallback extractor if primary fails
    favor_precision=False,      # recall > precision for RAG use-case
    output_format="markdown",
)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class IngestResult:
    success: bool
    source_url: str
    chunks_stored: int
    char_count: int
    error: str | None = None
    sample_chunks: list[Chunk] = field(default_factory=list)  # first 3, for logging


# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #

def _extract_text(html: str, url: str) -> str | None:
    """
    Run Trafilatura on raw HTML. Returns clean Markdown string or None
    if extraction fails (e.g. empty page, paywall, pure JS shell).
    """
    text = trafilatura.extract(html, url=url, **_TRAF_OPTS)
    if not text or len(text.strip()) < 100:
        # Trafilatura sometimes returns None for JS-heavy pages even after
        # Playwright renders them. Try the bare-minimum fallback.
        text = trafilatura.extract(
            html, url=url,
            include_comments=False,
            no_fallback=True,
            favor_recall=True,
            output_format="txt",
        )
    return text.strip() if text else None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

async def run(
    page: Page,
    source_url: str | None = None,
    store: ChromaStore | None = None,
    chunk_size: int = 1200,
    overlap: int = 200,
) -> IngestResult:
    """
    Run the full text/RAG pipeline for an obstacle-cleared page.

    Args:
        page:       Active Playwright Page, already past any login/gates.
        source_url: Canonical URL to tag chunks with (defaults to page.url).
        store:      Optional pre-built ChromaStore (created fresh if None).
        chunk_size: Target chunk size in chars (passed to chunker).
        overlap:    Overlap between adjacent chunks in chars.

    Returns:
        IngestResult — check .success and .error for status.
        On success, .chunks_stored > 0 and semantic search is available
        immediately via ChromaStore.query().
    """
    url = source_url or page.url
    store = store or ChromaStore()

    # 1. Get rendered HTML
    logger.info("Text pipeline: capturing rendered HTML from %s", url)
    try:
        html = await page.content()
    except Exception as e:
        return IngestResult(
            success=False, source_url=url, chunks_stored=0, char_count=0,
            error=f"Failed to get page content: {e}",
        )

    # 2. Extract clean text with Trafilatura
    logger.info("Running Trafilatura extraction...")
    text = _extract_text(html, url)

    if not text:
        return IngestResult(
            success=False, source_url=url, chunks_stored=0, char_count=0,
            error=(
                "Trafilatura could not extract meaningful text. "
                "The page may be a pure JS shell, paywalled, or empty."
            ),
        )

    logger.info("Extracted %d chars of clean text", len(text))

    # 3. Chunk the text
    chunks = chunk_text(
        text,
        source_url=url,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    if not chunks:
        return IngestResult(
            success=False, source_url=url, chunks_stored=0, char_count=len(text),
            error="Chunker produced no chunks — text may be too short or malformed.",
        )

    logger.info("Produced %d chunks", len(chunks))

    # 4. Embed + store in ChromaDB
    try:
        stored = store.store(chunks)
    except Exception as e:
        return IngestResult(
            success=False, source_url=url, chunks_stored=0, char_count=len(text),
            error=f"ChromaDB storage failed: {e}",
        )

    return IngestResult(
        success=True,
        source_url=url,
        chunks_stored=stored,
        char_count=len(text),
        sample_chunks=chunks[:3],
    )


# --------------------------------------------------------------------------- #
# Convenience: query after ingest (for demo / CLI use)
# --------------------------------------------------------------------------- #

def search(
    query_text: str,
    n_results: int = 5,
    source_url: str | None = None,
    store: ChromaStore | None = None,
) -> list[QueryResult]:
    """
    Run a semantic search over previously ingested content.

    Can be called immediately after run() with the same ChromaStore instance,
    or on a fresh ChromaStore that reads from the persisted DB on disk.
    """
    s = store or ChromaStore()
    results = s.query(query_text, n_results=n_results, source_url=source_url)
    return results


