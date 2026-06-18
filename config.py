"""
GhostPipe — Global Configuration

Loads environment variables (from .env) and exposes them as constants
used across the navigation, transfer, and RAG pipelines.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


# --- Groq LLM ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# --- aria2c RPC ---
ARIA2_RPC_URL = os.getenv("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
ARIA2_RPC_SECRET = os.getenv("ARIA2_RPC_SECRET", "")

# --- Storage Paths ---
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./data/downloads")).resolve()
CHROMA_DB_PATH = Path(os.getenv("CHROMA_DB_PATH", "./data/chroma_db")).resolve()

# --- Browser ---
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes")
# --- FlareSolverr ---
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")


def ensure_dirs() -> None:
    """Create required runtime directories if they don't exist."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)


def validate() -> list[str]:
    """Return a list of missing/invalid required settings."""
    errors = []
    if not GROQ_API_KEY:
        errors.append("GROQ_API_KEY is not set (check your .env file)")
    return errors
