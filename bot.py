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


async def accept_cookies(page):
    cookie_buttons = [
        "Alle akzeptieren",
        "Akzeptieren",
        "Alle Cookies akzeptieren",
        "Accept All",
        "Accept"
    ]

    for button_text in cookie_buttons:
        try:
            button = page.get_by_text(
                button_text,
                exact=True
            )

            if await button.count() > 0:
                await button.first.click(timeout=3000)
                await page.wait_for_timeout(2000)
                return True

        except Exception:
            pass

    return False


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
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(3000)

        # Cookie-Hinweis automatisch akzeptieren
        await accept_cookies(page)

        # Rockstar danach kurz laden lassen
        await page.wait_for_timeout(5000)

        # Alle Newswire-Artikel suchen
        articles = await page.locator(
            'a[href*="/newswire/article/"]'
        ).all()

        news = []

        for article in articles:

            try:
                title = await article.inner_text()
                title = title.strip()

                link = await article.get_attribute("href")

                if not title or not link:
                    continue

                if "/newswire/article/" not in link:
                    continue

                if link.startswith("/"):
                    link = "https://www.rockstargames.com" + link

                # Nur GTA-Online-News berücksichtigen
                if (
                    "GTA Online" in title
                    or "GTA" in title
                    or "GTA Online" in link
                ):
                    if not any(
                        existing["title"] == title
                        for existing in news
                    ):
                        news.append({
                            "title": title,
                            "link": link
                        })

            except Exception:
                pass

        await browser.close()

        return news


async def main():
    news = await get_rockstar_news()

    if news:

        message = (
            "🚗 **LS-Insider – GTA Online News**\n\n"
        )

        for item in news[:5]:
            message += (
                f"📰 **{item['title']}**\n"
                f"🔗 {item['link']}\n\n"
            )

    else:

        message = (
            "🚗 **LS-Insider – GTA Online News**\n\n"
            "⚠️ Es wurden keine GTA-Online-News gefunden.\n\n"
            "Rockstar wurde erreicht, aber es konnten keine "
            "passenden Artikel ausgelesen werden."
        )

    send_to_discord(message)


if __name__ == "__main__":
    asyncio.run(main())
