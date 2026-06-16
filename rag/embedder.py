"""
Embedder — generates embeddings for text chunks using ChromaDB's
built-in embedding function.

We deliberately use ChromaDB's default embedding (sentence-transformers
all-MiniLM-L6-v2) rather than a separate model class. Reasons:
  - Zero extra setup for a hackathon — ChromaDB downloads the model
    automatically on first use.
  - Consistent: the same model is used at write AND query time, so
    similarity scores are always comparable.
  - Can be swapped for OpenAI/Cohere embeddings later by changing
    DEFAULT_EF below.

If you want to use a different model, replace DEFAULT_EF with e.g.:
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
    DEFAULT_EF = OpenAIEmbeddingFunction(api_key=..., model_name=...)
"""

import logging

from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)

# sentence-transformers/all-MiniLM-L6-v2 — 384-dim, fast, free, runs locally
DEFAULT_MODEL = "all-MiniLM-L6-v2"


def get_embedding_function(model_name: str = DEFAULT_MODEL):
    """
    Return a ChromaDB-compatible embedding function.

    ChromaDB passes this directly into collection.add() and collection.query(),
    so embeddings are generated automatically — you never call this manually.

    First call will download the model (~80MB) if not already cached.
    """
    logger.info("Using embedding model: %s", model_name)
    return SentenceTransformerEmbeddingFunction(model_name=model_name)
