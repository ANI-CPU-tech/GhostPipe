"""
Obstacle Handler — LLM-assisted handling of login walls, cookie-consent
banners, and "generate link" gate pages.
"""

import asyncio
import base64
import json
import logging
import tempfile
from dataclasses import dataclass, field
from typing import Literal

from groq import Groq
from playwright.async_api import Page

import config

logger = logging.getLogger(__name__)

OBSTACLE_MAX_ROUNDS = 4       # max LLM-guided action rounds per page
STEP_SETTLE_MS     = 1200     # ms to wait after each action before checking
POST_ACTION_SETTLE = 6000     # ms to wait after full action sequence

# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

ActionType = Literal["click", "fill", "press", "wait", "done", "fail"]

@dataclass
class Action:
    action: ActionType
    selector: str | None = None
    value: str | None = None      
    reason: str = ""

@dataclass
class ObstacleResult:
    cleared: bool                   
    final_url: str = ""
    final_html: str = ""
    actions_taken: list[Action] = field(default_factory=list)
    error: str | None = None

# --------------------------------------------------------------------------- #
# DOM snapshot helper
# --------------------------------------------------------------------------- #

_DOM_SNAPSHOT_JS = """
() => {
    const tags = ['input','button','a','select','textarea',
                   '[role="button"]','[role="dialog"]',
                   '[class*="modal"]','[class*="popup"]',
                   '[class*="login"]','[class*="cookie"]',
                   'iframe'];
    const els = document.querySelectorAll(tags.join(','));
    const out = [];
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;   // invisible
        out.push({
            tag:  el.tagName.toLowerCase(),
            id:   el.id   || null,
            // Compressed to save LLM tokens!
            cls:  (el.className || '').toString().trim().slice(0, 35),
            text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 35),
            type: el.type  || null,
            href: el.href ? el.href.toString().slice(0, 60) : null,
        });
        if (out.length >= 20) break;   
    }
    return out;
}
"""

async def _dom_snapshot(page: Page) -> str:
    """Return a compact JSON string of visible interactive elements."""
    try:
        # ANTI-HANG SHIELD: 5-second timeout to prevent Cloudflare from trapping the script
        elements = await asyncio.wait_for(page.evaluate(_DOM_SNAPSHOT_JS), timeout=5.0)
        return json.dumps(elements, indent=2)
    except asyncio.TimeoutError:
        logger.warning("DOM snapshot timed out (Cloudflare CDP trap active).")
        return "[]"
    except Exception as e:
        logger.warning("DOM snapshot failed: %s", e)
        return "[]"

async def _screenshot_b64(page: Page) -> str:
    """Return a base64-encoded PNG screenshot of the current viewport."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    await page.screenshot(path=path, full_page=False)
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# --------------------------------------------------------------------------- #
# LLM prompt + response parsing
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You are the obstacle-resolution module of GhostPipe, an \
autonomous web-navigation agent.

You will receive:
  - GOAL: what the agent is trying to reach
  - CURRENT URL: the page the browser is on
  - DOM SNAPSHOT: a JSON list of visible interactive elements

Your job: decide the MINIMUM set of actions needed to clear any obstacle \
(login wall, cookie banner, "generate link" gate, popup) so the \
browser can reach the actual content.

Respond ONLY with a JSON object matching:
{
  "assessment": "one sentence describing the obstacle",
  "obstacle_present": true | false,
  "actions": [
    {"action": "click",  "selector": "CSS selector",  "reason": "..."}
  ]
}

Rules:
- Use STRICT standard CSS selectors only (e.g., #btn-id, .class-name, [href="/download"]). DO NOT use "text=" pseudo-selectors.
- If no obstacle: single action {"action": "done", ...}
- Keep actions to minimum — do not over-click. Prefer id/name selectors over class.
"""

