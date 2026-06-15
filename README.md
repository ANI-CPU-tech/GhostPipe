
# GhostPipe

> Autonomous Headless Ingestion Daemon — bridging the gap between websites
> built for humans and pipelines built for machines.

## Status
🚧 Hackathon work-in-progress. See `GhostPipe_Project_Documentation.pdf` for
full architecture and design rationale.

## Setup
```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in GROQ_API_KEY
```

## Run
```bash
python main.py "your natural language request"
```

## Architecture
- `core/` — intent parsing, routing, orchestration
- `browser/` — Playwright navigation + stealth + obstacle handling
- `pipelines/` — binary (aria2c) and text/RAG branches
- `transfer/` — aria2c RPC manager
- `rag/` — chunking, embedding, ChromaDB storage
- `dashboard/` — progress display
