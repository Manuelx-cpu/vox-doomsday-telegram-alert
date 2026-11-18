# VOX Avengers: Doomsday (Infinity Vision) Monitor

Official movie page monitored:
https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision

Target: Mall of Egypt, 24–31 December 2026.

## Android / GitHub setup
1. Create a repository on GitHub (or use your existing repository after removing old files).
2. Upload the ZIP contents, preserving folders.
3. Put `monitor.py`, `requirements.txt`, `state.json`, `.gitignore`, and `README.md` in the repository root.
4. Put `monitor.yml` exactly in `.github/workflows/monitor.yml`.
5. In the repository open Settings → Secrets and variables → Actions → New repository secret.
6. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` using the existing values. Do not put them in code or screenshots.
7. Open Actions → VOX Avengers Doomsday Monitor → Run workflow.
8. Open the run and inspect the `Run monitor` step. A successful run prints `Check complete`.
9. Keep only one active copy of the workflow to avoid duplicate alerts.

GitHub Actions runs on GitHub's cloud; your Android phone and PC do not need to stay on. The schedule is every 5 minutes, but GitHub can delay scheduled runs.

IMPORTANT: This is a fresh rebuild with HTTP/2 disabled and page-load retries. It still requires a successful live GitHub Actions test: VOX may change its page structure or date-selector behavior. Do not assume monitoring is validated until the first run succeeds and its log is checked.
