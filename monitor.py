#!/usr/bin/env python3
"""Watch VOX Egypt for Avengers: Doomsday - Infinity Vision at Mall of Egypt.

Sends a Telegram alert the first time a showtime for 24-31 December 2026 is
listed, plus at most one "still clear" update per Cairo calendar day near the
end of that day.

A page that cannot be loaded, is blocked, or cannot be understood is a FAILURE
(non-zero exit). It is never reported as "no showtimes".

Telegram credentials are read only from TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # Playwright/requests are imported lazily so --gate needs only the stdlib.
    from playwright.async_api import BrowserContext, Page

VOX_URL = (
    "https://egy.voxcinemas.com/showtimes"
    "?c=mall-of-egypt&m=avengers-doomsday-infinity-vision"
)
MOVIE_TITLE = "Avengers: Doomsday (Infinity Vision)"
CINEMA = "Mall of Egypt"
START_DATE = date(2026, 12, 24)
END_DATE = date(2026, 12, 31)
TARGET_DATES = [date(2026, 12, day) for day in range(24, 32)]
TZ = ZoneInfo("Africa/Cairo")
STATE_PATH = Path(__file__).resolve().parent / "state.json"

# The "still clear" update is sent by the first successful no-match check at or
# after this Cairo time. Several runs fall in the window, so a delayed or failed
# run does not cause a missed day.
CLEAR_UPDATE_FROM = (22, 30)

NAV_ATTEMPTS = 2
NAV_TIMEOUT_MS = 60_000
READY_TIMEOUT_S = 40
CHECK_DEADLINE_S = 420

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BLOCK_MARKERS = (
    "access denied",
    "attention required",
    "just a moment",
    "verify you are human",
    "captcha",
    "request blocked",
    "unusual traffic",
    "error 1020",
)
# Best-effort list used only to attribute session times when a page lists
# several venues. The URL itself already filters to Mall of Egypt.
KNOWN_VENUES = ("City Centre Alexandria", "City Centre Almaza", "Mall of Egypt")

TIME_RE = re.compile(r"(?<![\d:])(0?[1-9]|1[0-2]):([0-5]\d)\s?([AaPp])\.?[Mm]\b")
ANY_CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])")


class CheckFailed(Exception):
    """The VOX page could not be loaded or understood. Never means 'no showtimes'."""


class TelegramError(Exception):
    """A Telegram message could not be sent. Messages never contain credentials."""


# --------------------------------------------------------------------------- #
# Logging with credential redaction
# --------------------------------------------------------------------------- #
def redact(text: str) -> str:
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        value = os.environ.get(name, "").strip()
        if len(value) >= 3:
            text = text.replace(value, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_handler.addFilter(RedactingFilter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), handlers=[_handler])
log = logging.getLogger("vox-monitor")


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def default_state() -> dict[str, Any]:
    return {
        "activation_sent": False,
        "alert_sent": False,
        "matches": [],
        "last_clear_status_date": None,
    }


def load_state() -> dict[str, Any]:
    state = default_state()
    if not STATE_PATH.exists():
        return state
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read state.json safely: {type(exc).__name__}") from None
    if not isinstance(raw, dict):
        raise RuntimeError("state.json is not a JSON object")

    # Accept the keys written by the earlier version of this project.
    state["activation_sent"] = bool(raw.get("activation_sent", raw.get("initialized", False)))
    state["alert_sent"] = bool(raw.get("alert_sent", False))
    matches = raw.get("matches")
    state["matches"] = matches if isinstance(matches, list) else []
    state["last_clear_status_date"] = raw.get(
        "last_clear_status_date", raw.get("clear_status_date")
    )
    if "alert_sent_at" in raw:
        state["alert_sent_at"] = raw["alert_sent_at"]
    return state


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, STATE_PATH)


def cairo_now() -> datetime:
    return datetime.now(TZ)


def in_clear_window(now: datetime) -> bool:
    return (now.hour, now.minute) >= CLEAR_UPDATE_FROM


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def require_telegram_env() -> None:
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or not os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    ).strip():
        raise TelegramError(
            "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID is not set in the environment"
        )


def telegram_send(text: str) -> None:
    require_telegram_env()
    import requests

    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            response = requests.post(url, json=payload, timeout=(10, 20))
        except requests.RequestException as exc:
            # Only the exception type: request errors embed the URL (and token).
            last_error = f"network error ({type(exc).__name__})"
        else:
            description = ""
            try:
                body = response.json()
                description = str(body.get("description", ""))[:200]
                if response.status_code == 200 and body.get("ok"):
                    return
            except ValueError:
                pass
            last_error = f"HTTP {response.status_code} {description}".strip()
            if 400 <= response.status_code < 500 and response.status_code != 429:
                break  # Retrying a rejected request will not help.
        if attempt < 3:
            time.sleep(2 * attempt)
    raise TelegramError(redact(f"Telegram send failed: {last_error}"))


def fmt_date(d: date) -> str:
    return f"{d:%A}, {d.day} {d:%B %Y}"


def build_alert(matches: list[dict[str, Any]]) -> str:
    lines = [
        "🚨 <b>VOX showtimes are live!</b>",
        "",
        f"🎬 {html.escape(MOVIE_TITLE)}",
        f"📍 {html.escape(CINEMA)}",
        "",
    ]
    for match in matches:
        lines.append(
            f"📅 <b>{html.escape(match['date_label'])}</b>: "
            f"{html.escape(', '.join(match['times']))}"
        )
    if any(not match.get("verified", True) for match in matches):
        lines += [
            "",
            "⚠️ The page did not confirm which date was selected, so check the date on VOX.",
        ]
    lines += ["", f'<a href="{html.escape(VOX_URL, quote=True)}">Open the VOX page</a>']
    return "\n".join(lines)


def build_activation() -> str:
    return (
        "✅ <b>VOX monitor is active.</b>\n\n"
        "The first check succeeded: the VOX page loaded and no matching showtimes "
        f"are listed yet for {html.escape(MOVIE_TITLE)} at {html.escape(CINEMA)} "
        "on 24–31 December 2026.\n\n"
        "You will get an alert as soon as one appears, and one “still clear” "
        "update near the end of each Cairo day."
    )


def build_clear(now: datetime) -> str:
    text = (
        "🟢 <b>Still clear.</b>\n\n"
        f"No showtimes for {html.escape(MOVIE_TITLE)} at {html.escape(CINEMA)} "
        f"on 24–31 December 2026 as of {now:%H:%M} Cairo time on "
        f"{html.escape(fmt_date(now.date()))}."
    )
    if now.date() >= END_DATE:
        text += "\n\nThis is the last day of the monitoring window."
    return text


# --------------------------------------------------------------------------- #
# Page parsing (pure functions, unit-testable)
# --------------------------------------------------------------------------- #
def normalized_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def format_time(match: re.Match[str]) -> str:
    hour, minute, meridiem = match.groups()
    return f"{int(hour)}:{minute} {meridiem.upper()}M"


def extract_session_times(text: str) -> list[str]:
    """Return session times that belong to Mall of Egypt, in display order.

    If the page lists other known venues as headings, only times under the
    Mall of Egypt heading count. If it lists no other venue (the usual case for
    a page already filtered with c=mall-of-egypt), every time on the page counts.
    """
    lines = normalized_lines(text)
    venue_at: dict[int, str] = {}
    for index, line in enumerate(lines):
        for venue in KNOWN_VENUES:
            if line.casefold() == venue.casefold():
                venue_at[index] = venue
    multi_venue = any(v.casefold() != CINEMA.casefold() for v in venue_at.values())

    times: list[str] = []
    current: str | None = None
    for index, line in enumerate(lines):
        if index in venue_at:
            current = venue_at[index]
            continue
        if multi_venue and (current is None or current.casefold() != CINEMA.casefold()):
            continue
        times.extend(format_time(m) for m in TIME_RE.finditer(line))
    return list(dict.fromkeys(times))


def date_label_pattern(d: date) -> str:
    """Regex source (JS and Python compatible) for a tab label such as 'Thu 24 Dec'."""
    weekday = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s*"
    day = rf"0?{d.day}(?:st|nd|rd|th)?"
    month = r"(?:dec|december)\.?"
    year = rf"(?:\s*,?\s*{d.year})?"
    return rf"^(?:{weekday})?(?:{day}\s*{month}|{month}\s*{day}){year}$"


def date_in_url(url: str, d: date) -> bool:
    return f"{d:%Y%m%d}" in url or f"{d:%Y-%m-%d}" in url


# --------------------------------------------------------------------------- #
# Browser helpers
# --------------------------------------------------------------------------- #
JS_RAW_TEXT = "() => (document.body ? document.body.innerText : '')"

# Text of the page without selectors, navigation and footers, so a cinema
# dropdown or menu cannot be mistaken for session content.
JS_CONTENT_TEXT = r"""() => {
  const hidden = [];
  const sel = 'select, nav, footer, script, style, noscript, body > header, [role="banner"]';
  for (const el of document.querySelectorAll(sel)) {
    hidden.push([el, el.style.display]);
    el.style.display = 'none';
  }
  const text = document.body ? document.body.innerText : '';
  for (const [el, display] of hidden) el.style.display = display;
  return text;
}"""

JS_SCROLL_STRIPS = r"""() => {
  for (const el of document.querySelectorAll('*')) {
    if (el.clientWidth > 100 && el.scrollWidth > el.clientWidth + 30) {
      el.scrollLeft = el.scrollWidth;
    }
  }
}"""

JS_LIST_DATE_LABELS = r"""() => {
  const norm = v => (v || '').replace(/\s+/g, ' ').trim();
  const mon = '(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?';
  const wd = '(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\\.?,?\\s*)?';
  const re = new RegExp('^' + wd + '(?:\\d{1,2}(?:st|nd|rd|th)?\\s*' + mon +
    '|' + mon + '\\s*\\d{1,2}(?:st|nd|rd|th)?)(?:\\s*,?\\s*\\d{4})?$', 'i');
  const out = new Set();
  const sel = 'button, a, [role="tab"], [role="button"], [onclick], li, label, span, div, option';
  for (const el of document.querySelectorAll(sel)) {
    const t = norm(el.innerText || el.textContent);
    if (t.length <= 24 && re.test(t)) out.add(t);
  }
  return [...out].slice(0, 60);
}"""

JS_CLICK_DATE = r"""(source) => {
  const re = new RegExp(source, 'i');
  const norm = v => (v || '').replace(/\s+/g, ' ').trim();
  const textOf = el => norm(el.innerText || el.textContent);
  const sel = 'button, a, [role="tab"], [role="button"], [onclick], li, label, span, div, option';
  const found = [...document.querySelectorAll(sel)].filter(el => {
    const t = textOf(el);
    return t.length <= 24 && re.test(t);
  });
  if (!found.length) return {found: false};
  // Innermost matches only: an outer wrapper with identical text is not the tab.
  const innermost = found.filter(el => !found.some(o => o !== el && el.contains(o)));
  innermost.sort((a, b) => textOf(a).length - textOf(b).length);
  let target = innermost[0];
  if (target.tagName !== 'OPTION') {
    const interactive = 'button, a, [role="tab"], [role="button"], [onclick], label';
    for (let n = target; n && n !== document.body; n = n.parentElement) {
      if (n.matches(interactive) || getComputedStyle(n).cursor === 'pointer') {
        target = n;
        break;
      }
    }
  }
  const chain = [];
  for (let n = target, i = 0; n && i < 4; n = n.parentElement, i++) chain.push(n);
  const disabledRe = /(^|[\s_-])(disabled|inactive|unavailable|sold-?out)([\s_-]|$)/i;
  const disabled = target.disabled === true || chain.some(n =>
    n.getAttribute('aria-disabled') === 'true' || disabledRe.test(n.getAttribute('class') || ''));
  for (const old of document.querySelectorAll('[data-vox-monitor-target]')) {
    old.removeAttribute('data-vox-monitor-target');
  }
  target.setAttribute('data-vox-monitor-target', '1');
  const label = textOf(target);
  if (disabled) return {found: true, disabled: true, label};
  target.scrollIntoView({block: 'center', inline: 'center'});
  if (target.tagName === 'OPTION') {
    const select = target.closest('select');
    select.value = target.value;
    select.dispatchEvent(new Event('input', {bubbles: true}));
    select.dispatchEvent(new Event('change', {bubbles: true}));
  } else {
    target.click();
  }
  return {found: true, disabled: false, label};
}"""

JS_SELECTED_STATE = r"""() => {
  const t = document.querySelector('[data-vox-monitor-target]');
  if (!t) return null;
  if (t.tagName === 'OPTION') return !!t.selected;
  let sawAria = false;
  for (let n = t, i = 0; n && i < 4; n = n.parentElement, i++) {
    for (const attr of ['aria-selected', 'aria-current', 'aria-pressed', 'aria-checked']) {
      const v = n.getAttribute(attr);
      if (v === null) continue;
      sawAria = true;
      if (v !== 'false') return true;
    }
    if (/(^|[\s_-])(active|selected|current|checked)([\s_-]|$)/i.test(n.getAttribute('class') || '')) {
      return true;
    }
  }
  return sawAria ? false : null;
}"""


def first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:300]


async def read_text(page: Page, content_only: bool) -> str:
    script = JS_CONTENT_TEXT if content_only else JS_RAW_TEXT
    parts: list[str] = []
    for frame in page.frames:
        try:
            text = await frame.evaluate(script)
        except Exception:  # frame navigating or detached
            continue
        if text:
            parts.append(text)
    return "\n".join(parts)


async def wait_until_ready(page: Page) -> None:
    """Wait until the page is recognisably the VOX Mall of Egypt / Doomsday page."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    length = 0
    blocked = ""
    while time.monotonic() < deadline:
        text = (await read_text(page, content_only=False)).lower()
        length = len(text)
        if "mall of egypt" in text or "doomsday" in text:
            return
        blocked = next((m for m in BLOCK_MARKERS if m in text), "")
        if blocked:
            raise CheckFailed(f"Block or challenge page detected (marker: {blocked!r})")
        await asyncio.sleep(1)
    raise CheckFailed(
        f"VOX page content was not recognised within {READY_TIMEOUT_S}s "
        f"(visible text length: {length})"
    )


