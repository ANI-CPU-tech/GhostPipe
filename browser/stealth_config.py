"""
Stealth Config — context defaults for headless browsing.

Note: With the upgrade to Camoufox, C++ level anti-detect stealth 
is handled natively by the browser engine. The old `playwright-stealth` 
package has been removed to prevent engine conflicts.
"""

from playwright.async_api import BrowserContext

# A realistic, recent desktop user agent. Kept here so it's easy to
# rotate/update in one place.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1366, "height": 768}
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/New_York"


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
    Legacy stub: Camoufox now handles stealth natively at the C++ level.
    Returns the context unmodified.
    """
    return context
