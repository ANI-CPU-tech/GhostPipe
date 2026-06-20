<p align="center">
  <h1 align="center">👻 GhostPipe</h1>
  <p align="center"><strong>Autonomous Headless Ingestion Daemon</strong></p>
  <p align="center"><em>Bridging the gap between websites built for humans and pipelines built for machines.</em></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/Build-Passing-brightgreen?style=flat-square" alt="Build Status" />
  <img src="https://img.shields.io/badge/LLM-Llama%203.1%20%28Groq%29-blueviolet?style=flat-square" alt="LLM" />
  <img src="https://img.shields.io/badge/Engine-Camoufox-orange?style=flat-square" alt="Camoufox" />
</p>

---

GhostPipe is an autonomous, stealthy data-ingestion daemon. You give it a natural-language instruction — like *"Download Pokemon Ruby from this URL"* — and it silently takes care of everything else. It uses a **Groq/Llama 3.1** LLM to parse intent, launches a **Camoufox** (hardened headless Firefox) browser to navigate and bypass anti-bot defenses, intercepts the authenticated download link, and hands it directly to an **aria2c** background daemon for a **16-connection, resumable transfer** — without a single byte of the binary payload passing through Python memory.

---

## Screenshots

<p align="center">
  <img src="assets/asset11.jpeg" alt="GhostPipe Interactive REPL" width="800" />
  <br/>
  <em>Interactive REPL — boot sequence, status bar, and slash command shell</em>
</p>

<p align="center">
  <img src="assets/asset12.jpeg" alt="GhostPipe Download Progress" width="800" />
  <br/>
  <em>Live download progress — rich progress bar with speed, ETA, and aria2c handoff</em>
</p>

<p align="center">
  <img src="assets/asset13.jpeg" alt="GhostPipe RAG Search Results" width="800" />
  <br/>
  <em>RAG semantic search results — ChromaDB query output rendered as a rich table</em>
</p>

---

## Table of Contents