async def load_page(context: BrowserContext) -> Page:
    from playwright.async_api import Error as PlaywrightError

    last_error = "unknown"
    for attempt in range(1, NAV_ATTEMPTS + 1):
        page = await context.new_page()
        started = time.monotonic()
        try:
            log.info("Navigation attempt %d/%d: %s", attempt, NAV_ATTEMPTS, VOX_URL)
            response = await page.goto(VOX_URL, wait_until="commit", timeout=NAV_TIMEOUT_MS)
            status = response.status if response else None
            server = response.headers.get("server", "?") if response else "?"
            log.info(
                "Response after %.1fs: HTTP %s (server: %s, final URL: %s)",
                time.monotonic() - started, status, server, page.url,
            )
            if status is None or status >= 400:
                raise CheckFailed(f"VOX answered HTTP {status}")
            await wait_until_ready(page)
            log.info("VOX page recognised after %.1fs", time.monotonic() - started)
            return page
        except (PlaywrightError, CheckFailed) as exc:
            last_error = f"{type(exc).__name__}: {first_line(exc)}"
            log.warning(
                "Attempt %d/%d failed after %.1fs: %s",
                attempt, NAV_ATTEMPTS, time.monotonic() - started, last_error,
            )
            try:
                await page.close()
            except Exception:
                pass
            if attempt < NAV_ATTEMPTS:
                await asyncio.sleep(5)
    raise CheckFailed(
        f"Could not load the VOX page after {NAV_ATTEMPTS} attempts. Last error: {last_error}"
    )


