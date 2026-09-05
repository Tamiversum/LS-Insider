import os
import asyncio
import requests
from playwright.async_api import async_playwright

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"


def send_to_discord(message):
    response = requests.post(
        WEBHOOK_URL,
        json={"content": message}
    )

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


async def get_rockstar_news():
    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="de-DE"
        )

        await page.goto(
            ROCKSTAR_URL,
            wait_until="networkidle",
            timeout=60000
        )

        await page.wait_for_timeout(5000)

        # Alle sichtbaren Überschriften auslesen
        headlines = await page.locator(
            "h1, h2, h3"
        ).all_inner_texts()

        await browser.close()

        return [
            headline.strip()
            for headline in headlines
            if headline.strip()
        ]


async def main():
    headlines = await get_rockstar_news()

    if headlines:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            "📰 **Aktuelle Rockstar-News:**\n\n"
        )

        for headline in headlines[:5]:
            message += f"• {headline}\n"

    else:
        message = (
            "🚗 **LS-Insider – Rockstar News**\n\n"
            "⚠️ Keine News gefunden."
        )

    send_to_discord(message)


if __name__ == "__main__":
    asyncio.run(main())
