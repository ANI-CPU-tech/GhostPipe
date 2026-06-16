"""
Chunker — splits clean text/Markdown into embedding-sized passages.

Strategy: sentence-aware sliding window.
  - Split on sentence boundaries (double-newlines, then ". ")
  - Accumulate sentences into chunks of ~CHUNK_SIZE tokens (approximated
    as chars / 4, good enough for embedding purposes)
  - Overlap between chunks keeps context across boundaries

This keeps chunks semantically coherent (no mid-sentence splits) while
staying well within typical embedding model limits (512 tokens).
"""

from dataclasses import dataclass, field
import re

CHUNK_SIZE_CHARS  = 1200   # ~300 tokens — safe for most embedding models
CHUNK_OVERLAP_CHARS = 200  # overlap to preserve cross-boundary context
MIN_CHUNK_CHARS   = 80     # discard tiny fragments (nav leftovers, etc.)


@dataclass
class Chunk:
    index: int
    text: str
    char_start: int
    char_end: int
    metadata: dict = field(default_factory=dict)


def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentence-ish units.
    Prefers paragraph breaks, then sentence-ending punctuation.
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split on paragraph breaks first
    paragraphs = re.split(r"\n{2,}", text)

    sentences = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Further split on sentence endings inside a paragraph
        parts = re.split(r"(?<=[.!?])\s+", para)
        sentences.extend(p.strip() for p in parts if p.strip())

    return sentences


def chunk_text(
    text: str,
    source_url: str = "",
    chunk_size: int = CHUNK_SIZE_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
    min_size: int = MIN_CHUNK_CHARS,
) -> list[Chunk]:
    """
    Split `text` into overlapping chunks suitable for embedding.

    Args:
        text:       Clean text to chunk (output of Trafilatura).
        source_url: URL the text came from — stored in chunk metadata.
        chunk_size: Target max chars per chunk.
        overlap:    How many chars of the previous chunk to carry over.
        min_size:   Minimum chars — shorter chunks are discarded.

    Returns:
        List of Chunk objects in order.
    """
    if not text or not text.strip():
        return []

    sentences = _split_sentences(text)
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0
    char_cursor = 0
    chunk_index = 0

    for sentence in sentences:
        s_len = len(sentence)

        # If adding this sentence exceeds the limit, flush current buffer
        if current_len + s_len > chunk_size and current_parts:
            chunk_text_str = " ".join(current_parts)
            if len(chunk_text_str) >= min_size:
                chunks.append(Chunk(
                    index=chunk_index,
                    text=chunk_text_str,
                    char_start=char_cursor,
                    char_end=char_cursor + len(chunk_text_str),
                    metadata={"source_url": source_url, "chunk_index": chunk_index},
                ))
                chunk_index += 1

            # Keep overlap: retain trailing sentences that fit in `overlap` chars
            overlap_parts: list[str] = []
            overlap_len = 0
            for part in reversed(current_parts):
                if overlap_len + len(part) <= overlap:
                    overlap_parts.insert(0, part)
                    overlap_len += len(part)
                else:
                    break

            char_cursor += current_len - overlap_len
            current_parts = overlap_parts
            current_len = overlap_len

        current_parts.append(sentence)
        current_len += s_len

    # Flush remaining content
    if current_parts:
        chunk_text_str = " ".join(current_parts)
        if len(chunk_text_str) >= min_size:
            chunks.append(Chunk(
                index=chunk_index,
                text=chunk_text_str,
                char_start=char_cursor,
                char_end=char_cursor + len(chunk_text_str),
                metadata={"source_url": source_url, "chunk_index": chunk_index},
            ))

    return chunks
