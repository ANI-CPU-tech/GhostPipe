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

        Usage:
            async for progress in mgr.watch(gid):
                print(f"{progress.percent:.1f}% @ {progress.speed_bps/1e6:.1f} MB/s")
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
        Logs progress every poll interval.
        """
        result_path: Path | None = None
        last_status = ""

        async for progress in self.watch(gid):
            if progress.status != last_status or progress.status == "active":
                speed_mb = progress.speed_bps / 1_000_000
                total_mb = progress.total_bytes / 1_000_000
                logger.info(
                    "GID=%s  %.1f%%  %.1f/%.1f MB  %.2f MB/s  ETA=%ss",
                    gid,
                    progress.percent,
                    progress.completed_bytes / 1_000_000,
                    total_mb,
                    speed_mb,
                    progress.eta_seconds,
                )
                last_status = progress.status

            if progress.status == "complete":
                dl = self._api.get_download(gid)
                files = dl.files
                if files:
                    result_path = Path(files[0].path)
                return DownloadResult(success=True, gid=gid, filepath=result_path)

            if progress.status == "error":
                dl = self._api.get_download(gid)
                err = getattr(dl, "error_message", "Unknown aria2c error")
                return DownloadResult(success=False, gid=gid, error=err)

        return DownloadResult(success=False, gid=gid, error="Download ended unexpectedly")

if __name__ == "__main__":
    import asyncio
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def _test_aria():
        print("🚀 Starting Aria2c Dump Truck...")
        # Using the official Python CDN (globally cached, highly reliable 25MB file)
        test_url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        
        async with Aria2Manager() as mgr:
            print("Queueing download...")
            gid = await mgr.add_download(
                url=test_url, 
                dest_dir="./downloads", 
                filename="python_test.exe"
            )
            print(f"Download started! GID: {gid}")
            
            result = await mgr.wait_for_completion(gid)
            
            print("\n--- Final Result ---")
            print(f"Success: {result.success}")
            if result.success:
                print(f"Saved to: {result.filepath}")
            else:
                print(f"Error Message: {result.error}")

    asyncio.run(_test_aria())
