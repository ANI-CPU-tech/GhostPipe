"""
Navigator — Playwright session lifecycle and page navigation helpers.

Wraps Playwright's async API behind a small `Navigator` class that:
  - launches a (stealth-patched) Chromium browser
  - opens pages and waits for JS-rendered content to settle
  - exposes cookies/headers/HTML for downstream pipelines
  - cleans up the browser/playwright process on exit

Used as an async context manager:

    async with Navigator() as nav:
        await nav.goto("https://example.com")
        html = await nav.get_html()
        cookies = await nav.get_cookies()
"""

import logging

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

import config
from browser.stealth_config import apply_stealth, context_options

logger = logging.getLogger(__name__)


class Navigator:
    """Manages a single Playwright browser/context/page for one task."""

    def __init__(self, headless: bool | None = None):
        self.headless = config.HEADLESS if headless is None else headless

        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # --- Lifecycle ---------------------------------------------------

    async def start(self) -> "Navigator":
        """Launch the browser, create a stealth context, and open a page."""
        self._playwright = await async_playwright().start()

        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        self.context = await self.browser.new_context(**context_options())
        await apply_stealth(self.context)

        self.page = await self.context.new_page()
        return self

    async def close(self) -> None:
        """Close the browser and stop the Playwright driver."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def __aenter__(self) -> "Navigator":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # --- Navigation ----------------------------------------------------

    async def goto(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        extra_settle_ms: int = 1500,
    ) -> None:
        """
        Navigate to `url` and give JS-rendered content time to settle.

        `wait_until="domcontentloaded"` returns as soon as the DOM is
        ready (faster than waiting on `networkidle`, which can hang on
        pages with long-polling/analytics). The extra `extra_settle_ms`
        sleep gives client-side frameworks a short window to render.
        """
        if not self.page:
            raise RuntimeError("Navigator not started — use 'async with Navigator()'")

        logger.info("Navigating to %s", url)
        await self.page.goto(url, wait_until=wait_until)

        if extra_settle_ms:
            await self.page.wait_for_timeout(extra_settle_ms)

    # --- Content extraction --------------------------------------------

    async def get_html(self) -> str:
        """Return the fully rendered page HTML."""
        if not self.page:
            raise RuntimeError("Navigator not started")
        return await self.page.content()

    async def get_cookies(self) -> list[dict]:
        """Return all cookies for the current context (used for aria2c handoff)."""
        if not self.context:
            raise RuntimeError("Navigator not started")
        return await self.context.cookies()

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        """Save a screenshot — useful for the obstacle handler's LLM input."""
        if not self.page:
            raise RuntimeError("Navigator not started")
        await self.page.screenshot(path=path, full_page=full_page)

    @property
    def current_url(self) -> str | None:
        return self.page.url if self.page else None


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python -m browser.navigator https://example.com
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO)

    async def _demo(url: str):
        async with Navigator() as nav:
            await nav.goto(url)
            html = await nav.get_html()
            print(f"URL: {nav.current_url}")
            print(f"HTML length: {len(html)} chars")
            print(f"Cookies: {len(await nav.get_cookies())}")

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    asyncio.run(_demo(target))
