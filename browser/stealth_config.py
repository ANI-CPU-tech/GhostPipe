"""
Stealth Config — playwright-stealth patches and browser-context defaults
to reduce common headless-detection fingerprints.

This is NOT a Cloudflare/enterprise anti-bot bypass — see project docs,
section "Reality Checks". It patches things like navigator.webdriver,
chrome.runtime, plugin/mimeType arrays, and WebGL vendor strings so that
basic bot checks (login walls, JS-rendered pages, simple fingerprint
gates) behave like a normal browser.
"""

from playwright.async_api import BrowserContext
from playwright_stealth import Stealth


# A realistic, recent desktop user agent. Kept here so it's easy to
# rotate/update in one place.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1366, "height": 768}
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/New_York"

# Shared Stealth instance — playwright-stealth applies a bundle of
# evasion scripts (navigator.webdriver removal, plugin spoofing, etc.)
STEALTH = Stealth()


def context_options(
    user_agent: str = DEFAULT_USER_AGENT,
    viewport: dict | None = None,
    locale: str = DEFAULT_LOCALE,
    timezone_id: str = DEFAULT_TIMEZONE,
) -> dict:
    """
    Build a `browser.new_context(**options)` kwargs dict with
    stealth-friendly defaults.
    """
    return {
        "user_agent": user_agent,
        "viewport": viewport or DEFAULT_VIEWPORT,
        "locale": locale,
        "timezone_id": timezone_id,
    }


async def apply_stealth(context: BrowserContext) -> BrowserContext:
    """
    Apply playwright-stealth evasion scripts to an existing browser context.

    Every new page opened in this context will have the stealth patches
    injected before any site script runs.
    """
    await STEALTH.apply_stealth_async(context)
    return context