def _parse_actions(raw_json: str) -> tuple[bool, list[Action]]:
    """Parse LLM JSON → (obstacle_present, list of Action)."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return False, [Action(action="fail", reason=f"Invalid JSON: {e}")]

    obstacle_present = bool(data.get("obstacle_present", False))
    raw_actions = data.get("actions", [])

    actions = []
    for item in raw_actions:
        a = item.get("action", "")
        if a not in ("click", "fill", "press", "wait", "done", "fail"):
            continue
        actions.append(Action(
            action=a,
            selector=item.get("selector") or None,
            value=item.get("value") or None,
            reason=item.get("reason", ""),
        ))

    if not actions:
        actions = [Action(action="done", reason="no actions returned")]

    return obstacle_present, actions

# --------------------------------------------------------------------------- #
# Playwright action executor
# --------------------------------------------------------------------------- #

async def _execute_action(page: Page, action: Action) -> bool:
    """Execute a single action on the page with Anti-Hang shields."""
    try:
        if action.action == "click":
            await asyncio.wait_for(page.click(action.selector), timeout=8.0)
        elif action.action == "fill":
            await asyncio.wait_for(page.fill(action.selector, action.value or ""), timeout=8.0)
        elif action.action == "press":
            key = action.value or "Enter"
            if action.selector:
                await asyncio.wait_for(page.press(action.selector, key), timeout=8.0)
            else:
                await page.keyboard.press(key)
        elif action.action == "wait":
            await page.wait_for_timeout(STEP_SETTLE_MS)
        elif action.action in ("done", "fail"):
            return True

        await page.wait_for_timeout(STEP_SETTLE_MS)
        return True

    except asyncio.TimeoutError:
        logger.warning("Action %s on %r timed out (element not found or blocked)", action.action, action.selector)
        return False
    except Exception as e:
        logger.warning("Action %s on %r failed: %s", action.action, action.selector, e)
        return False

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def handle_obstacles(
    page: Page,
    goal: str,
    credentials: dict | None = None,
    groq_client: Groq | None = None,
    max_rounds: int = OBSTACLE_MAX_ROUNDS,
) -> ObstacleResult:
    """Detect and clear obstacles on the current page."""
    
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")

    client = groq_client or Groq(api_key=config.GROQ_API_KEY)
    all_actions: list[Action] = []

    creds_hint = ""
    if credentials:
        u = credentials.get("username", "")
        p = credentials.get("password", "")
        if u or p:
            creds_hint = f"\nCREDENTIALS AVAILABLE:\n  username → {u}\n  password → {p}\n"

    for round_num in range(1, max_rounds + 1):
        logger.info("Obstacle handler round %d/%d — %s", round_num, max_rounds, page.url)

        # --- TURNSTILE AUTO-SNIPER ---
        try:
            logger.info("Scanning for enterprise security gates...")
            # Native Playwright locator for the Cloudflare iframe
            cf_box = page.locator('iframe[src*="cloudflare"]')
            if await cf_box.count() > 0 and await cf_box.first.is_visible():
                logger.warning("Turnstile Auto-Sniper engaged! Clicking checkbox directly...")
                await asyncio.wait_for(cf_box.first.click(), timeout=2.0)
                await page.wait_for_timeout(6000)
                continue # Page cleared, skip the LLM and loop again
        except Exception:
            pass # No Turnstile found, proceed to normal LLM flow

        dom = await _dom_snapshot(page)
        user_content = f"GOAL: {goal}{creds_hint}\n\nCURRENT URL: {page.url}\n\nDOM SNAPSHOT:\n{dom}"

        try:
            response = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_content},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
        except Exception as e:
            logger.error("Groq call failed: %s", e)
            return ObstacleResult(cleared=False, final_url=page.url, actions_taken=all_actions, error=str(e))

        obstacle_present, actions = _parse_actions(raw)
        all_actions.extend(actions)

        # Check for terminal actions
        if not obstacle_present or (len(actions) == 1 and actions[0].action == "done"):
            logger.info("No obstacle detected — page is clear")
            try:
                final_html = await asyncio.wait_for(page.content(), timeout=5.0)
            except:
                final_html = ""
            return ObstacleResult(cleared=True, final_url=page.url, final_html=final_html, actions_taken=all_actions)

        if any(a.action == "fail" for a in actions):
            reason = next(a.reason for a in actions if a.action == "fail")
            logger.warning("Obstacle handler gave up: %s", reason)
            return ObstacleResult(cleared=False, final_url=page.url, actions_taken=all_actions, error=f"Unbeatable obstacle: {reason}")

        # Execute actions
        for action in actions:
            if action.action in ("done", "fail"):
                break
            await _execute_action(page, action)

        await page.wait_for_timeout(POST_ACTION_SETTLE)

    logger.warning("Obstacle handler hit max rounds (%d) without clearing", max_rounds)
    return ObstacleResult(cleared=False, final_url=page.url, final_html="", actions_taken=all_actions, error="Max rounds reached")
