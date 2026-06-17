"""
GhostPipe — CLI Entry Point

Usage examples:
    python main.py  (Starts Interactive REPL Mode)
    python main.py "Download the 114GB game installer from example-games.com"
    python main.py --visible "Scrape the NeurIPS 2024 schedule"
    python main.py --search "What was Q3 revenue?" --query-only

Options:
    request         Natural-language request. If omitted, starts Interactive Mode!
    --visible       Run browser headed (non-headless) — useful for debugging
    --user TEXT     Login username passed to obstacle handler
    --password TEXT Login password passed to obstacle handler
    --dest PATH     Where to save downloaded files
    --search TEXT   Semantic search query to run against stored ChromaDB
    --query-only    Skip ingestion entirely; just run --search
    --log-level     DEBUG | INFO | WARNING | ERROR (default: WARNING)
"""

import sys
import logging
import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich import box

console = Console()

# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostpipe",
        description="GhostPipe — Autonomous Headless Ingestion Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("request", nargs="?", default=None, metavar="REQUEST", help='Natural-language request (omit to start interactive mode)')
    p.add_argument("--visible", action="store_true", help="Run browser in headed mode")
    p.add_argument("--user", default=None, metavar="EMAIL", help="Login username")
    p.add_argument("--password", default=None, metavar="PASSWORD", help="Login password")
    p.add_argument("--dest", default=None, type=Path, metavar="DIR", help="Download directory")
    p.add_argument("--search", default=None, metavar="QUERY", help="Semantic search query")
    p.add_argument("--query-only", action="store_true", help="Skip ingestion; search ChromaDB")
    p.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log verbosity")
    return p

def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

# --------------------------------------------------------------------------- #
# Search Helper
# --------------------------------------------------------------------------- #

def _run_search(query: str, require_results: bool = False) -> None:
    """Helper to query ChromaDB and print a formatted table."""
    from rag.chroma_store import ChromaStore
    store = ChromaStore()
    
    if store.count() == 0:
        console.print("[red]ChromaDB is empty — run an ingestion first.[/]")
        if require_results: sys.exit(1)
        return

    results = store.query(query, n_results=5)
    if not results:
        console.print("[yellow]No results found.[/]")
        if require_results: sys.exit(0)
        return

    t = Table(box=box.ROUNDED, show_lines=True, padding=(0, 1))
    t.add_column("#", style="dim", width=3)
    t.add_column("Score", style="cyan", width=7)
    t.add_column("Source", style="dim", width=30)
    t.add_column("Text", style="white")

    for i, r in enumerate(results, 1):
        src = r.source_url[-28:] if len(r.source_url) > 28 else r.source_url
        t.add_row(str(i), f"{r.score:.3f}", src, r.text[:280].replace("\n", " "))
    console.print(t)

# --------------------------------------------------------------------------- #
# Interactive REPL Mode
# --------------------------------------------------------------------------- #

def interactive_mode(credentials: dict | None, dest_dir: Path | None, headless: bool) -> None:
    """The persistent, conversational CLI loop."""
    from core.orchestrator import run_sync
    from dashboard.app import show_result

    console.print(Panel(
        "[bold cyan]GhostPipe Interactive Session[/]\n"
        "[dim]The autonomous 3-headed ingestion daemon is online.[/]\n\n"
        "[white]Commands:[/]\n"
        "  [bold green]<your prompt>[/]   → Start an ingestion or download task\n"
        "  [bold blue]/search <query>[/] → Semantic search across your local RAG memory\n"
        "  [bold red]/q[/]               → Exit GhostPipe",
        title="👻 Welcome to GhostPipe", border_style="cyan", padding=(1, 2)
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]GhostPipe >[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Force quitting GhostPipe...[/]")
            break

        if not user_input:
            continue

        lowered = user_input.lower()
        if lowered in ("/q", "/quit", "exit", "quit"):
            console.print("[dim]Shutting down GhostPipe... Goodbye![/]")
            break

        if lowered.startswith("/search"):
            query = user_input[7:].strip()
            if not query:
                console.print("[yellow]Usage: /search <your question>[/]")
            else:
                _run_search(query)
            continue

        # If it's not a command, treat it as a pipeline request!
        result = run_sync(
            user_request=user_input,
            credentials=credentials,
            dest_dir=dest_dir,
            headless=headless,
        )
        
        show_result(result)

# --------------------------------------------------------------------------- #
# Main Execution
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    _setup_logging(args.log_level)

    # 1. Query-only mode
    if args.query_only:
        if not args.search:
            console.print("[red]--query-only requires --search QUERY[/]")
            sys.exit(1)
        _run_search(args.search, require_results=True)
        return

    credentials = None
    if args.user or args.password:
        credentials = {"username": args.user or "", "password": args.password or ""}

    # 2. Interactive Mode (Triggered if no prompt is passed)
    if not args.request:
        interactive_mode(credentials, args.dest, not args.visible)
        return

    # 3. Single-Shot Mode (Traditional CLI)
    console.print(f"\n[bold cyan]GhostPipe[/]  [dim]{args.request}[/]\n")
    from core.orchestrator import run_sync
    from dashboard.app import show_result

    result = run_sync(
        user_request=args.request,
        credentials=credentials,
        dest_dir=args.dest,
        headless=not args.visible,
    )
    show_result(result)
    
    if args.search and result.success and result.pipeline == "text":
        console.rule("[bold]Inline Search Result[/]")
        _run_search(args.search)

    sys.exit(0 if result.success else 1)

if __name__ == "__main__":
    main()
