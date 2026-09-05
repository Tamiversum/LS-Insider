import os
import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_ARTICLE_URL = (
    "https://www.rockstargames.com/de/newswire/article/"
    "ak43aoa18a19o2/"
    "compete-across-entrepreneurial-endeavors-in-the-gta-online-business-ri"
)


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
        ROCKSTAR_ARTICLE_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    if response.status_code != 200:
        raise Exception(
            f"Rockstar-Artikel konnte nicht geladen werden: "
            f"{response.status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.find("h1")

    if title:
        title_text = title.get_text(" ", strip=True)
    else:
        title_text = "Kein Titel gefunden."

    message = (
        "🚗 **LS-Insider – Rockstar News**\n\n"
        f"📰 **{title_text}**"
    )

    send_to_discord(message)


if __name__ == "__main__":
    main()
