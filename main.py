"""
GhostPipe — CLI Entry Point

Usage examples:
    python main.py "Get the Q3 earnings PDF from Apple's investor portal"
    python main.py "Download the 114GB game installer from example-games.com"
    python main.py --visible "Scrape the NeurIPS 2024 schedule"
    python main.py --user me@email.com --password secret "Download from members.site.com"
    python main.py --search "What was Q3 revenue?" --query-only

Options:
    request         Natural-language ingestion/download request (required unless --query-only)
    --visible       Run browser headed (non-headless) — useful for debugging
    --user TEXT     Login username passed to obstacle handler
    --password TEXT Login password passed to obstacle handler
    --dest PATH     Where to save downloaded files (binary pipeline, default: data/downloads)
    --search TEXT   Semantic search query to run against stored ChromaDB
    --query-only    Skip ingestion entirely; just run --search against existing ChromaDB
    --log-level     DEBUG | INFO | WARNING | ERROR (default: WARNING)
"""

import sys
import logging
import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table
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
    p.add_argument(
        "request",
        nargs="?",
        default=None,
        metavar="REQUEST",
        help='Natural-language ingestion request (quote it)',
    )
    p.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in headed mode",
    )
    p.add_argument("--user",     default=None, metavar="EMAIL",    help="Login username")
    p.add_argument("--password", default=None, metavar="PASSWORD", help="Login password")
    p.add_argument(
        "--dest",
        default=None,
        type=Path,
        metavar="DIR",
        help="Download directory (binary pipeline)",
    )
    p.add_argument(
        "--search",
        default=None,
        metavar="QUERY",
        help="Run a semantic search query against ChromaDB after ingest",
    )
    p.add_argument(
        "--query-only",
        action="store_true",
        help="Skip ingestion; search existing ChromaDB with --search",
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        metavar="LEVEL",
        help="Log verbosity: DEBUG | INFO | WARNING | ERROR (default: WARNING)",
    )
    return p


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


# --------------------------------------------------------------------------- #
# Query-only mode (no browser)
# --------------------------------------------------------------------------- #

def _query_only(query: str) -> None:
    """Search existing ChromaDB content without running the browser."""
    from rag.chroma_store import ChromaStore

    store = ChromaStore()
    total = store.count()
    if total == 0:
        console.print("[red]ChromaDB is empty — run an ingestion first.[/]")
        sys.exit(1)

    console.print(f"[dim]Searching {total} chunks…[/]\n")
    results = store.query(query, n_results=5)

    if not results:
        console.print("[yellow]No results found.[/]")
        sys.exit(0)

    t = Table(box=box.ROUNDED, show_lines=True, padding=(0, 1))
    t.add_column("#",      style="dim",   width=3)
    t.add_column("Score",  style="cyan",  width=7)
    t.add_column("Source", style="dim",   width=30)
    t.add_column("Text",   style="white")

    for i, r in enumerate(results, 1):
        src = r.source_url[-28:] if len(r.source_url) > 28 else r.source_url
        t.add_row(str(i), f"{r.score:.3f}", src, r.text[:280].replace("\n", " "))

    console.print(t)


# --------------------------------------------------------------------------- #
# Inline post-ingest search (non-interactive, single query)
# --------------------------------------------------------------------------- #

def _inline_search(query: str, source_url: str | None = None) -> None:
    from rag.chroma_store import ChromaStore

    store = ChromaStore()
    results = store.query(query, n_results=5, source_url=source_url)
    if not results:
        console.print("[yellow]No results for that query.[/]")
        return

    t = Table(box=box.ROUNDED, show_lines=True, padding=(0, 1))
    t.add_column("#",      style="dim",   width=3)
    t.add_column("Score",  style="cyan",  width=7)
    t.add_column("Text",   style="white")
    for i, r in enumerate(results, 1):
        t.add_row(str(i), f"{r.score:.3f}", r.text[:300].replace("\n", " "))
    console.print(t)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    _setup_logging(args.log_level)

    # --- Mode 1: --query-only -------------------------------------------
    if args.query_only:
        if not args.search:
            console.print("[red]--query-only requires --search QUERY[/]")
            sys.exit(1)
        _query_only(args.search)
        return

    # --- Mode 2: Full pipeline ------------------------------------------
    if not args.request:
        parser.print_help()
        sys.exit(0)

    credentials = None
    if args.user or args.password:
        credentials = {
            "username": args.user     or "",
            "password": args.password or "",
        }

    console.print(f"\n[bold cyan]GhostPipe[/]  [dim]{args.request}[/]\n")

    # Lazy import so --help works without installing deps
    from core.orchestrator import run_sync
    from dashboard.app import show_result

    result = run_sync(
        user_request=args.request,
        credentials=credentials,
        dest_dir=args.dest,
        headless=not args.visible,
    )

    show_result(result)

    # Optional inline search after a successful text ingest
    if args.search and result.success and result.pipeline == "text":
        console.rule("[bold]Inline Search Result[/]")
        _inline_search(
            args.search,
            source_url=result.ingest.source_url if result.ingest else None,
        )

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
