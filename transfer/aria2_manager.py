"""
aria2 Manager — aria2p RPC wrapper for multi-connection, resumable downloads.

aria2c is the actual transfer engine. It is launched as a background
subprocess with RPC enabled. GhostPipe's browser layer resolves the
authenticated URL + session cookies, then this module hands them to
aria2c which does the actual bytes-on-disk work.

The data NEVER passes through the Python process — this is the key
architectural separation described in the project docs.

Usage:
    async with Aria2Manager() as mgr:
        gid = await mgr.add_download(
            url="https://...",
            cookies=cookies_list,          # from Playwright context
            headers={"Referer": "..."},
            dest_dir="/data/downloads",
            filename="installer.exe",
        )
        async for progress in mgr.watch(gid):
            print(progress)
"""

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import aria2p
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)

import config

logger = logging.getLogger(__name__)

ARIA2C_DEFAULT_CONNECTIONS = 16     # -x16 as per project spec
ARIA2C_POLL_INTERVAL       = 1.0    # seconds between progress polls
ARIA2C_STARTUP_WAIT        = 2.0    # seconds to wait for aria2c RPC to come up


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class DownloadProgress:
    gid: str
    status: str                  # "active" | "complete" | "error" | "paused"
    filename: str
    total_bytes: int
    completed_bytes: int
    speed_bps: int
    eta_seconds: int | None
    percent: float


@dataclass
class DownloadResult:
    success: bool
    gid: str
    filepath: Path | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Cookie / header helpers
# --------------------------------------------------------------------------- #

def _cookies_to_header(cookies: list[dict]) -> str:
    """
    Convert a Playwright cookies list to a Cookie: header string for aria2c.
    Playwright cookie dicts have at least {"name": ..., "value": ...}.
    """
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def _build_aria2_headers(
    cookies: list[dict] | None,
    extra_headers: dict | None,
) -> list[str]:
    """
    Build the list of --header=... strings aria2p expects.
    aria2p passes these through as individual HTTP headers.
    """
    headers = []
    if cookies:
        cookie_str = _cookies_to_header(cookies)
        if cookie_str:
            headers.append(f"Cookie: {cookie_str}")
    if extra_headers:
        for k, v in extra_headers.items():
            headers.append(f"{k}: {v}")
    return headers


# --------------------------------------------------------------------------- #
# aria2c subprocess launcher
# --------------------------------------------------------------------------- #

def _build_aria2c_cmd(
    rpc_secret: str = "",
    rpc_port: int = 6800,
) -> list[str]:
    cmd = [
        "aria2c",
        "--enable-rpc",
        "--rpc-listen-all=false",
        f"--rpc-listen-port={rpc_port}",
        "--rpc-allow-origin-all=true",
        "--daemon=false",          # we manage the process ourselves
        "--continue=true",         # resume partial downloads
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
    ]
    if rpc_secret:
        cmd.append(f"--rpc-secret={rpc_secret}")
    return cmd


# --------------------------------------------------------------------------- #
# Manager class
# --------------------------------------------------------------------------- #

