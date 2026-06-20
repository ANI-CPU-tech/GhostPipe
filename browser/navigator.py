"""
Navigator — browser session lifecycle and page navigation helpers.

Upgraded to Camoufox: Provides the blazing speed of native Playwright 
with C++ level anti-detect stealth to bypass Cloudflare natively.
"""

import logging
from typing import Optional

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Page, Browser

import config

logger = logging.getLogger(__name__)


class Navigator:
    """Manages a single Camoufox Playwright browser instance."""

    def __init__(self, headless: bool | None = None):
        self.headless = config.HEADLESS if headless is None else headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._camoufox_context = None

    # --- Lifecycle ---------------------------------------------------

    async def start(self) -> "Navigator":
        """Launch the stealth browser using Camoufox."""
        logger.info("Starting Camoufox (stealth Playwright)...")
        
        # AsyncCamoufox manages the playwright engine and the browser automatically
        self._camoufox_context = AsyncCamoufox(headless=self.headless)
        self.browser = await self._camoufox_context.__aenter__()
        
        # Camoufox returns a native Playwright Browser object
        self.page = await self.browser.new_page()

        # --- THE FIREWALL ---
        # Intercept and destroy poisoned Turnstile errors before Playwright's Node.js driver reads them and crashes!
        await self.page.add_init_script("""
            window.addEventListener('error', event => {
                if (!event.error || typeof event.error === 'string' || !event.error.stack) {
                    event.stopImmediatePropagation();
                    event.preventDefault();
                }
            }, true);
        """)

        return self

    async def close(self) -> None:
        """Close the browser safely, absorbing upstream driver crashes."""
        try:
            if self.page and not self.page.is_closed():
                await self.page.close()
        except Exception as e:
            logger.debug("Absorbed page close error: %s", e)
            
        try:
            if self._camoufox_context:
                await self._camoufox_context.__aexit__(None, None, None)
        except Exception as e:
            logger.debug("Absorbed context close error: %s", e)

    async def __aenter__(self) -> "Navigator":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # --- Navigation ----------------------------------------------------

    async def goto(
        self,
        url: str,
        wait_until: str = "load",  # Force full network load to avoid Turnstile race conditions
        extra_settle_ms: int = 8000,
    ) -> None:
        if not self.page:
            raise RuntimeError("Navigator not started — use 'async with Navigator()'")

        logger.info("Navigating to %s", url)
        
        try:
            await self.page.goto(url, wait_until=wait_until)
            if extra_settle_ms:
                await self.page.wait_for_timeout(extra_settle_ms)
        except Exception as e:
            # Cloudflare intentionally throws malformed JS to crash headless drivers.
            # We catch it here so the pipeline continues with whatever HTML it successfully rendered.
            logger.warning("Navigation interrupted (anti-bot defense absorbed).")

    # --- Content extraction --------------------------------------------

    async def get_html(self) -> str:
        if not self.page or self.page.is_closed():
            return ""
        try:
            return await self.page.content()
        except Exception:
            return ""

    async def get_cookies(self) -> list[dict]:
        if not self.page or self.page.is_closed():
            return []
        try:
            return await self.page.context.cookies()
        except Exception:
            return []

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        if not self.page or self.page.is_closed():
            return
        try:
            await self.page.screenshot(path=path, full_page=full_page)
        except Exception as e:
            logger.debug("Screenshot failed: %s", e)

    @property
    def current_url(self) -> str | None:
        if not self.page or self.page.is_closed():
            return None
        return self.page.url
