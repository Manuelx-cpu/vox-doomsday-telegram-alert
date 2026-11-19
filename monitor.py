import asyncio, json, os, re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

URL = "https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision"
START, END = date(2026, 12, 24), date(2026, 12, 31)
CAIRO = ZoneInfo("Africa/Cairo")
TOKEN, CHAT_ID = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
STATE = Path("state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIME_RE = re.compile(r"\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s?(?:am|pm)\b", re.I)
MALL_RE = re.compile(r"(?is)Mall of Egypt(.*?)(?:\n\s*\n|$)")


def read_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"alert_sent": False}


def write_state(s):
    STATE.write_text(json.dumps(s, indent=2) + "\n")


def send_telegram(message):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True},
        timeout=25,
    )
    r.raise_for_status()
    if not r.json().get("ok"):
        raise RuntimeError("Telegram rejected the message")


async def load_page(page):
    last_error = None
    for attempt in range(3):
        try:
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            if resp and resp.status >= 400:
                raise RuntimeError(
                    f"VOX HTTP {resp.status} (server: {resp.headers.get('server', '?')})")
            await page.wait_for_timeout(4500)
            body = await page.locator("body").inner_text(timeout=20000)
            if "infinity vision" not in body.lower():
                raise RuntimeError("Loaded page does not identify the Infinity Vision movie")
            return
        except Exception as e:
            last_error = e
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await page.wait_for_timeout(20000 * (attempt + 1))
    raise RuntimeError(f"VOX page failed after 3 attempts: {last_error}")


async def scan():
    matches, clicked_days = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-http2", "--disable-features=Http2"])
        try:
            context = await browser.new_context(
                user_agent=UA, locale="en-US", timezone_id="Africa/Cairo",
                viewport={"width": 1366, "height": 768})
            page = await context.new_page()
            await load_page(page)

            d = START
            while d <= END:
                labels = [d.strftime("%a %d Dec"), d.strftime("%a %-d Dec"),
                          d.strftime("%d Dec"), d.strftime("%-d Dec")]
                clicked = False
                for label in labels:
                    try:
                        loc = page.get_by_text(label, exact=True).first
                        if await loc.count() and await loc.is_visible():
                            await loc.click(timeout=5000)
                            await page.wait_for_timeout(1800)
                            clicked = True
                            break
                    except Exception:
                        pass
                if clicked:
                    clicked_days.append(d.isoformat())
                    txt = await page.locator("body").inner_text()
                    mall = MALL_RE.search(txt)
                    if mall:
                        times = TIME_RE.findall(mall.group(1))
                        print(f"{d}: Mall block sample: {mall.group(1)[:200]!r}")
                        if times:
                            matches.append((d, times))
                d = date.fromordinal(d.toordinal() + 1)
        finally:
            await browser.close()
    print(f"Date tabs found: {clicked_days or 'none'}")
    return matches


async def main():
    state = read_state()
    now = datetime.now(CAIRO)

    if state.get("alert_sent") or now.date() > END:
        print("Done: alert already sent or date range over. Nothing to check.")
        return

    matches = await scan()
    old = json.dumps(state, sort_keys=True)

    if matches:
        lines = ["VOX showtimes are up!", "Avengers: Doomsday (Infinity Vision)",
                 "Mall of Egypt", ""]
        for day, times in matches:
            lines.append(f"{day.strftime('%A %d %B %Y')}: {', '.join(times)}")
        lines += ["", f"Book here: {URL}"]
        send_telegram("\n".join(lines))
        state.update(alert_sent=True, status="alert_sent",
                     matching_dates=[d.isoformat() for d, _ in matches])
    else:
        state["status"] = "no_confirmed_matching_showtimes"
        today = now.date().isoformat()
        if now.hour >= 22 and state.get("last_clear_date") != today:
            send_telegram("Still clear: no Doomsday Infinity Vision showtimes "
                          f"for 24-31 Dec at Mall of Egypt yet ({today}).")
            state["last_clear_date"] = today

    if json.dumps(state, sort_keys=True) != old:
        write_state(state)
    print(f"Check complete: {state['status']}; matching dates: {len(matches)}")


if __name__ == "__main__":
    asyncio.run(main())