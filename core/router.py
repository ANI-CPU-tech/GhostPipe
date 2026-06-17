"""
Router — decides which pipeline to run based on the intent parser's output
and optional runtime signals from the live page.

Primary signal:   intent["target_type"] from the LLM ("binary" | "text")
Secondary signal: URL heuristics on the resolved page (file extension, etc.)
Tertiary signal:  Response Content-Type header if captured during navigation

The router returns a Pipeline enum value. The orchestrator acts on it.

Default is always TEXT — it is far safer to accidentally ingest a page
into ChromaDB than to accidentally kick off a 100GB aria2c transfer.
"""

import logging
import re
from enum import Enum

logger = logging.getLogger(__name__)

# URL path extensions that force binary pipeline regardless of LLM output
BINARY_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage",
    ".iso", ".img", ".bin", ".dat",
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".flac",
    ".pt", ".safetensors", ".gguf", ".ckpt",       # model weights
    ".csv", ".parquet", ".jsonl",                   # datasets
    ".npy", ".npz",                                 # numpy arrays
}

# Content-Type fragments that force binary pipeline
BINARY_CONTENT_TYPES = {
    "application/zip",
    "application/x-tar",
    "application/octet-stream",
    "application/x-msdownload",
    "application/x-iso9660-image",
    "video/",
    "audio/",
}


class Pipeline(str, Enum):
    BINARY = "binary"
    TEXT   = "text"


def _url_looks_binary(url: str) -> bool:
    """Heuristic: does the URL path end with a known binary extension?"""
    path = url.split("?")[0].lower()
    return any(path.endswith(ext) for ext in BINARY_EXTENSIONS)


def _content_type_is_binary(content_type: str) -> bool:
    ct = content_type.lower()
    return any(frag in ct for frag in BINARY_CONTENT_TYPES)


def choose_pipeline(
    intent: dict,
    current_url: str = "",
    response_content_type: str = "",
) -> Pipeline:
    """
    Decide which pipeline to run for the current page state.

    Decision priority (highest → lowest):
      1. Response Content-Type header — most authoritative, hard binary signal
      2. URL path extension heuristic — reliable for direct download links
      3. LLM intent["target_type"] — used when signals above are absent
      4. Default → TEXT (safest fallback)

    Args:
        intent:
            Dict from core.intent_parser.classify_intent().
        current_url:
            The URL the browser is on after navigation + obstacle handling
            (may differ from the original target due to redirects).
        response_content_type:
            Content-Type header if captured by the navigator (optional).

    Returns:
        Pipeline.BINARY or Pipeline.TEXT
    """
    # 1. Content-Type is definitive
    if response_content_type and _content_type_is_binary(response_content_type):
        logger.info("Router → BINARY  [content-type: %s]", response_content_type)
        return Pipeline.BINARY

    # 2. URL extension
    if current_url and _url_looks_binary(current_url):
        logger.info("Router → BINARY  [URL extension: %s]", current_url)
        return Pipeline.BINARY

    # 3. LLM classification
    llm_type   = intent.get("target_type", "text")
    confidence = float(intent.get("confidence", 0.0))

    if llm_type == "binary":
        logger.info("Router → BINARY  [LLM, confidence=%.2f]", confidence)
        return Pipeline.BINARY

    # 4. Default — TEXT
    logger.info("Router → TEXT  [LLM=%s, confidence=%.2f]", llm_type, confidence)
    return Pipeline.TEXT
