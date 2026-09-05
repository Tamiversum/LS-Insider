import os
import requests
from bs4 import BeautifulSoup

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
    response = requests.get(
        ROCKSTAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    if response.status_code != 200:
        raise Exception(
            f"Rockstar-Seite konnte nicht geladen werden: {response.status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")

    # Wir suchen Überschriften der Newswire-Seite
    headlines = []

    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = heading.get_text(" ", strip=True)

        if text and text not in headlines:
            headlines.append(text)

    if headlines:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            "📰 **Gefundene News:**\n\n"
            + "\n".join(f"• {headline}" for headline in headlines[:5])
        )
    else:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            "⚠️ Rockstar hat keine Überschriften geliefert."
        )

    send_to_discord(message)


if __name__ == "__main__":
    main()