async def list_date_labels(page: Page) -> list[str]:
    labels: dict[str, None] = {}
    for frame in page.frames:
        try:
            for label in await frame.evaluate(JS_LIST_DATE_LABELS):
                labels[label] = None
        except Exception:
            continue
    return list(labels)


async def settle(page: Page) -> None:
    for state, timeout in (("domcontentloaded", 10_000), ("networkidle", 4_000)):
        try:
            await page.wait_for_load_state(state, timeout=timeout)
        except Exception:
            pass
    await page.wait_for_timeout(1000)


async def click_date(page: Page, d: date) -> tuple[Any, dict[str, Any]]:
    source = date_label_pattern(d)
    for frame in page.frames:
        try:
            result = await frame.evaluate(JS_CLICK_DATE, source)
        except Exception:
            continue
        if result.get("found"):
            return frame, result
    return None, {"found": False}


async def selected_state(frame: Any) -> bool | None:
    try:
        return await frame.evaluate(JS_SELECTED_STATE)
    except Exception:  # the click navigated; state cannot be read
        return None


async def collect_matches(page: Page, debug: bool) -> list[dict[str, Any]]:
    for frame in page.frames:
        try:
            await frame.evaluate(JS_SCROLL_STRIPS)
        except Exception:
            pass
    await page.wait_for_timeout(500)

    labels = await list_date_labels(page)
    base_text = await read_text(page, content_only=True)
    base_times = extract_session_times(base_text)
    log.info("Date tabs visible: %s", labels if labels else "none")
    log.info("Session times in the default view: %d", len(base_times))
    if debug:
        log.info("Default view text:\n%s", base_text[:6000])

    if not labels:
        if base_times:
            log.error("Page text (first 2000 chars):\n%s", base_text[:2000])
            raise CheckFailed(
                "Session times are listed but no date tabs were recognised, so dates "
                "cannot be attributed. The VOX layout probably changed."
            )
        clocks = ANY_CLOCK_RE.findall(base_text)
        if clocks:
            log.warning(
                "No AM/PM times found, but %d clock-like value(s) exist: %s",
                len(clocks), clocks[:8],
            )
        log.info("Page loaded, no date tabs and no session times: nothing published yet.")
        return []

    matches: list[dict[str, Any]] = []
    for d in TARGET_DATES:
        frame, result = await click_date(page, d)
        if not result.get("found"):
            log.info("%s: no date tab (not published)", d.isoformat())
            continue
        if result.get("disabled"):
            log.info("%s: date tab is disabled (no sessions)", d.isoformat())
            continue

        await settle(page)
        selected = await selected_state(frame)
        verified = selected is True or date_in_url(page.url, d)
        if selected is False and not date_in_url(page.url, d):
            log.info("%s: tab did not become selected; ignoring", d.isoformat())
            continue
        if not verified:
            log.warning("%s: selection could not be verified from the page", d.isoformat())

        text = await read_text(page, content_only=True)
        times = extract_session_times(text)
        if debug:
            log.info("Text after selecting %s:\n%s", d.isoformat(), text[:6000])
        if times:
            log.info("MATCH %s: %s (verified=%s)", d.isoformat(), ", ".join(times), verified)
            matches.append(
                {
                    "date": d.isoformat(),
                    "date_label": fmt_date(d),
                    "times": times,
                    "verified": verified,
                }
            )
        else:
            log.info("%s: tab found, no session times listed", d.isoformat())
    return matches


