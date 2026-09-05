```python
import os
import asyncio
import requests
import re
from playwright.async_api import async_playwright

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"

LAST_ARTICLE_FILE = "last_article.txt"

DISCORD_LIMIT = 1950


def send_to_discord(message):
    response = requests.post(
        WEBHOOK_URL,
        json={"content": message},
        timeout=30
    )

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


def load_last_article():
    try:
        if os.path.exists(LAST_ARTICLE_FILE):
            with open(
                LAST_ARTICLE_FILE,
                "r",
                encoding="utf-8"
            ) as file:
                return file.read().strip()

    except Exception as error:
        print(
            f"⚠️ Konnte gespeicherten Artikel nicht lesen: {error}"
        )

    return ""


def save_last_article(article_url):
    try:
        with open(
            LAST_ARTICLE_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            file.write(article_url)

    except Exception as error:
        print(
            f"⚠️ Konnte Artikel nicht speichern: {error}"
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


def clean_text(text):
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def unique_items(items):
    result = []

    for item in items:
        item = clean_text(item)

        if item and item not in result:
            result.append(item)

    return result


async def find_latest_gta_article(page):
    articles = await page.locator(
        'a[href*="/newswire/article/"]'
    ).all()

    for article in articles:
        try:
            title = clean_text(
                await article.inner_text()
            )

            link = await article.get_attribute(
                "href"
            )

            if not title or not link:
                continue

            if "/newswire/article/" not in link:
                continue

            if "GTA Online" not in title:
                continue

            if link.startswith("/"):
                link = (
                    "https://www.rockstargames.com"
                    + link
                )

            return link

        except Exception:
            pass

    return None


def detect_date(lines):
    date_pattern = re.compile(
        r"\b\d{1,2}\.\s+"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
        r"Oktober|November|Dezember)"
        r"\s+\d{4}\b",
        re.IGNORECASE
    )

    for line in lines:
        match = date_pattern.search(line)

        if match:
            return match.group(0)

    return ""


def detect_event_period(text):
    patterns = [
        r"\b\d{1,2}\.\s*[–-]\s*\d{1,2}\.\s*"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
        r"Oktober|November|Dezember)\b",

        r"\b\d{1,2}\.\s*bis\s*\d{1,2}\.\s*"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
        r"Oktober|November|Dezember)\b"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            return match.group(0)

    return ""


def classify_section(heading, text):
    combined = (
        heading + " " + text
    ).lower()

    bonus_keywords = [
        "bonus",
        "boni",
        "doppelte belohnungen",
        "dreifache belohnungen",
        "2x",
        "3x",
        "doppelte gta$",
        "dreifache gta$",
        "doppelte rp",
        "dreifache rp",
        "gta$ und rp",
        "gta$ & rp",
        "mehr gta$",
        "mehr rp"
    ]

    if any(
        keyword in combined
        for keyword in bonus_keywords
    ):
        return "money"

    free_keywords = [
        "kostenlos",
        "kostenlose",
        "kostenfreien",
        "kostenfrei",
        "gratis",
        "geschenk",
        "geschenke",
        "gratis erhalten"
    ]

    if any(
        keyword in combined
        for keyword in free_keywords
    ):
        return "free"

    vehicle_keywords = [
        "fahrzeug",
        "fahrzeuge",
        "auto",
        "wagen",
        "motorrad",
        "motorräder",
        "suv",
        "sportwagen",
        "supersportwagen",
        "muscle-car",
        "muscle car"
    ]

    if any(
        keyword in combined
        for keyword in vehicle_keywords
    ):
        return "vehicles"

    discount_keywords = [
        "rabatt",
        "rabatte",
        "reduziert",
        "preisnachlass",
        "vergünstigt",
        "vergünstigungen",
        "% günstiger",
        "günstiger"
    ]

    if any(
        keyword in combined
        for keyword in discount_keywords
    ):
        return "discounts"

    return "other"


async def read_article(page, article_url):
    await page.goto(
        article_url,
        wait_until="domcontentloaded",
        timeout=60000
    )

    await page.wait_for_timeout(5000)

    await accept_cookies(page)

    await page.wait_for_timeout(2000)

    title = ""

    title_locator = page.locator("h1")

    if await title_locator.count() > 0:
        title = clean_text(
            await title_locator.first.inner_text()
        )

    body_text = await page.locator(
        "body"
    ).inner_text()

    lines = [
        clean_text(line)
        for line in body_text.splitlines()
        if clean_text(line)
    ]

    date = detect_date(lines)

    event_period = detect_event_period(
        body_text
    )

    sections = []

    heading_elements = await page.locator(
        "h2, h3"
    ).all()

    for heading_element in heading_elements:
        try:
            heading = clean_text(
                await heading_element.inner_text()
            )

            if not heading:
                continue

            text_parts = []

            current = heading_element

            for _ in range(15):
                current = current.locator(
                    "xpath=following-sibling::*[1]"
                )

                if await current.count() == 0:
                    break

                tag_name = await current.evaluate(
                    "(element) => element.tagName.toLowerCase()"
                )

                if tag_name in ("h2", "h3"):
                    break

                if tag_name in ("p", "li"):
                    text = clean_text(
                        await current.inner_text()
                    )

                    if text:
                        text_parts.append(text)

            section_text = clean_text(
                " ".join(text_parts)
            )

            if (
                section_text
                or len(heading) > 3
            ):
                sections.append({
                    "heading": heading,
                    "text": section_text
                })

        except Exception:
            pass

    if not sections:
        paragraphs = await page.locator(
            "p"
        ).all_inner_texts()

        clean_paragraphs = unique_items(
            paragraphs
        )

        for paragraph in clean_paragraphs:
            if len(paragraph) >= 40:
                sections.append({
                    "heading": "",
                    "text": paragraph
                })

    return {
        "title": title,
        "date": date,
        "event_period": event_period,
        "sections": sections,
        "url": article_url
    }


def classify_article(article):
    results = {
        "money": [],
        "free": [],
        "vehicles": [],
        "discounts": [],
        "other": []
    }

    for section in article["sections"]:
        category = classify_section(
            section["heading"],
            section["text"]
        )

        results[category].append(
            section
        )

    return results


def format_section(section):
    heading = clean_text(
        section.get("heading", "")
    )

    text = clean_text(
        section.get("text", "")
    )

    if heading and text:
        return (
            f"**{heading}**\n"
            f"{text}"
        )

    if heading:
        return f"**{heading}**"

    return text


def add_category(
    message,
    emoji,
    title,
    items,
    max_items=4
):
    if not items:
        return message

    message += (
        f"{emoji} **{title}**\n"
    )

    added = 0

    for item in items:
        formatted = format_section(
            item
        )

        if not formatted:
            continue

        if len(formatted) > 500:
            formatted = (
                formatted[:497]
                + "..."
            )

        message += (
            f"• {formatted}\n"
        )

        added += 1

        if added >= max_items:
            break

    message += "\n"

    return message


def build_message(article):
    matches = classify_article(
        article
    )

    title = article["title"]

    if not title:
        title = "GTA Online Eventwoche"

    message = (
        "🚗 **LS-Insider – "
        "GTA Online Eventwoche**\n\n"
        f"📰 **{title}**\n\n"
    )

    if article["event_period"]:
        message += (
            "📅 **Aktuelle Eventwoche**\n"
            f"• {article['event_period']}\n\n"
        )

    intro = ""

    for section in article["sections"]:
        if not section["text"]:
            continue

        category = classify_section(
            section["heading"],
            section["text"]
        )

        if category == "other":
            intro = section["text"]
            break

    if intro:
        if len(intro) > 600:
            intro = (
                intro[:597]
                + "..."
            )

        message += (
            "📝 **Kurz zusammengefasst**\n"
            f"{intro}\n\n"
        )

    message = add_category(
        message,
        "💰",
        "Boni & Belohnungen",
        matches["money"],
        5
    )

    message = add_category(
        message,
        "🎁",
        "Kostenlose Sachen",
        matches["free"],
        5
    )

    message = add_category(
        message,
        "🚗",
        "Fahrzeuge",
        matches["vehicles"],
        5
    )

    message = add_category(
        message,
        "🏷️",
        "Rabatte",
        matches["discounts"],
        5
    )

    ignored_headings = [
        "rockstar games",
        "gta online",
        "mehr erfahren",
        "mehr",
        "newsletter",
        "social media",
        "related articles"
    ]

    other_items = []

    for item in matches["other"]:
        heading = clean_text(
            item.get("heading", "")
        )

        if not heading:
            continue

        if heading.lower() in ignored_headings:
            continue

        other_items.append(
            heading
        )

    if other_items:
        message += (
            "🔎 **Weitere Highlights**\n"
        )

        for heading in other_items[:5]:
            message += (
                f"• {heading}\n"
            )

        message += "\n"

    if article["date"]:
        message += (
            f"📌 **Veröffentlicht:** "
            f"{article['date']}\n\n"
        )

    message += (
        "🔗 **Vollständiger "
        "Rockstar-Artikel:**\n"
        f"{article['url']}"
    )

    if len(message) > DISCORD_LIMIT:
        link = (
            "\n🔗 **Vollständiger "
            "Rockstar-Artikel:**\n"
            + article["url"]
        )

        available = (
            DISCORD_LIMIT
            - len(link)
            - 10
        )

        message = (
            message[:available]
            + "\n\n..."
            + link
        )

    return message


async def get_latest_news():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="de-DE"
        )

        try:
            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                3000
            )

            await accept_cookies(
                page
            )

            await page.wait_for_timeout(
                2000
            )

            article_url = (
                await find_latest_gta_article(
                    page
                )
            )

            if not article_url:
                return None

            article = await read_article(
                page,
                article_url
            )

            return article

        finally:
            await browser.close()


async def main():

    try:
        print(
            "🔎 Suche nach aktuellem "
            "GTA-Online-Artikel..."
        )

        article = await get_latest_news()

        if not article:
            message = (
                "🚗 **LS-Insider – "
                "GTA Online News**\n\n"
                "⚠️ Kein aktueller "
                "GTA-Online-Artikel gefunden."
            )

            send_to_discord(
                message
            )

            return

        article_url = article["url"]

        last_article = (
            load_last_article()
        )

        if article_url == last_article:
            print(
                "⏭️ Artikel wurde bereits "
                "gepostet. Nichts senden."
            )
            return

        message = build_message(
            article
        )

        send_to_discord(
            message
        )

        save_last_article(
            article_url
        )

        print(
            "✅ Neuer GTA-Online-Artikel "
            "erfolgreich an Discord gesendet."
        )

    except Exception as error:
        print(
            f"❌ Fehler: {error}"
        )

        raise


if __name__ == "__main__":
    asyncio.run(main())
```