class Aria2Manager:
    """
    Manages the aria2c subprocess and exposes async download + progress APIs.

    Can be used in two modes:
      1. Subprocess mode (default): GhostPipe launches aria2c itself.
      2. External mode: aria2c is already running (pass manage_process=False).
    """

    def __init__(self, manage_process: bool = True):
        self.manage_process = manage_process
        self._proc: subprocess.Popen | None = None
        self._api: aria2p.API | None = None

    # --- Lifecycle ------------------------------------------------------

    async def start(self) -> "Aria2Manager":
        if self.manage_process:
            await self._launch_aria2c()
        self._connect()
        return self

    async def _launch_aria2c(self) -> None:
        cmd = _build_aria2c_cmd(
            rpc_secret=config.ARIA2_RPC_SECRET,
        )
        logger.info("Launching aria2c: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give RPC server time to come up
        await asyncio.sleep(ARIA2C_STARTUP_WAIT)

    def _connect(self) -> None:
        """Connect aria2p client to the RPC endpoint."""
        # Parse host/port from config URL: http://localhost:6800/jsonrpc
        url = config.ARIA2_RPC_URL
        host = "localhost"
        port = 6800
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or host
            port = parsed.port or port
        except Exception:
            pass

        self._api = aria2p.API(
            aria2p.Client(
                host=f"http://{host}",
                port=port,
                secret=config.ARIA2_RPC_SECRET,
            )
        )
        logger.info("Connected to aria2c RPC at %s:%s", host, port)

    async def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
            logger.info("aria2c process stopped")

    async def __aenter__(self) -> "Aria2Manager":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    # --- Download API ---------------------------------------------------

    async def add_download(
        self,
        url: str,
        cookies: list[dict] | None = None,
        headers: dict | None = None,
        dest_dir: str | Path | None = None,
        filename: str | None = None,
        connections: int = ARIA2C_DEFAULT_CONNECTIONS,
    ) -> str:
        """
        Queue a download in aria2c.

        Args:
            url:         Authenticated download URL resolved by the browser layer.
            cookies:     Playwright cookie list from context.cookies().
            headers:     Extra HTTP headers (Referer, Authorization, etc.).
            dest_dir:    Directory to save file into (defaults to config.DOWNLOAD_DIR).
            filename:    Override filename. If None, aria2c infers from URL/headers.
            connections: Parallel connections (-x). Defaults to 16.

        Returns:
            GID string — aria2c's identifier for this download.
        """
        if not self._api:
            raise RuntimeError("Aria2Manager not started")

        dest_dir = Path(dest_dir or config.DOWNLOAD_DIR)
        dest_dir.mkdir(parents=True, exist_ok=True)

        aria2_headers = _build_aria2_headers(cookies, headers)

        options: dict = {
            "dir": str(dest_dir),
            "max-connection-per-server": str(connections),
            "split": str(connections),
            "continue": "true",
            "auto-file-renaming": "false",
        }
        if filename:
            options["out"] = filename
        if aria2_headers:
            # aria2p accepts header as a list of strings
            options["header"] = aria2_headers

        download = self._api.add_uris([url], options=options)
        gid = download.gid
        logger.info("Queued download GID=%s  url=%s", gid, url)
        return gid

    async def watch(
        self,
        gid: str,
        poll_interval: float = ARIA2C_POLL_INTERVAL,
    ):
        """
        Async generator that yields DownloadProgress every `poll_interval` seconds
        until the download completes or errors.
        """
        if not self._api:
            raise RuntimeError("Aria2Manager not started")

        while True:
            await asyncio.sleep(poll_interval)
            try:
                dl = self._api.get_download(gid)
            except Exception as e:
                logger.error("aria2c RPC error while watching %s: %s", gid, e)
                break

            total     = dl.total_length or 0
            completed = dl.completed_length or 0
            percent   = (completed / total * 100) if total else 0.0

            eta: int | None = None
            speed = dl.download_speed or 0
            if speed > 0 and total > completed:
                eta = int((total - completed) / speed)

            progress = DownloadProgress(
                gid=gid,
                status=dl.status,
                filename=dl.name or "",
                total_bytes=total,
                completed_bytes=completed,
                speed_bps=speed,
                eta_seconds=eta,
                percent=percent,
            )
            yield progress

            if dl.status in ("complete", "error", "removed"):
                break

    async def wait_for_completion(self, gid: str) -> DownloadResult:
        """
        Block until the download is complete or errors. Returns DownloadResult.
        Uses rich.progress to render a sleek, self-updating progress bar.
        """
        result_path: Path | None = None
        
        # Build the Rich Progress UI
        with Progress(
            TextColumn("[bold cyan]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None, complete_style="green", finished_style="bold green"),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            expand=True,
            transient=False  # Keep the bar visible when finished
        ) as progress_ui:
            
            task_id = progress_ui.add_task("Downloading...", total=100, filename="Connecting...")

            async for p in self.watch(gid):
                display_name = p.filename if p.filename else "Downloading..."
                # Truncate really long filenames for the UI
                if len(display_name) > 30:
                    display_name = display_name[:27] + "..."

                # Update the progress bar in place!
                progress_ui.update(
                    task_id,
                    total=p.total_bytes,
                    completed=p.completed_bytes,
                    filename=display_name
                )

                if p.status == "complete":
                    dl = self._api.get_download(gid)
                    files = dl.files
                    if files:
                        result_path = Path(files[0].path)
                    return DownloadResult(success=True, gid=gid, filepath=result_path)

                if p.status == "error":
                    dl = self._api.get_download(gid)
                    err = getattr(dl, "error_message", "Unknown aria2c error")
                    return DownloadResult(success=False, gid=gid, error=err)

        return DownloadResult(success=False, gid=gid, error="Download ended unexpectedly")