async def check_vox(debug: bool) -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-http2"])
        try:
            context = await browser.new_context(
                locale="en-GB",
                timezone_id="Africa/Cairo",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            try:
                page = await load_page(context)
                return await collect_matches(page, debug)
            finally:
                await context.close()
        finally:
            await browser.close()


def run_check(debug: bool) -> list[dict[str, Any]]:
    try:
        return asyncio.run(asyncio.wait_for(check_vox(debug), timeout=CHECK_DEADLINE_S))
    except asyncio.TimeoutError:
        raise CheckFailed(f"The VOX check exceeded {CHECK_DEADLINE_S}s") from None


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def gate() -> int:
    """Tell the workflow whether any monitoring is still needed (stdlib only)."""
    state = load_state()
    if state["alert_sent"]:
        active, reason = False, "alert already sent"
    elif cairo_now().date() > END_DATE:
        active, reason = False, "monitoring window has ended"
    else:
        active, reason = True, "monitoring in progress"
    log.info("Monitoring active: %s (%s)", active, reason)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"active={'true' if active else 'false'}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gate", action="store_true", help="write active=true|false to GITHUB_OUTPUT")
    parser.add_argument("--debug", action="store_true", help="log the page text per date")
    args = parser.parse_args(argv)
    if args.gate:
        return gate()
    debug = args.debug or os.environ.get("MONITOR_DEBUG") == "1"

    state = load_state()
    if state["alert_sent"]:
        log.info("Alert already sent; nothing more to do.")
        return 0
    if cairo_now().date() > END_DATE:
        log.info("The monitoring window (to %s) has ended.", END_DATE.isoformat())
        return 0

    require_telegram_env()  # fail fast, before spending a browser run

    try:
        matches = run_check(debug)
    except CheckFailed as exc:
        log.error("VOX could NOT be checked: %s", redact(str(exc)))
        log.error("This run is a failure, not a 'no showtimes' result.")
        return 1

    # From here on the VOX page was loaded and understood.
    if matches:
        telegram_send(build_alert(matches))
        state.update(
            alert_sent=True,
            activation_sent=True,
            matches=matches,
            alert_sent_at=cairo_now().isoformat(timespec="seconds"),
        )
        save_state(state)
        log.info("Alert sent; monitoring is complete.")
        return 0

    log.info("VOX checked successfully: no matching showtimes.")
    if not state["activation_sent"]:
        telegram_send(build_activation())
        state["activation_sent"] = True
        save_state(state)
        log.info("Activation message sent.")

    now = cairo_now()
    today = now.date().isoformat()
    if in_clear_window(now) and state["last_clear_status_date"] != today:
        telegram_send(build_clear(now))
        state["last_clear_status_date"] = today
        save_state(state)
        log.info("'Still clear' update sent for %s.", today)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log.error("Monitor run failed:\n%s", redact(traceback.format_exc()))
        code = 1
    sys.exit(code)
