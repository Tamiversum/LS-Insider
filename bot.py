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
    response = requests.get(ROCKSTAR_URL)

    if response.status_code != 200:
        raise Exception(
            f"Rockstar-Seite konnte nicht geladen werden: {response.status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")

    links = soup.find_all("a")

    gta_article = None

    for link in links:
        text = link.get_text(" ", strip=True)

        if "GTA Online" in text and text:
            gta_article = text
            break

    if gta_article:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            f"📰 {gta_article}"
        )
    else:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            "⚠️ Kein GTA-Online-Artikel gefunden."
        )

    send_to_discord(message)


if __name__ == "__main__":
    main()