- [Screenshots](#screenshots)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [How It Works (The Pipeline)](#how-it-works-the-pipeline)
- [Configuration Reference](#configuration-reference)
- [License](#license)

---

## Key Features

**Camoufox Stealth Engine**
Uses a heavily customized headless Firefox Playwright driver with C++-level patches that natively bypass WebGL fingerprinting, Canvas fingerprinting, and User-Agent sniffing. The old `playwright-stealth` JS layer has been replaced entirely by Camoufox's native engine to eliminate detection conflicts.

**LLM DOM Vision**
Takes compressed JSON snapshots of up to 20 visible, interactive DOM elements (trimmed to save tokens) and sends them to **Groq (Llama 3.1-8b-instant)** to intelligently navigate cookie banners, invisible pop-ups, login walls, and fake download links — with zero hardcoded selectors.

**The Zero-Trust Firewall**
Built-in Python and JS shields absorb poisoned Cloudflare `pageError` CDP traps that would normally crash the Node.js browser layer. The DOM snapshot evaluator has a 5-second asyncio timeout, and the Turnstile auto-sniper polls for `"Just a moment..."` page titles and checkbox iframes, handling both passive spinner and active checkbox Turnstile variants automatically.

**Out-of-Band Binary Transfers**
The Python script **never touches the binary payload**. The browser layer resolves the authenticated URL and harvests session cookies from the Playwright context, then passes both to an internal `aria2p` RPC wrapper. `aria2c` executes the actual download as a background daemon with **16 parallel connections** (`-x16 -s16`) and full resume support.

**Interactive REPL + Single-Shot CLI**
Launch with no arguments for a persistent conversational shell with slash commands (`/search`, `/downloads`, `/help`), or pass a one-liner for fully automated, non-interactive execution. A `rich`-powered progress bar renders live transfer speed, ETA, and completion in the terminal.

**RAG Memory Integration**
A full text-ingestion pipeline built on `trafilatura`, `sentence-transformers`, and **ChromaDB** stores page content as vector embeddings for local semantic search. Query your ingested pages with `/search <query>` or `--search` at any time.

**Three Intelligent Pipelines**
Intent classification routes every request to the correct execution path:
- `binary_pipeline` — resolves and hands off large file downloads to aria2c
- `media_pipeline` — delegates video/audio URLs to yt-dlp
- `text_pipeline` — extracts, chunks, embeds, and stores page content in ChromaDB

---

## Architecture

```
GhostPipe/
│
├── main.py                  # CLI entry point — argument parser, REPL, single-shot mode
├── config.py                # Global config loader (python-dotenv → typed constants)
├── requirements.txt
├── .env.example
│
├── core/
│   ├── orchestrator.py      # Central coordinator — validates config, calls every layer in sequence
│   ├── intent_parser.py     # Groq/Llama 3.1 intent classifier → structured JSON schema
│   └── router.py            # Selects pipeline (binary / media / text) from intent + URL
│
├── browser/
│   ├── navigator.py         # Camoufox async context manager — goto, current_url, page handle
│   ├── stealth_config.py    # Context defaults (UA, viewport, locale, timezone)
│   └── obstacle_handler.py  # LLM-guided obstacle clearing — Turnstile, login walls, cookie banners
│
├── pipelines/
│   ├── binary_pipeline.py   # DOM analysis → intercepts trigger → aria2c handoff
│   ├── media_pipeline.py    # yt-dlp integration for media URLs
│   └── text_pipeline.py     # trafilatura extraction → chunking → ChromaDB storage
│
├── transfer/
│   └── aria2_manager.py     # aria2c subprocess launcher + aria2p RPC client + rich progress UI
│
├── rag/
│   ├── chroma_store.py      # ChromaDB collection wrapper (upsert, query, count)
│   ├── chunker.py           # Text splitter for RAG ingestion
│   └── embedder.py          # sentence-transformers embedding model wrapper
│
├── dashboard/
│   └── app.py               # rich-formatted result display for CLI output
│
└── data/
    ├── downloads/           # Default binary download destination
    └── chroma_db/           # Persistent ChromaDB vector store
```

**Data flow summary:** `main.py` → `orchestrator.py` → `intent_parser.py` (Groq) → `navigator.py` (Camoufox) → `obstacle_handler.py` (Groq) → `router.py` → pipeline → `aria2_manager.py` (aria2c RPC) or `chroma_store.py`.

---

## Prerequisites

Before running GhostPipe, make sure the following are available on your system:

| Dependency | Purpose | Install |
|---|---|---|
| **Python 3.10+** | Runtime | [python.org](https://www.python.org/downloads/) |
| **Node.js 18+** | Required by Playwright's browser binaries | [nodejs.org](https://nodejs.org/) |
| **aria2c** | The actual binary download engine | `sudo apt install aria2` / `brew install aria2` |
| **Groq API Key** | LLM inference (Llama 3.1-8b-instant) | [console.groq.com](https://console.groq.com/) |

> **Note:** `aria2c` must be accessible on your system `PATH`. GhostPipe launches it as a managed subprocess and communicates via its JSON-RPC interface on port `6800`.

---

## Installation & Setup

**1. Clone the repository**

```bash
git clone https://github.com/your-username/GhostPipe.git
cd GhostPipe
```

**2. Create and activate a virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
```

**3. Install Python dependencies**

```bash
pip install -r requirements.txt
```

**4. Configure your environment**

```bash
cp .env.example .env
```

Open `.env` and add your Groq API key:

```dotenv
GROQ_API_KEY=gsk_your_key_here

# Optional overrides (these are the defaults):
# GROQ_MODEL=llama-3.1-8b-instant
# ARIA2_RPC_URL=http://localhost:6800/jsonrpc
# ARIA2_RPC_SECRET=
# DOWNLOAD_DIR=./data/downloads
# CHROMA_DB_PATH=./data/chroma_db
# HEADLESS=true
```

**5. Install the Camoufox / Playwright browser binaries**

```bash
# Install Camoufox (headless Firefox with C++ anti-detect patches)
python -m camoufox fetch

# Install Playwright's Chromium as a fallback engine
playwright install chromium
```

---

## Usage

### Interactive REPL Mode

Launch with no arguments to enter the persistent conversational shell:

```bash
python main.py
```

Inside the REPL, you can use natural language or slash commands:

```
> Download Pokemon Ruby from https://archive.org/download/pokemon-ruby.gba
> /search what was on that page about gen 3 starters
> /downloads
> /help
```

### Single-Shot CLI Mode

Pass a request directly for fully automated, non-interactive execution:

```bash
# Binary download — hands off to aria2c automatically
python main.py "Download Pokemon Ruby from this url https://archive.org/download/pokemon-ruby.gba"

# Run browser in headed mode for debugging
python main.py "Download Pokemon Ruby from https://..." --visible

# With login credentials for gated sites
python main.py "Download the installer from example-games.com" \
  --user "me@example.com" --password "hunter2"

# Override download destination
python main.py "Download GTA V trailer" --dest /tmp/videos

# Ingest a page into RAG memory, then search it immediately
python main.py "Read the Q3 earnings report from apple.com/investor" \
  --search "gross margin"

# Pure semantic search against your local ChromaDB (no browser launched)
python main.py --search "gross margin guidance" --query-only
```

### Full CLI Reference

```
usage: ghostpipe [-h] [--visible] [--user EMAIL] [--password PASSWORD]
                 [--dest DIR] [--search QUERY] [--query-only]
                 [--log-level {DEBUG,INFO,WARNING,ERROR}]
                 [REQUEST]

positional arguments:
  REQUEST               Natural-language request. Omit to start Interactive Mode.

options:
  --visible             Run browser in headed (non-headless) mode
  --user EMAIL          Login username for obstacle handler
  --password PASSWORD   Login password for obstacle handler
  --dest DIR            Override download output directory
  --search QUERY        Semantic search query against ChromaDB
  --query-only          Skip ingestion entirely; only run --search
  --log-level LEVEL     DEBUG | INFO | WARNING | ERROR (default: WARNING)
```

---

## How It Works (The Pipeline)

```
User Request (natural language)
        │
        ▼
┌─────────────────────┐
│   Intent Parser      │  Groq/Llama 3.1 classifies the request into a structured
│   (core/)            │  JSON schema: target_type, target_site, search_hint,
│                      │  filename_hint, confidence score.
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   URL Resolution     │  Hard URL override (regex) → LLM target_site →
│   (orchestrator.py)  │  Google search fallback. Never starts without a target.
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Camoufox Launch    │  A headless Firefox instance with C++-level fingerprint
│   (browser/)         │  patches. Navigates to the resolved URL with stealth
│                      │  context (UA, viewport, locale, timezone).
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Obstacle Clearing  │  Up to 4 LLM-guided rounds. Auto-detects Cloudflare
│   (obstacle_handler) │  Turnstile spinners and checkbox iframes. For everything
│                      │  else, a compressed DOM snapshot (≤20 elements, ≤35 chars
│                      │  each) is sent to Groq, which returns CSS selectors to
│                      │  click or fill. All Playwright calls have asyncio timeouts
│                      │  to absorb Cloudflare CDP traps.
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Pipeline Router    │  Checks intent type + current URL to select execution
│   (core/router.py)   │  path: binary, media, or text/RAG.
└──────────┬──────────┘
           │
     ┌─────┴──────────────────┐
     ▼                        ▼                        ▼
Binary Pipeline         Media Pipeline           Text Pipeline
     │                        │                        │
DOM analysis            yt-dlp call             trafilatura
finds download          with URL or             extraction →
trigger link →          search hint             chunking →
Playwright              directly.               sentence-
intercepts                                      transformers
network                                         embedding →
request →                                       ChromaDB
extracts URL
+ cookies
     │
     ▼
┌─────────────────────┐
│   aria2c Handoff     │  Authenticated URL + harvested session cookies are passed
│   (transfer/)        │  to the aria2p RPC client. aria2c downloads the file with
│                      │  -x16 parallel connections. Python never touches the binary.
│                      │  A rich progress bar renders speed, ETA, and % live.
└─────────────────────┘
```

**The key architectural principle:** GhostPipe's Python process acts purely as a _resolver and coordinator_. For binary downloads, it identifies **what** to download and **how** to authenticate, then immediately delegates the actual transfer to `aria2c`. This means 30GB game files, ROM archives, and installer packages are downloaded at wire speed with full resume support, without loading a single byte into Python memory.

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | _(required)_ | Groq API key for Llama 3.1 inference |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model ID |
| `ARIA2_RPC_URL` | `http://localhost:6800/jsonrpc` | aria2c RPC endpoint |
| `ARIA2_RPC_SECRET` | _(empty)_ | Optional aria2c RPC secret token |
| `DOWNLOAD_DIR` | `./data/downloads` | Binary download destination |
| `CHROMA_DB_PATH` | `./data/chroma_db` | ChromaDB persistence directory |
| `HEADLESS` | `true` | Set to `false` to run browser headed |
| `FLARESOLVERR_URL` | `http://localhost:8191/v1` | Optional FlareSolverr endpoint |

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
