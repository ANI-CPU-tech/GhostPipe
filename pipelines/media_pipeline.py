"""
Media Pipeline — specialized yt-dlp wrapper for complex streaming sites.

Unlike aria2c (which needs a direct binary link), yt-dlp handles DASH 
streams, JavaScript decryption, and audio/video multiplexing automatically. 
We use this for YouTube, Vimeo, Twitter/X, Reddit, etc.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

@dataclass
class MediaResult:
    success: bool
    filepath: str | None = None
    title: str | None = None
    error: str | None = None

def _download_sync(url: str, dest_dir: Path) -> MediaResult:
    """Synchronous yt-dlp call (runs in a background thread)."""
    
    ydl_opts = {
        # Download best mp4 video and best m4a audio and merge them
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(dest_dir / '%(title)s.%(ext)s'),
        'quiet': False,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Extracting media info for: %s", url)
            info = ydl.extract_info(url, download=True)
            
            title = info.get('title', 'Unknown Media')
            filepath = ydl.prepare_filename(info)
            
            logger.info("Media download complete: %s", title)
            return MediaResult(success=True, filepath=filepath, title=title)
            
    except Exception as e:
        logger.error("yt-dlp failed: %s", e)
        return MediaResult(success=False, error=str(e))

async def run(url: str, dest_dir: str | Path | None = None) -> MediaResult:
    """Async entry point for the media pipeline."""
    dest_dir = Path(dest_dir or "./data/downloads")
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("Media pipeline engaged. Handing off to yt-dlp...")
    # yt-dlp is synchronous, so we offload it to an asyncio thread
    return await asyncio.to_thread(_download_sync, url, dest_dir)
