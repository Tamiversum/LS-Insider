import os
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"


def send_to_discord(message):
    data = {
        "content": message
    }

    response = requests.post(WEBHOOK_URL, json=data)

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


def main():
    response = requests.get(ROCKSTAR_URL)

    if response.status_code != 200:
        raise Exception(
            f"Rockstar-Seite konnte nicht geladen werden: {response.status_code}"
        )

    message = (
        "🚗 **LS-Insider – Verbindungstest**\n\n"
        "✅ Die offizielle deutsche Rockstar-News-Seite wurde erfolgreich erreicht!"
    )

    send_to_discord(message)


if __name__ == "__main__":
    main()
