import os
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire"


def send_to_discord(message):
    response = requests.post(
        WEBHOOK_URL,
        json={"content": message}
    )

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


def main():
    response = requests.get(
        ROCKSTAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    # Wir zeigen nur technische Informationen an,
    # keine komplette Rockstar-Seite.
    text = response.text

    message = (
        "🔍 **LS-Insider – Rockstar Diagnose**\n\n"
        f"HTTP-Status: `{response.status_code}`\n"
        f"Antwortlänge: `{len(text)}` Zeichen\n\n"
        f"HTML enthält `GTA Online`: "
        f"`{'GTA Online' in text}`\n"
        f"HTML enthält `Business`: "
        f"`{'Business' in text}`\n"
        f"HTML enthält `Rockstar`: "
        f"`{'Rockstar' in text}`"
    )

    send_to_discord(message)


if __name__ == "__main__":
    main()
