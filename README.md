# VOX Avengers: Doomsday (Infinity Vision) Monitor

Watches https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision
for Mall of Egypt showtimes on 24-31 December 2026 and sends a Telegram alert
as soon as any appear.

## Files
- `monitor.py` - the checker (repo root)
- `requirements.txt`, `state.json` - repo root
- `.github/workflows/monitor.yml` - the schedule (every 10 minutes)

## Setup
1. Repository -> Settings -> Secrets and variables -> Actions.
2. Add secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never put them in code.
3. Actions -> VOX Avengers Doomsday Monitor -> Run workflow.
4. Open the run, check the "Run VOX monitor" step. Success prints `Check complete`.

## Behavior
- A page that fails to load (e.g. HTTP 403) fails the run. It is never treated as "no bookings".
- One alert when showtimes appear, then the monitor stops checking.
- One "still clear" Telegram message per day after 22:00 Cairo time.
- Stops automatically after 31 Dec 2026.
- `state.json` is only committed when something changes.

## Known limits
- GitHub may delay scheduled runs.
- VOX may block GitHub's IP ranges (403). If so, run `python monitor.py`
  from a home machine or VPS with the two secrets as environment variables.
- Date-tab and Mall of Egypt detection depend on VOX's page layout. Check the
  first run's log lines "Date tabs found" and "Mall block sample".