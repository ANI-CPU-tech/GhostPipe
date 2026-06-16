"""
Obstacle Handler — LLM-assisted handling of login walls, cookie-consent
banners, and "generate link" gate pages.

Strategy:
  1. Take a screenshot + extract a compact DOM snapshot (visible interactive
     elements only — buttons, inputs, links, dialogs).
  2. Send both to Groq/Llama 3.1 with a prompt describing what GhostPipe is
     trying to accomplish.
  3. The LLM returns a JSON action plan: a list of steps like
       {"action": "click", "selector": "#accept-btn"}
     or
       {"action": "fill",  "selector": "#email", "value": "user@example.com"}
  4. Execute each step via Playwright, with a brief settle between steps.
  5. Return the resulting page state (HTML + URL) so the caller can check
     whether the obstacle is cleared or another pass is needed.

Max retries are capped (OBSTACLE_MAX_ROUNDS) to avoid infinite loops on
genuinely unbeatable gates (Cloudflare Turnstile, etc.).
"""

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
POST_ACTION_SETTLE = 2000     # ms to wait after full action sequence


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

ActionType = Literal["click", "fill", "press", "wait", "done", "fail"]


@dataclass
class Action:
    action: ActionType
    selector: str | None = None
    value: str | None = None      # used by "fill" and "press"
    reason: str = ""


@dataclass
class ObstacleResult:
    cleared: bool                   # True → obstacle gone, proceed
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
                   '[class*="login"]','[class*="cookie"]'];
    const els = document.querySelectorAll(tags.join(','));
    const out = [];
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;   // invisible
        out.push({
            tag:  el.tagName.toLowerCase(),
            id:   el.id   || null,
            cls:  (el.className || '').toString().trim().slice(0, 80),
            text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 80),
            type: el.type  || null,
            href: el.href  || null,
            name: el.name  || null,
        });
        if (out.length >= 40) break;   // cap to keep prompt small
    }
    return out;
}
"""


async def _dom_snapshot(page: Page) -> str:
    """Return a compact JSON string of visible interactive elements."""
    try:
        elements = await page.evaluate(_DOM_SNAPSHOT_JS)
        return json.dumps(elements, indent=2)
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
  - DOM SNAPSHOT: a JSON list of visible interactive elements (tag, id, class, \
text, type, href, name)

Your job: decide the MINIMUM set of actions needed to clear any obstacle \
(login wall, cookie banner, "generate link" gate, age check, popup) so the \
browser can reach the actual content.

Respond ONLY with a JSON object, no markdown, matching:
{
  "assessment": "one sentence describing what obstacle (if any) is present",
  "obstacle_present": true | false,
  "actions": [
    {"action": "click",  "selector": "CSS selector or text selector",  "reason": "short reason"},
    {"action": "fill",   "selector": "CSS selector", "value": "text to type", "reason": "..."},
    {"action": "press",  "selector": "CSS selector", "value": "Enter",        "reason": "..."},
    {"action": "wait",   "selector": null,            "value": null,           "reason": "let page settle"},
    {"action": "done",   "selector": null,            "value": null,           "reason": "no obstacle"}
  ]
}

Rules:
- Use Playwright CSS selectors or text selectors like: text=Accept, #btn-id, .class-name
- If no obstacle: single action {"action": "done", ...}
- If obstacle is unbeatable (Cloudflare Turnstile, reCAPTCHA): single action \
{"action": "fail", "reason": "describe why"}
- Keep actions to minimum — do not over-click. Prefer id/name selectors over class when available.
"""


def _parse_actions(raw_json: str) -> tuple[bool, list[Action]]:
    """Parse LLM JSON → (obstacle_present, list of Action)."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("Obstacle handler: invalid JSON from LLM: %s", e)
        return False, [Action(action="fail", reason=f"LLM returned invalid JSON: {e}")]

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
    """
    Execute a single action on the page.

    Returns True if successful, False if it fails (selector not found, etc.).
    Failures are logged as warnings — the loop continues to the next step.
    """
    try:
        if action.action == "click":
            await page.click(action.selector, timeout=6000)

        elif action.action == "fill":
            await page.fill(action.selector, action.value or "", timeout=6000)

        elif action.action == "press":
            key = action.value or "Enter"
            if action.selector:
                await page.press(action.selector, key, timeout=6000)
            else:
                await page.keyboard.press(key)

        elif action.action == "wait":
            await page.wait_for_timeout(STEP_SETTLE_MS)

        elif action.action in ("done", "fail"):
            return True   # handled by caller

        await page.wait_for_timeout(STEP_SETTLE_MS)
        return True

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
    """
    Detect and clear obstacles on the current page using an LLM-guided loop.

    Args:
        page:        Active Playwright Page already loaded to the target URL.
        goal:        Natural-language description of what GhostPipe is trying
                     to reach (passed to the LLM as context).
        credentials: Optional dict with "username"/"password" keys so the LLM
                     can instruct fill actions to use real credentials.
        groq_client: Pre-built Groq client (optional; built from config if None).
        max_rounds:  Maximum LLM round-trips before giving up.

    Returns:
        ObstacleResult with .cleared=True if the page appears unblocked,
        or .cleared=False if a FAIL action was returned or max rounds hit.
    """
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set — obstacle handler requires Groq")

    client = groq_client or Groq(api_key=config.GROQ_API_KEY)
    all_actions: list[Action] = []

    # Build credentials context string for the system prompt
    creds_hint = ""
    if credentials:
        u = credentials.get("username", "")
        p = credentials.get("password", "")
        if u or p:
            creds_hint = (
                f"\nCREDENTIALS AVAILABLE — if a login form is present, fill:\n"
                f"  username/email → {u}\n"
                f"  password → {p}\n"
            )

    for round_num in range(1, max_rounds + 1):
        logger.info("Obstacle handler round %d/%d — %s", round_num, max_rounds, page.url)

        dom    = await _dom_snapshot(page)
        user_content = (
            f"GOAL: {goal}{creds_hint}\n\n"
            f"CURRENT URL: {page.url}\n\n"
            f"DOM SNAPSHOT:\n{dom}"
        )

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
            logger.error("Groq call failed in obstacle handler: %s", e)
            return ObstacleResult(
                cleared=False,
                final_url=page.url,
                actions_taken=all_actions,
                error=str(e),
            )

        obstacle_present, actions = _parse_actions(raw)
        all_actions.extend(actions)

        # Check for terminal actions before executing
        if not obstacle_present or (len(actions) == 1 and actions[0].action == "done"):
            logger.info("No obstacle detected — page is clear")
            return ObstacleResult(
                cleared=True,
                final_url=page.url,
                final_html=await page.content(),
                actions_taken=all_actions,
            )

        if any(a.action == "fail" for a in actions):
            reason = next(a.reason for a in actions if a.action == "fail")
            logger.warning("Obstacle handler gave up: %s", reason)
            return ObstacleResult(
                cleared=False,
                final_url=page.url,
                actions_taken=all_actions,
                error=f"Unbeatable obstacle: {reason}",
            )

        # Execute the action sequence
        for action in actions:
            if action.action in ("done", "fail"):
                break
            await _execute_action(page, action)

        # Let the page settle after a full action sequence
        await page.wait_for_timeout(POST_ACTION_SETTLE)

    # Max rounds hit
    logger.warning("Obstacle handler hit max rounds (%d) without clearing", max_rounds)
    return ObstacleResult(
        cleared=False,
        final_url=page.url,
        final_html=await page.content(),
        actions_taken=all_actions,
        error=f"Max rounds ({max_rounds}) reached without clearing obstacle",
    )


