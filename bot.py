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
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml"
        }
    )

    text = response.text

    # Suche nach möglichen API-Adressen im HTML
    keywords = [
        "api",
        "newswire",
        "graphql",
        "article"
    ]

    found = []

    for keyword in keywords:
        if keyword.lower() in text.lower():
            found.append(keyword)

    message = (
        "🔍 **LS-Insider – API-Diagnose**\n\n"
        f"HTTP-Status: `{response.status_code}`\n"
        f"Antwortlänge: `{len(text)}` Zeichen\n\n"
        f"Gefundene Begriffe: `{', '.join(found) if found else 'keine'}`\n\n"
        "📡 Rockstar lädt die eigentlichen News vermutlich über eine "
        "separate API."
    )

    send_to_discord(message)


if __name__ == "__main__":
    main()
