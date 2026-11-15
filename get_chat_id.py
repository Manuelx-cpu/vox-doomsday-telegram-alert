#!/usr/bin/env python3
"""Find the private Telegram chat ID after the user sends /start to the bot."""

from getpass import getpass
import requests


def main() -> None:
    token = getpass("Paste your Telegram bot token (input hidden): ").strip()
    if not token:
        raise SystemExit("No token entered.")

    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram API error: {payload.get('description', payload)}")

    chats = {}
    for update in payload.get("result", []):
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
        )
        if not message or not message.get("chat"):
            continue
        chat = message["chat"]
        chats[str(chat["id"])] = {
            "id": str(chat["id"]),
            "type": chat.get("type", "unknown"),
            "title": chat.get("title") or chat.get("first_name") or "Telegram chat",
            "username": chat.get("username", ""),
        }

    if not chats:
        raise SystemExit(
            "No chat updates found. Open your bot in Telegram, press Start, send /start, "
            "then run this script again."
        )

    print("Chat ID(s) detected:")
    for chat in chats.values():
        print(f"  {chat['id']}  ({chat['type']})  {chat['title']}")
    print("\nUse your private-chat ID as the TELEGRAM_CHAT_ID GitHub secret.")


if __name__ == "__main__":
    main()
