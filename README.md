# VOX Doomsday Telegram booking alert

Watches the VOX Egypt showtimes page for **Avengers: Doomsday – Infinity Vision at Mall of Egypt** and messages you on Telegram when a showtime for **24–31 December 2026** appears:

<https://egy.voxcinemas.com/showtimes?c=mall-of-egypt&m=avengers-doomsday-infinity-vision>

It checks only Mall of Egypt and only those eight dates. It runs on GitHub Actions every five minutes (GitHub may delay scheduled runs, so alerts are not guaranteed to be instant).

## What you receive

| Message | When |
| --- | --- |
| **Monitor is active** | Once, after the first check in which the VOX page really loaded. |
| **Showtimes are live** | As soon as a matching showtime is found: date, times, cinema and a link. Monitoring then stops. |
| **Still clear** | At most once per Cairo calendar day, from 22:30 Cairo time, if the page was checked successfully and nothing matched. |

Monitoring also stops after 31 December 2026 (Cairo time).

## Failures are never "no showtimes"

If VOX cannot be loaded (timeout, HTTP error, block or challenge page) or the page layout is not understood, the run **fails** and logs diagnostics. No "active" or "still clear" message is sent from a failed check.

## Setup

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather), open it and press **Start**.
2. Find your chat ID with `python get_chat_id.py` (the token prompt is hidden).
3. In the repository: **Settings → Secrets and variables → Actions**, add the secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never commit them.
4. **Settings → Actions → General → Workflow permissions**: allow read and write (the workflow commits `state.json`).
5. **Actions → Monitor VOX Doomsday showtimes → Run workflow**. Tick `debug` to print the VOX page text per date.

## Reading the run log

- `VOX reachability: HTTP … | remote_ip=… | elapsed=…` shows whether the runner can reach VOX at all.
- `Response after …s: HTTP …` and `VOX page recognised` confirm that the browser loaded the page.
- `Date tabs visible: …` lists the dates VOX exposes; `MATCH …` lines are found showtimes.

## Files

- `monitor.py`: the checker (Playwright Chromium, `--disable-http2`).
- `.github/workflows/monitor.yml`: schedule, diagnostics, state commit.
- `state.json`: which messages were already sent; committed by the workflow.

## Local run

```bash
pip install -r requirements.txt
python -m playwright install chromium
TELEGRAM_BOT_TOKEN="…" TELEGRAM_CHAT_ID="…" python monitor.py --debug
```
