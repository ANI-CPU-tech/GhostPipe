"""
Navigator — nodriver session lifecycle and page navigation helpers.

Replaced Playwright with `nodriver` to provide undetectable Chrome automation!
This file acts as a 'Shim' (a translation layer) so that your other files 
(like binary_pipeline.py and obstacle_handler.py) still THINK they are talking 
to Playwright, but all the actions are secretly routed through nodriver's 
stealth CDP engine.
"""

import logging
import asyncio
import re

import nodriver as uc
import nodriver.cdp.network as network
import aiohttp

import config

logger = logging.getLogger(__name__)


class PlaywrightPageShim:
    """
    Translation layer between Playwright API and nodriver Tab API.
    Prevents you from having to rewrite the entire GhostPipe codebase!
    """
    def __init__(self, browser: uc.Browser, tab: uc.Tab):
        self.browser = browser
        self.tab = tab
        self._request_handlers = []

        # Intercept network requests for the Binary Pipeline
        self.tab.add_handler(network.RequestWillBeSent, self._handle_network_request)

    async def _handle_network_request(self, event: network.RequestWillBeSent):
        # Fake a Playwright Request object
        class FakeRequest:
            url = event.request.url
            resource_type = "fetch"  # Approximation

        for handler in self._request_handlers:
            await handler(FakeRequest())

    @property
    def url(self) -> str:
        return self.tab.target.url

    async def content(self) -> str:
        return await self.tab.get_content()

    async def evaluate(self, js_string: str):
        # Convert Playwright IIFEs (Arrow functions) into executable JS
        if js_string.strip().startswith("() =>"):
            js_string = f"({js_string})()"
        return await self.tab.evaluate(js_string)

    async def _get_element(self, selector: str):
        """Translates Playwright text selectors into native nodriver commands."""
        try:
            if selector.startswith("text="):
                # Convert "text=Accept" -> "Accept"
                text_val = selector.split("text=", 1)[1].strip("\"'")
                return await self.tab.find(text_val)
            elif ":has-text" in selector:
                # Convert "a:has-text('Download')" -> "Download"
                match = re.search(r":has-text\(['\"]?(.*?)['\"]?\)", selector)
                if match:
                    return await self.tab.find(match.group(1))
            
            # If it's standard CSS (e.g., #btn, .class), use native select
            return await self.tab.select(selector)
        except Exception:
            return None

    async def click(self, selector: str, timeout: int = 15000):
        elem = await self._get_element(selector)
        if elem:
            await elem.click()

    async def fill(self, selector: str, value: str, timeout: int = 15000):
        elem = await self._get_element(selector)
        if elem:
            await elem.clear_input()
            await elem.send_keys(value)

    async def press(self, selector: str, key: str, timeout: int = 15000):
        elem = await self._get_element(selector)
        if elem:
            await elem.send_keys(key)

    @property
    def keyboard(self):
        class KeyboardShim:
            async def press(self_, key: str):
                pass  # Handled directly in press()
        return KeyboardShim()

    async def wait_for_timeout(self, timeout_ms: int):
        await self.tab.sleep(timeout_ms / 1000.0)

    async def wait_for_selector(self, selector: str, timeout: int = 10000):
        await self._get_element(selector)

    async def screenshot(self, path: str, full_page: bool = False):
        await self.tab.save_screenshot(path)

    async def reload(self, wait_until: str = "domcontentloaded"):
        await self.tab.reload()

    def on(self, event, handler):
        if event == "request":
            self._request_handlers.append(handler)

    def remove_listener(self, event, handler):
        if event == "request" and handler in self._request_handlers:
            self._request_handlers.remove(handler)

    @property
    def context(self):
        tab_ref = self.tab
        class ContextShim:
            async def cookies(self_ctx):
                # Retrieve cookies directly via CDP
                cookies = await tab_ref.send(network.get_cookies())
                return [
                    {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                    for c in cookies
                ]
                
            async def add_cookies(self_ctx, pw_cookies: list[dict]):
                for c in pw_cookies:
                    await tab_ref.send(network.set_cookie(
                        name=c["name"],
                        value=c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/")
                    ))
            
            @property
            def request(self_ctx):
                # Replace Playwright's HTTP client with aiohttp for FlareSolverr
                class RequestShim:
                    async def post(self_req, url, data, headers):
                        async with aiohttp.ClientSession() as session:
                            resp = await session.post(url, json=data, headers=headers)
                            class RespShim:
                                ok = resp.ok
                                status = resp.status
                                async def json(self): return await resp.json()
                            return RespShim()
                return RequestShim()
        return ContextShim()


class Navigator:
    """Manages a single nodriver browser instance."""

    def __init__(self, headless: bool | None = None):
        self.headless = config.HEADLESS if headless is None else headless
        self.browser: uc.Browser | None = None
        self.page: PlaywrightPageShim | None = None

    # --- Lifecycle ---------------------------------------------------

    async def start(self) -> "Navigator":
        """Launch the stealth browser."""
        logger.info("Starting nodriver (undetected chrome)...")
        
        self.browser = await uc.start(
            headless=self.headless,
        )

        tab = await self.browser.get("about:blank")
        self.page = PlaywrightPageShim(self.browser, tab)
        return self

    async def close(self) -> None:
        """Close the browser."""
        if self.browser:
            self.browser.stop()

    async def __aenter__(self) -> "Navigator":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # --- Navigation ----------------------------------------------------

    async def goto(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        extra_settle_ms: int = 8000,
    ) -> None:
        if not self.browser:
            raise RuntimeError("Navigator not started — use 'async with Navigator()'")

        logger.info("Navigating to %s", url)
        tab = await self.browser.get(url)
        self.page = PlaywrightPageShim(self.browser, tab)

        if extra_settle_ms:
            await self.page.wait_for_timeout(extra_settle_ms)

    # --- Content extraction --------------------------------------------

    async def get_html(self) -> str:
        if not self.page:
            raise RuntimeError("Navigator not started")
        return await self.page.content()

    async def get_cookies(self) -> list[dict]:
        if not self.page:
            raise RuntimeError("Navigator not started")
        return await self.page.context.cookies()

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        if not self.page:
            raise RuntimeError("Navigator not started")
        await self.page.screenshot(path=path, full_page=full_page)

    @property
    def current_url(self) -> str | None:
        return self.page.url if self.page else None
