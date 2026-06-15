"""
Intent Parser — Groq/Llama 3.1

Takes a natural-language request from the user and classifies it so the
rest of GhostPipe knows which pipeline to run and where to start looking.

Output schema (dict):
{
    "target_type": "binary" | "text",
    "target_site": str | null,      # best-guess domain/URL to start from
    "search_hint": str | null,      # what to search for if no direct site
    "description": str,             # plain-language summary of what's wanted
    "filename_hint": str | null,    # expected filename/extension if known
    "confidence": float             # 0.0 - 1.0
}

"target_type":
    "binary" -> large/binary resource (installers, datasets, archives,
                model weights, media files, etc.) -> binary_pipeline.py
    "text"   -> article/document/webpage to read or ingest for RAG
                -> text_pipeline.py
"""

import json
import logging

from groq import Groq

import config

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the intent-classification module of GhostPipe, \
an autonomous data-ingestion agent.

Given a user's natural-language request, determine:
1. Whether the user wants a BINARY resource (a large file to download and \
save to disk — e.g. installer, archive, dataset, model weights, video, \
ISO, executable) or a TEXT resource (an article, report, page, or document \
whose CONTENT should be read/extracted for retrieval-augmented generation).
2. The most likely website/URL to start navigation from, if the user named \
one or it's strongly implied (e.g. "investor portal" for a public company \
implies their IR site). If unclear, set target_site to null.
3. If target_site is null, a short search_hint describing what to search \
for to find the right site.
4. A one-sentence plain-language description of the goal.
5. A filename_hint if a specific file/format is implied (e.g. "Q3-earnings.pdf"), \
otherwise null.
6. A confidence score from 0.0 to 1.0 for your classification.

Respond with ONLY a single JSON object, no markdown fences, no preamble, \
matching exactly this schema:
{
  "target_type": "binary" | "text",
  "target_site": string | null,
  "search_hint": string | null,
  "description": string,
  "filename_hint": string | null,
  "confidence": number
}
"""


def _build_client() -> Groq:
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return Groq(api_key=config.GROQ_API_KEY)


def _fallback_result(user_request: str, error: str | None = None) -> dict:
    """Conservative default if the LLM call fails or returns bad JSON."""
    return {
        "target_type": "text",
        "target_site": None,
        "search_hint": user_request,
        "description": user_request,
        "filename_hint": None,
        "confidence": 0.0,
        "error": error,
    }


def classify_intent(user_request: str, client: Groq | None = None) -> dict:
    """
    Classify a natural-language request into a structured intent dict.

    Args:
        user_request: The raw natural-language instruction from the user,
            e.g. "Get the latest Q3 earnings report from the investor portal"
            or "Download the 114GB installer from this game's site".
        client: Optional pre-built Groq client (mainly for testing).

    Returns:
        dict matching the schema described in the module docstring.
        On failure, returns a "text"-typed fallback with confidence 0.0
        and an "error" key describing what went wrong.
    """
    if not user_request or not user_request.strip():
        return _fallback_result(user_request, error="Empty request")

    try:
        client = client or _build_client()

        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_request.strip()},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        result = json.loads(raw)

    except json.JSONDecodeError as e:
        logger.warning("Intent parser returned invalid JSON: %s", e)
        return _fallback_result(user_request, error=f"Invalid JSON from LLM: {e}")
    except Exception as e:  # noqa: BLE001 - surface any API/auth/network error
        logger.warning("Intent parser failed: %s", e)
        return _fallback_result(user_request, error=str(e))

    # --- Normalize / validate fields ---
    target_type = result.get("target_type")
    if target_type not in ("binary", "text"):
        target_type = "text"

    confidence = result.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "target_type": target_type,
        "target_site": result.get("target_site") or None,
        "search_hint": result.get("search_hint") or None,
        "description": result.get("description") or user_request.strip(),
        "filename_hint": result.get("filename_hint") or None,
        "confidence": confidence,
    }


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python -m core.intent_parser "Get the latest Q3 earnings report..."
    import sys

    logging.basicConfig(level=logging.INFO)
    query = " ".join(sys.argv[1:]) or "Download the 114GB game installer from example-games.com"
    print(json.dumps(classify_intent(query), indent=2))
