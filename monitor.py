#!/usr/bin/env python3
"""Monitor VOX Egypt's Infinity Vision listing and send a one-time Telegram alert."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import BrowserContext, Frame, Page, async_playwright

VOX_MOVIE_URL = (
    "https://egy.voxcinemas.com/showtimes"
    "?c=mall-of-egypt&m=avengers-doomsday-infinity-vision"
)
MOVIE_TITLE = "Avengers: Doomsday (Infinity Vision)"
CINEMA_NAME = "Mall of Egypt"
START_DATE = date(2026, 12, 24)
END_DATE = date(2026, 12, 31)
TIMEZONE = ZoneInfo("Africa/Cairo")
STATE_PATH = Path("state.json")

# VOX's English date selector currently uses labels such as "Wed 16 Dec".
DATE_LABELS = {
    date(2026, 12, day): date(2026, 12, day).strftime("%a %-d %b")
    for day in range(24, 32)
}
DATE_ALIASES = {
    dt: (
        label,
        dt.strftime("%-d Dec"),
        dt.strftime("%a %-d December"),
        dt.strftime("%-d December"),
    )
    for dt, label in DATE_LABELS.items()
}

TIME_RE = re.compile(
    r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s?(?:a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
KNOWN_VENUES = ("City Centre Alexandria", "City Centre Almaza", "Mall of Egypt")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("vox-watcher")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"initialized": False, "alert_sent": False, "matches": []}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state.json is not a JSON object")
        state.setdefault("initialized", False)
        state.setdefault("alert_sent", False)
        state.setdefault("matches", [])
        return state
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Cannot read state.json safely: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATE_PATH)


def telegram_send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables."
        )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram rejected the message: {payload}")


def normalized_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]


def get_target_venue_times(text: str) -> list[str]:
    """Extract times only from the Mall of Egypt block."""
    lines = normalized_lines(text)
    venue_positions = [
        index
        for index, line in enumerate(lines)
        if line.casefold() == CINEMA_NAME.casefold()
    ]
    all_times: list[str] = []

    for start in venue_positions:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].casefold() in {
                venue.casefold() for venue in KNOWN_VENUES
            }:
                end = index
                break
        segment = "\n".join(lines[start + 1 : end])
        times = [
            re.sub(r"\s+", "", match).lower()
            for match in TIME_RE.findall(segment)
        ]
        if times:
            all_times.extend(times)

    # Remove duplicates while preserving VOX's display order.
    return list(dict.fromkeys(all_times))


async def scroll_date_strips(frame: Frame) -> None:
    try:
        await frame.evaluate(
            """() => {
                for (const el of document.querySelectorAll('*')) {
                    if (el.clientWidth > 100 && el.scrollWidth > el.clientWidth + 30) {
                        el.scrollLeft = el.scrollWidth;
                    }
                }
            }"""
        )
    except Exception:
        pass


async def click_date_label(frame: Frame, label: str) -> bool:
    """Click a date tab even when VOX renders it as nested div/span elements."""
    try:
        return bool(
            await frame.evaluate(
                r"""(wanted) => {
                    const norm = value =>
                        (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const candidates = [...document.querySelectorAll(
                        'button, a, [role="button"], [onclick], li, span, div'
                    )].filter(
                        el => norm(el.innerText || el.textContent) === norm(wanted)
                    );
                    if (!candidates.length) return false;
                    candidates.sort((a, b) =>
                        (a.innerText || a.textContent || '').length -
                        (b.innerText || b.textContent || '').length
                    );
                    let target = candidates[0];
                    for (
                        let node = target;
                        node && node !== document.body;
                        node = node.parentElement
                    ) {
                        const style = window.getComputedStyle(node);
                        if (
                            node.matches(
                                'button, a, [role="button"], [onclick]'
                            ) ||
                            style.cursor === 'pointer'
                        ) {
                            target = node;
                            break;
                        }
                    }
                    target.scrollIntoView({
                        block: 'center',
                        inline: 'center'
                    });
                    target.click();
                    return true;
                }""",
                label,
            )
        )
    except Exception:
        return False


async def read_body(frame: Frame) -> str:
    try:
        return await frame.locator("body").inner_text(timeout=2500)
    except Exception:
        return ""


async def monitor_vox(debug: bool = False) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-http2"],
        )
        context: BrowserContext = await browser.new_context(
            locale="en-GB",
            timezone_id="Africa/Cairo",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page: Page = await context.new_page()

        try:
            log.info("Opening official VOX Mall of Egypt Infinity Vision showtimes")
            await page.goto(
                VOX_MOVIE_URL,
               wait_until="commit",
                timeout=60000,
            )
            await page.wait_for_timeout(5000)

            # Inspect the main page and child frames for booking content.
            frames = list(page.frames)
            for frame in frames:
                await scroll_date_strips(frame)
            await page.wait_for_timeout(800)

            for target_date, aliases in DATE_ALIASES.items():
                found_date_tab = False
                for frame in list(page.frames):
                    for alias in aliases:
                        if await click_date_label(frame, alias):
                            found_date_tab = True
                            await page.wait_for_timeout(1100)
                            break
                    if found_date_tab:
                        break

                if not found_date_tab:
                    log.info("Date tab not exposed yet: %s", DATE_LABELS[target_date])
                    continue

                # Re-read all frames after selecting the date.
                page_texts = [
                    await read_body(frame) for frame in list(page.frames)
                ]
                combined_text = "\n".join(text for text in page_texts if text)
                if debug:
                    log.info(
                        "After selecting %s, page text:\n%s",
                        DATE_LABELS[target_date],
                        combined_text[:12000],
                    )

                times = get_target_venue_times(combined_text)
                if times:
                    match = {
                        "date": target_date.isoformat(),
                        "date_label": DATE_LABELS[target_date],
                        "times": ", ".join(times),
                    }
                    matches.append(match)
                    log.info(
                        "MATCH: %s at %s — %s",
                        match["date_label"],
                        CINEMA_NAME,
                        match["times"],
                    )

            return matches
        finally:
            await context.close()
            await browser.close()


def build_alert(matches: list[dict[str, str]]) -> str:
    lines = [
        "🚨 <b>VOX BOOKING IS OPEN!</b>",
        "",
        f"🎬 {html.escape(MOVIE_TITLE)}",
        f"📍 {html.escape(CINEMA_NAME)}",
        "",
        "<b>Available sessions:</b>",
    ]
    for match in matches:
        lines.append(
            f"• {html.escape(match['date_label'])}: "
            f"{html.escape(match['times'])}"
        )
    lines.extend(
        [
            "",
            f'<a href="{VOX_MOVIE_URL}">Open VOX booking page</a>',
            "",
            "This is the Infinity Vision listing; check out promptly because seats may sell out.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    debug = "--debug" in sys.argv
    state = load_state()

    if state.get("alert_sent"):
        log.info("Alert already sent. No further checks are needed.")
        return 0

    today = datetime.now(TIMEZONE).date()
    if today > END_DATE:
        state["expired"] = True
        save_state(state)
        log.info("The requested monitoring window has ended.")
        return 0

    if not state.get("initialized"):
        telegram_send(
            "✅ <b>VOX booking monitor is active.</b>\n\n"
            f"Watching {html.escape(MOVIE_TITLE)} at "
            f"{html.escape(CINEMA_NAME)} for showtimes from "
            "24 to 31 December 2026.\n"
            "I will send one alert when the first matching session appears."
        )
        state["initialized"] = True
        save_state(state)
        log.info("Sent activation confirmation to Telegram.")

    matches = asyncio.run(monitor_vox(debug=debug))

    if not matches:
        log.info(
            "No qualifying Mall of Egypt sessions found for 24–31 Dec 2026."
        )
        now = datetime.now(TIMEZONE)
        report_date = now.date().isoformat()

        # The workflow runs every five minutes; this catches its last run
        # before midnight Cairo time and sends no more than one clear report daily.
        if (
            now.hour == 23
            and now.minute >= 55
            and state.get("clear_status_date") != report_date
        ):
            telegram_send(
                "✅ <b>Daily update: still clear.</b>\n\n"
                f"No showtimes for {html.escape(MOVIE_TITLE)} at "
                f"{html.escape(CINEMA_NAME)} on 24–31 December 2026 "
                f"were showing by the end of {now.strftime('%A, %-d %B')} "
                "Africa/Cairo time."
            )
            state["clear_status_date"] = report_date
            save_state(state)
            log.info("Sent end-of-day clear update.")

        return 0

    telegram_send(build_alert(matches))
    state["alert_sent"] = True
    state["matches"] = matches
    state["alert_sent_at"] = datetime.now(TIMEZONE).isoformat(
        timespec="seconds"
    )
    save_state(state)
    log.info("Alert delivered; monitoring is now complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log.exception("Monitor run failed")
        raise
