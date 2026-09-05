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
                return

        except Exception:
            pass


async def find_latest_gta_article(page):
    articles = await page.locator(
        'a[href*="/newswire/article/"]'
    ).all()

    for article in articles:
        try:
            title = await article.inner_text()
            title = title.strip()

            link = await article.get_attribute("href")

            if not title or not link:
                continue

            if "/newswire/article/" not in link:
                continue

            # Wir suchen den neuesten GTA-Online-Artikel.
            if "GTA Online" in title:
                if link.startswith("/"):
                    link = "https://www.rockstargames.com" + link

                return title, link

        except Exception:
            pass

    return None, None


async def read_article(page, article_url):
    await page.goto(
        article_url,
        wait_until="domcontentloaded",
        timeout=60000
    )

    await page.wait_for_timeout(5000)

    await accept_cookies(page)

    await page.wait_for_timeout(3000)

    title = ""

    title_locator = page.locator("h1")

    if await title_locator.count() > 0:
        title = await title_locator.first.inner_text()

    title = title.strip()

    # Datum suchen
    date = ""

    body_text = await page.locator("body").inner_text()

    lines = [
        line.strip()
        for line in body_text.splitlines()
        if line.strip()
    ]

    for line in lines:
        if "September 2026" in line:
            date = line
            break

    # Überschriften sammeln
    headings = await page.locator(
        "h2, h3"
    ).all_inner_texts()

    clean_headings = []

    for heading in headings:
        heading = heading.strip()

        if (
            heading
            and heading not in clean_headings
            and len(heading) > 3
        ):
            clean_headings.append(heading)

    # Erste sinnvolle Absätze sammeln
    paragraphs = await page.locator(
        "p"
    ).all_inner_texts()

    clean_paragraphs = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if (
            paragraph
            and len(paragraph) > 40
            and paragraph not in clean_paragraphs
        ):
            clean_paragraphs.append(paragraph)

    return {
        "title": title,
        "date": date,
        "headings": clean_headings[:8],
        "paragraphs": clean_paragraphs[:5]
    }


async def get_latest_news():
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

        await accept_cookies(page)

        await page.wait_for_timeout(3000)

        title, article_url = await find_latest_gta_article(page)

        if not article_url:
            await browser.close()
            return None

        article = await read_article(
            page,
            article_url
        )

        article["url"] = article_url

        await browser.close()

        return article


async def main():
    article = await get_latest_news()

    if not article:
        message = (
            "🚗 **LS-Insider – GTA Online News**\n\n"
            "⚠️ Kein aktueller GTA-Online-Artikel gefunden."
        )

        send_to_discord(message)
        return

    message = (
        "🚗 **LS-Insider – GTA Online Eventwoche**\n\n"
        f"📰 **{article['title']}**\n\n"
    )

    if article["date"]:
        message += f"📅 **Datum:** {article['date']}\n\n"

    if article["paragraphs"]:
        message += "📝 **Zusammenfassung:**\n\n"

        # Nur die wichtigsten ersten Absätze
        summary = article["paragraphs"][0]

        if len(summary) > 900:
            summary = summary[:900] + "..."

        message += summary + "\n\n"

    if article["headings"]:
        message += "📌 **Artikel enthält:**\n"

        for heading in article["headings"][:6]:
            message += f"• {heading}\n"

        message += "\n"

    message += f"🔗 **Zum Rockstar-Artikel:**\n{article['url']}"

    # Discord-Nachrichten dürfen maximal 2000 Zeichen haben.
    if len(message) > 1950:
        message = message[:1940] + "\n..."

    send_to_discord(message)


if __name__ == "__main__":
    asyncio.run(main())
