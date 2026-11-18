import asyncio, json, os, re
from datetime import date, datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision"
START, END = date(2026, 12, 24), date(2026, 12, 31)
TOKEN, CHAT_ID = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
STATE = Path("state.json")
TIME_RE = re.compile(r"\\b(?:1[0-2]|0?[1-9])(?::[0-5]\\d)?\\s?(?:am|pm)\\b", re.I)

def read_state():
    try: return json.loads(STATE.read_text())
    except Exception: return {"alert_sent": False}

def write_state(s):
    STATE.write_text(json.dumps(s, indent=2) + "\\n")

async def send_telegram(message):
    import requests
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}, timeout=25)
    r.raise_for_status()
    if not r.json().get("ok"): raise RuntimeError("Telegram rejected the message")

async def main():
    state = read_state()
    matches = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-http2", "--disable-features=Http2"])
        try:
            page = await browser.new_page()
            last_error = None
            for attempt in range(3):
                try:
                    response = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    if response and response.status >= 400: raise RuntimeError(f"VOX HTTP {response.status}")
                    await page.wait_for_timeout(4500)
                    body = await page.locator("body").inner_text(timeout=20000)
                    if "Avengers: Doomsday (Infinity Vision)" not in body:
                        raise RuntimeError("Loaded page does not identify the Infinity Vision movie")
                    break
                except Exception as e:
                    last_error = e
                    if attempt == 2: raise RuntimeError(f"VOX page failed after 3 attempts: {last_error}")
                    await page.wait_for_timeout((attempt + 1) * 4000)

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
                    txt = await page.locator("body").inner_text()
                    date_seen = any(x.lower() in txt.lower() for x in labels)
                    # Only count times in the Mall of Egypt block, not elsewhere on the page.
                    mall = re.search(r"(?is)Mall of Egypt(.*?)(?:\\n\\s*\\n|$)", txt)
                    if date_seen and mall:
                        times = TIME_RE.findall(mall.group(1))
                        if times: matches.append((d, times))
                d = date.fromordinal(d.toordinal() + 1)
            await page.close()
        finally:
            await browser.close()

    if matches and not state.get("alert_sent"):
        lines = ["VOX showtime update detected!", "Avengers: Doomsday (Infinity Vision)",
                 "Mall of Egypt", ""]
        for day, times in matches:
            lines.append(f"{day.strftime('%A %d %B %Y')}: {', '.join(times)}")
        lines += ["", f"Official VOX page: {URL}"]
        await send_telegram("\\n".join(lines))
        state["alert_sent"] = True
        state["matching_dates"] = [d.isoformat() for d, _ in matches]
        state["status"] = "alert_sent"
    else:
        state["status"] = "no_confirmed_matching_showtimes"
    state["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
    write_state(state)
    print(f"Check complete: {state['status']}; matching dates: {len(matches)}")

if __name__ == "__main__":
    asyncio.run(main())
