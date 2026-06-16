"""
Chroma Store — local/persistent ChromaDB collection management.

Wraps ChromaDB behind a simple API:
  - store(chunks)          → embed and persist a list of Chunk objects
  - query(text, n)         → semantic search, returns top-n results
  - list_sources()         → what URLs have been ingested?
  - delete_source(url)     → remove all chunks from a given URL
  - reset()                → wipe the entire collection (dev/demo use)

The collection is persisted to disk at config.CHROMA_DB_PATH, so ingested
content survives between GhostPipe runs.
"""

import logging
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings

import config
from rag.chunker import Chunk
from rag.embedder import get_embedding_function

logger = logging.getLogger(__name__)

COLLECTION_NAME = "ghostpipe"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class QueryResult:
    chunk_index: int
    text: str
    source_url: str
    distance: float       # lower = more similar (L2 distance from ChromaDB)
    score: float          # 1 - normalised distance, higher = more relevant


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

class ChromaStore:
    """
    Manages a single persistent ChromaDB collection for GhostPipe.

    Usage:
        store = ChromaStore()
        store.store(chunks)
        results = store.query("What was Q3 revenue?", n_results=5)
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str = COLLECTION_NAME,
    ):
        self._persist_dir = str(persist_dir or config.CHROMA_DB_PATH)
        self._collection_name = collection_name
        self._client: chromadb.ClientAPI | None = None
        self._collection = None
        self._ef = get_embedding_function()

    def _ensure_connected(self) -> None:
        """Lazy-connect to the local ChromaDB instance."""
        if self._client is not None:
            return

        config.ensure_dirs()
        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self._ef,
            metadata={"hnsw:space": "l2"},
        )
        logger.info(
            "ChromaDB connected — collection '%s' has %d chunks",
            self._collection_name,
            self._collection.count(),
        )

    # --- Write ----------------------------------------------------------

    def store(self, chunks: list[Chunk]) -> int:
        """
        Embed and persist a list of Chunk objects.

        Chunks are de-duplicated by ID (source_url + chunk_index) so
        re-ingesting the same page is safe — existing chunks are overwritten.

        Returns the number of chunks stored.
        """
        self._ensure_connected()

        if not chunks:
            logger.warning("store() called with empty chunk list")
            return 0

        ids        = [f"{c.metadata.get('source_url','')}::chunk::{c.index}" for c in chunks]
        documents  = [c.text for c in chunks]
        metadatas  = [c.metadata for c in chunks]

        # upsert = insert or overwrite
        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Stored %d chunks in ChromaDB", len(chunks))
        return len(chunks)

    # --- Query ----------------------------------------------------------

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        source_url: str | None = None,
    ) -> list[QueryResult]:
        """
        Semantic search over ingested chunks.

        Args:
            query_text: Natural-language query.
            n_results:  How many chunks to return.
            source_url: If set, restrict results to chunks from this URL.

        Returns:
            List of QueryResult sorted by relevance (most relevant first).
        """
        self._ensure_connected()

        total = self._collection.count()
        if total == 0:
            logger.warning("ChromaDB collection is empty — nothing to query")
            return []

        n_results = min(n_results, total)

        kwargs: dict = {
            "query_texts": [query_text],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if source_url:
            kwargs["where"] = {"source_url": source_url}

        raw = self._collection.query(**kwargs)

        results = []
        docs       = raw["documents"][0]
        metas      = raw["metadatas"][0]
        distances  = raw["distances"][0]

        # Normalise distances to a 0-1 score (works for L2 space)
        max_dist = max(distances) if distances else 1.0
        for doc, meta, dist in zip(docs, metas, distances):
            score = 1.0 - (dist / max_dist) if max_dist > 0 else 1.0
            results.append(QueryResult(
                chunk_index=meta.get("chunk_index", -1),
                text=doc,
                source_url=meta.get("source_url", ""),
                distance=dist,
                score=round(score, 4),
            ))

        return results

    # --- Inspection / management ----------------------------------------

    def list_sources(self) -> list[str]:
        """Return a deduplicated list of ingested source URLs."""
        self._ensure_connected()
        if self._collection.count() == 0:
            return []
        # Fetch all metadatas (no filter, limited fields)
        raw = self._collection.get(include=["metadatas"])
        urls = {m.get("source_url", "") for m in raw["metadatas"] if m.get("source_url")}
        return sorted(urls)

    def delete_source(self, source_url: str) -> int:
        """Remove all chunks from a given source URL. Returns count deleted."""
        self._ensure_connected()
        raw = self._collection.get(
            where={"source_url": source_url},
            include=["metadatas"],
        )
        ids = raw["ids"]
        if ids:
            self._collection.delete(ids=ids)
            logger.info("Deleted %d chunks for source: %s", len(ids), source_url)
        return len(ids)

    def reset(self) -> None:
        """Wipe the entire collection. Use with care (demo/dev only)."""
        self._ensure_connected()
        self._client.delete_collection(self._collection_name)
        self._collection = None
        self._client = None
        logger.warning("ChromaDB collection '%s' wiped", self._collection_name)

    def count(self) -> int:
        self._ensure_connected()
        return self._collection.count()
