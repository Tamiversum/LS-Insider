import os
import asyncio
import requests
import re
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

            if "GTA Online" in title:
                if link.startswith("/"):
                    link = "https://www.rockstargames.com" + link

                return link

        except Exception:
            pass

    return None


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

    body_text = await page.locator("body").inner_text()

    lines = [
        line.strip()
        for line in body_text.splitlines()
        if line.strip()
    ]

    # Datum erkennen
    date = ""

    date_pattern = re.compile(
        r"\b\d{1,2}\.\s+"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
        r"Oktober|November|Dezember)"
        r"\s+\d{4}\b"
    )

    for line in lines:
        match = date_pattern.search(line)

        if match:
            date = match.group(0)
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

    # Absätze sammeln
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
        "headings": clean_headings,
        "paragraphs": clean_paragraphs,
        "url": article_url
    }


def find_matching_items(article):
    """
    Sucht wichtige Informationen im Artikel.
    """

    text = " ".join(article["paragraphs"])
    text += " " + " ".join(article["headings"])

    results = {
        "money": [],
        "free": [],
        "vehicles": [],
        "discounts": [],
        "weekly": []
    }

    # GTA$ / RP
    money_keywords = [
        "GTA$",
        "GTA $",
        "RP",
        "Belohnungen",
        "Bonus",
        "dreifache Belohnungen",
        "doppelte Belohnungen"
    ]

    # Kostenlos
    free_keywords = [
        "kostenlos",
        "kostenfreie",
        "gratis",
        "kostenlose",
        "Geschenk"
    ]

    # Fahrzeuge
    vehicle_keywords = [
        "Fahrzeug",
        "SUV",
        "Sportwagen",
        "Motorrad",
        "Wagen",
        "Auto"
    ]

    # Rabatte
    discount_keywords = [
        "Rabatt",
        "Rabatte",
        "reduziert",
        "Preisnachlass"
    ]

    # Wocheninformationen
    weekly_keywords = [
        "3. September",
        "4. September",
        "5. September",
        "6. September",
        "7. September",
        "8. September",
        "9. September"
    ]

    for heading in article["headings"]:
        lower = heading.lower()

        if any(
            keyword.lower() in lower
            for keyword in money_keywords
        ):
            results["money"].append(heading)

        if any(
            keyword.lower() in lower
            for keyword in free_keywords
        ):
            results["free"].append(heading)

        if any(
            keyword.lower() in lower
            for keyword in vehicle_keywords
        ):
            results["vehicles"].append(heading)

        if any(
            keyword.lower() in lower
            for keyword in discount_keywords
        ):
            results["discounts"].append(heading)

        if any(
            keyword.lower() in lower
            for keyword in weekly_keywords
        ):
            results["weekly"].append(heading)

    return results


def add_unique(items, value):
    if value and value not in items:
        items.append(value)


def build_message(article):
    matches = find_matching_items(article)

    message = (
        "🚗 **LS-Insider – GTA Online Eventwoche**\n\n"
        f"📰 **{article['title']}**\n\n"
    )

    if article["date"]:
        message += (
            f"📅 **Veröffentlicht:** {article['date']}\n\n"
        )

    if article["paragraphs"]:
        summary = article["paragraphs"][0]

        if len(summary) > 700:
            summary = summary[:700] + "..."

        message += (
            "📝 **Kurz zusammengefasst:**\n"
            f"{summary}\n\n"
        )

    if matches["money"]:
        message += "💰 **GTA$ & RP:**\n"

        for item in matches["money"][:5]:
            message += f"• {item}\n"

        message += "\n"

    if matches["free"]:
        message += "🎁 **Kostenlos:**\n"

        for item in matches["free"][:5]:
            message += f"• {item}\n"

        message += "\n"

    if matches["vehicles"]:
        message += "🚗 **Fahrzeuge:**\n"

        for item in matches["vehicles"][:5]:
            message += f"• {item}\n"

        message += "\n"

    if matches["discounts"]:
        message += "🏷️ **Rabatte:**\n"

        for item in matches["discounts"][:5]:
            message += f"• {item}\n"

        message += "\n"

    message += (
        "📌 **Weitere Informationen:**\n"
    )

    for heading in article["headings"][:5]:
        message += f"• {heading}\n"

    message += (
        "\n🔗 **Zum Rockstar-Artikel:**\n"
        f"{article['url']}"
    )

    # Discord-Limit
    if len(message) > 1950:
        message = message[:1940] + "\n..."

    return message


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

        article_url = await find_latest_gta_article(
            page
        )

        if not article_url:
            await browser.close()
            return None

        article = await read_article(
            page,
            article_url
        )

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

    message = build_message(article)

    send_to_discord(message)


if __name__ == "__main__":
    asyncio.run(main())
