"""
Dashboard — Rich-based CLI display for GhostPipe.

Renders three phases:
  1. Intent panel   — what GhostPipe understood from the request
  2. Obstacle panel — what login walls / gates it encountered
  3. Result panel   — download complete (binary), media complete (media), or ingest summary (text)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

if TYPE_CHECKING:
    from core.orchestrator import GhostPipeResult

console = Console()
GHOST = "[bold cyan]G[/][cyan]host[/][bold white]Pipe[/]"

# --------------------------------------------------------------------------- #
# Phase 1 — Intent
# --------------------------------------------------------------------------- #

def show_intent(intent: dict) -> None:
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("k", style="dim",   width=16)
    t.add_column("v", style="white")

    ptype = intent.get("target_type", "?").upper()
    colour = "cyan" if ptype == "TEXT" else "magenta"
    t.add_row("Pipeline",    f"[bold {colour}]{ptype}[/]")
    t.add_row("Goal",        intent.get("description") or "—")
    t.add_row("Target site", intent.get("target_site")  or "—")
    t.add_row("Search hint", intent.get("search_hint")  or "—")
    t.add_row("Filename",    intent.get("filename_hint") or "—")
    t.add_row("Confidence",  f"{float(intent.get('confidence', 0)):.0%}")

    console.print(Panel(t, title="[bold]Intent[/]", border_style="blue"))

# --------------------------------------------------------------------------- #
# Phase 2 — Obstacle handling
# --------------------------------------------------------------------------- #

def show_obstacle_result(obstacle) -> None:
    if obstacle is None:
        return

    status  = "[green]✓ Cleared[/]" if obstacle.cleared else "[yellow]⚠ Not cleared[/]"
    actions = obstacle.actions_taken or []

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("k", style="dim",   width=16)
    t.add_column("v", style="white")

    t.add_row("Status",  status)
    t.add_row("Actions", str(len(actions)))

    if actions:
        steps = ", ".join(
            f"[cyan]{a.action}[/]({a.selector or '—'})"
            for a in actions[:6]
        )
        t.add_row("Steps", steps)

    if obstacle.error:
        t.add_row("Note", f"[yellow]{obstacle.error}[/]")

    console.print(Panel(t, title="[bold]Obstacle Handling[/]", border_style="yellow"))

# --------------------------------------------------------------------------- #
# Phase 3a — Binary download result
# --------------------------------------------------------------------------- #

def show_download_result(dl) -> None:
    if dl.success:
        size_str = ""
        if dl.filepath and Path(dl.filepath).exists():
            mb = Path(dl.filepath).stat().st_size / 1_000_000
            size_str = f"  [dim]({mb:.1f} MB)[/]"

        console.print(Panel(
            f"[green bold]✓ Download complete[/]{size_str}\n"
            f"[dim]GID :[/]  {dl.gid}\n"
            f"[dim]File:[/]  {dl.filepath}",
            title="[bold]Binary Pipeline[/]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red bold]✗ Download failed[/]\n"
            f"[dim]GID  :[/] {dl.gid or '—'}\n"
            f"[dim]Error:[/] {dl.error or 'Unknown error'}",
            title="[bold]Binary Pipeline[/]",
            border_style="red",
        ))

# --------------------------------------------------------------------------- #
# Phase 3b — Text / RAG ingest result
# --------------------------------------------------------------------------- #

def show_ingest_result(ingest) -> None:
    if ingest.success:
        samples = ""
        if ingest.sample_chunks:
            lines = ["\n[dim]Sample chunks:[/]"]
            for i, c in enumerate(ingest.sample_chunks, 1):
                preview = c.text[:120].replace("\n", " ")
                lines.append(f"  [cyan]{i}.[/] {preview}…")
            samples = "\n".join(lines)

        console.print(Panel(
            f"[green bold]✓ Ingestion complete[/]\n"
            f"[dim]Source:[/] {ingest.source_url}\n"
            f"[dim]Chunks:[/] {ingest.chunks_stored}\n"
            f"[dim]Chars: [/] {ingest.char_count:,}"
            + samples,
            title="[bold]Text / RAG Pipeline[/]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red bold]✗ Ingestion failed[/]\n"
            f"[dim]Source:[/] {ingest.source_url}\n"
            f"[dim]Error :[/] {ingest.error or 'Unknown error'}",
            title="[bold]Text / RAG Pipeline[/]",
            border_style="red",
        ))

# --------------------------------------------------------------------------- #
# Phase 3c — Media download result
# --------------------------------------------------------------------------- #

def show_media_result(media) -> None:
    if media.success:
        console.print(Panel(
            f"[green bold]✓ Media Extraction Complete[/]\n"
            f"[dim]Title:[/] {media.title}\n"
            f"[dim]File :[/] {media.filepath}",
            title="[bold]Media Pipeline (yt-dlp)[/]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red bold]✗ Media Extraction Failed[/]\n"
            f"[dim]Error:[/] {media.error or 'Unknown error'}",
            title="[bold]Media Pipeline[/]",
            border_style="red",
        ))

# --------------------------------------------------------------------------- #
# Top-level renderer
# --------------------------------------------------------------------------- #

def show_result(result: GhostPipeResult) -> None:
    """Render the full GhostPipeResult returned by core.orchestrator.run_sync()."""
    console.print(Rule(f" {GHOST} "))

    if result.intent:
        show_intent(result.intent)

    if result.obstacle:
        show_obstacle_result(result.obstacle)

    if result.pipeline == "binary" and result.download:
        show_download_result(result.download)

    elif result.pipeline == "media" and result.media:
        show_media_result(result.media)

    elif result.pipeline == "text" and result.ingest:
        show_ingest_result(result.ingest)
        # Note: Interactive search is now handled in the main.py REPL loop!

    elif result.error and not result.intent:
        console.print(Panel(
            f"[red bold]✗ Startup error[/]\n{result.error}",
            border_style="red",
        ))

    console.print(Rule())
