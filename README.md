# VOX Doomsday Telegram booking alert

This Python project checks the **VOX Egypt page dedicated to Avengers: Doomsday (Infinity Vision)** and sends one Telegram notification when it finds bookable showtimes at **Mall of Egypt** for **24–31 December 2026**.

It does not monitor the standard Doomsday listing or other VOX cinemas. It checks the date tabs individually and looks for actual session times inside the Mall of Egypt section.

## Hosting

The included GitHub Actions workflow runs the check every five minutes. It is suitable for a small personal monitor and does not require an always-on computer. GitHub documents five minutes as the shortest supported schedule interval; scheduled runs can be delayed during busy periods, so this is not a guaranteed real-time alert.

For the free scheduled-run model, create a **public GitHub repository** for this code. The Telegram token and chat ID must be saved only as GitHub Actions secrets; never put them in the source files. The repository contains no personal data or credentials.

## 1. Create your Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`, follow the prompts, and copy the bot token BotFather gives you.
3. Open the new bot using the link BotFather provides and press **Start**. Send `/start` in the chat.

## 2. Find your Telegram chat ID

On a computer with Python installed:

```bash
pip install requests
python get_chat_id.py
```

Paste the bot token when prompted. The script hides token input and prints the chat ID. If it says there are no updates, send `/start` to your bot and run it again.

## 3. Put the project on GitHub

1. Create a new **public** repository, for example `vox-doomsday-telegram-alert`.
2. Extract this project ZIP and upload its contents to the repository root. Keep the `.github/workflows/monitor.yml` folder structure intact.
3. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
4. Add these two secrets exactly:
   - `TELEGRAM_BOT_TOKEN` — the token from BotFather.
   - `TELEGRAM_CHAT_ID` — the chat ID from step 2.
5. Open **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**. Save.
6. Open the **Actions** tab, select **Monitor VOX Doomsday booking**, and choose **Run workflow** to test the setup.

You should receive a Telegram message confirming that the monitor is active. The scheduled checks then continue automatically.

## How the alert works

- Source: [VOX Avengers: Doomsday (Infinity Vision)](https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision)
- Venue: Mall of Egypt only.
- Dates: 24–31 December 2026 inclusive.
- Poll interval: five minutes, subject to GitHub Actions scheduling delays.
- Trigger: a date in the requested range has one or more session times listed under Mall of Egypt on the Infinity Vision-specific movie page.
- After the first match, the bot sends one alert and marks the monitor complete. It does not keep sending repeat alerts.
- The requested date window ends on 31 December 2026; checks stop after that date.

## Manual test / troubleshooting

- Check the latest run under **Actions**. The job log reports which date tabs were visible and whether any Mall of Egypt sessions were found.
- If Telegram does not receive the activation message, check that the two repository secrets are spelled correctly and that you pressed Start / sent `/start` to your bot.
- If VOX changes its page layout, the monitor may need its date-tab or showtime parsing updated. A failed or blocked page read is not treated as booking availability.
- You can run the script locally after installing the dependencies and Chromium:

```bash
pip install -r requirements.txt
python -m playwright install chromium
TELEGRAM_BOT_TOKEN="your-token" TELEGRAM_CHAT_ID="your-chat-id" python monitor.py --debug
```

Do not commit your token or chat ID to GitHub.
