import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# ============================================================
# EINSTELLUNGEN
# ============================================================

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

GTABASE_URL = (
    "https://www.gtabase.com/"
    "gta-online/weekly-update-bonuses-discounts"
)

ROCKSTAR_URL = (
    "https://www.rockstargames.com/"
    "de/newswire?tag_id=735"
)

STATE_FILE = "weekly_state.json"

VIENNA = ZoneInfo("Europe/Vienna")

# Discord erlaubt maximal 2000 Zeichen.
# Etwas Reserve lassen.
DISCORD_LIMIT = 1900

# WICHTIG:
# Für die momentanen Tests auf True lassen.
TEST_MODE = True


# ============================================================
# ALLGEMEINE HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)

    return text.strip()


def clean_single_line(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def unique_items(items):
    result = []
    seen = set()

    for item in items:
        item = clean_single_line(item)

        if not item:
            continue

        key = item.lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def make_signature(*parts):
    content = "||".join(
        clean_single_line(str(part))
        for part in parts
        if part is not None
    )

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception:
        return {}


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )


def contains_any(text, words):
    text = text.lower()

    return any(
        word.lower() in text
        for word in words
    )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not message:
        return

    chunks = []

    while len(message) > DISCORD_LIMIT:
        split_at = message.rfind(
            "\n",
            0,
            DISCORD_LIMIT
        )

        if split_at <= 0:
            split_at = DISCORD_LIMIT

        chunks.append(
            message[:split_at]
        )

        message = message[split_at:].lstrip()

    if message:
        chunks.append(message)

    for chunk in chunks:
        response = requests.post(
            WEBHOOK_URL,
            json={
                "content": chunk
            },
            timeout=30
        )

        response.raise_for_status()


# ============================================================
# ÜBERSETZUNGEN
# ============================================================

MONTHS = {
    "january": "Januar",
    "february": "Februar",
    "march": "März",
    "april": "April",
    "may": "Mai",
    "june": "Juni",
    "july": "Juli",
    "august": "August",
    "september": "September",
    "october": "Oktober",
    "november": "November",
    "december": "Dezember",
}


def translate_dates(text):
    if not text:
        return text

    for english, german in MONTHS.items():
        text = re.sub(
            rf"\b{english}\b",
            german,
            text,
            flags=re.IGNORECASE
        )

    return text


def format_period(text):
    """
    Beispiele:

    September 10-16
    ->
    10. – 16. September 2026

    September 10, 2026
    ->
    10. September 2026
    """

    if not text:
        return ""

    value = clean_single_line(text)

    current_year = datetime.now(
        VIENNA
    ).year

    # --------------------------------------------------------
    # September 10-16
    # September 10 - 16
    # --------------------------------------------------------

    match = re.search(
        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{1,2})"
        r"\s*[-–]\s*"
        r"(\d{1,2})"
        r"(?:,\s*(\d{4}))?"
        r"\b",
        value,
        flags=re.IGNORECASE
    )

    if match:
        month = MONTHS[
            match.group(1).lower()
        ]

        day_start = int(
            match.group(2)
        )

        day_end = int(
            match.group(3)
        )

        year = (
            int(match.group(4))
            if match.group(4)
            else current_year
        )

        return (
            f"{day_start}. – "
            f"{day_end}. "
            f"{month} "
            f"{year}"
        )

    # --------------------------------------------------------
    # September 10, 2026
    # --------------------------------------------------------

    match = re.search(
        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{1,2})"
        r",\s*"
        r"(\d{4})"
        r"\b",
        value,
        flags=re.IGNORECASE
    )

    if match:
        month = MONTHS[
            match.group(1).lower()
        ]

        day = int(
            match.group(2)
        )

        year = int(
            match.group(3)
        )

        return (
            f"{day}. "
            f"{month} "
            f"{year}"
        )

    return translate_dates(value)


def translate_text(text):
    if not text:
        return ""

    value = clean_single_line(text)

    exact_translations = {
        (
            "Compete Across Entrepreneurial Endeavors "
            "in the GTA Online Business Rivalries Event"
        ):
            "Messt euch im Event „Business Rivalries“ "
            "bei unternehmerischen Herausforderungen",

        (
            "GTA Online 3. September 2026 "
            "Messt euch im Event „Business Rivalries“ "
            "bei unternehmerischen Herausforderungen"
        ):
            "GTA Online – 3. September 2026: "
            "Messt euch im Event „Business Rivalries“ "
            "bei unternehmerischen Herausforderungen",

        (
            "GTA Online 10. September 2026 "
            "GTA+ Members Enjoy One Week of Early Access "
            "to the New Pegassi Horus Supercar"
        ):
            "GTA Online – 10. September 2026: "
            "GTA+-Mitglieder erhalten eine Woche Vorabzugang "
            "zum neuen Pegassi Horus",

        (
            "GTA+ Members Enjoy One Week of Early Access "
            "to the New Pegassi Horus Supercar"
        ):
            "GTA+-Mitglieder erhalten eine Woche Vorabzugang "
            "zum neuen Pegassi Horus",
    }

    if value in exact_translations:
        return exact_translations[value]

    replacements = [
        (
            "GTA Online Weekly Update",
            "GTA Online Eventwoche"
        ),
        (
            "Weekly Update",
            "Eventwoche"
        ),
        (
            "Bonuses",
            "Boni"
        ),
        (
            "Bonus",
            "Bonus"
        ),
        (
            "Discounts",
            "Rabatte"
        ),
        (
            "Discount",
            "Rabatt"
        ),
        (
            "New Vehicle",
            "Neues Fahrzeug"
        ),
        (
            "New Vehicles",
            "Neue Fahrzeuge"
        ),
        (
            "Supercar",
            "Supersportwagen"
        ),
        (
            "Motorcycle",
            "Motorrad"
        ),
        (
            "Vehicle",
            "Fahrzeug"
        ),
        (
            "Early Access",
            "Vorabzugang"
        ),
        (
            "Reward",
            "Belohnung"
        ),
        (
            "Rewards",
            "Belohnungen"
        ),
        (
            "Weekly Challenge",
            "Wöchentliche Herausforderung"
        ),
        (
            "FREE",
            "KOSTENLOS"
        ),
        (
            "Free",
            "Kostenlos"
        ),
        (
            "Gift",
            "Geschenk"
        ),
        (
            "Gifts",
            "Geschenke"
        ),
    ]

    for english, german in replacements:
        value = re.sub(
            re.escape(english),
            german,
            value,
            flags=re.IGNORECASE
        )

    value = translate_dates(value)

    return value


# ============================================================
# GTABASE – WOCHENINFORMATIONEN
# ============================================================

def find_weekly_title(text):
    patterns = [
        r"GTA Online Weekly Update.*",
        r"GTA Online Eventwoche.*",
        r"Weekly Update.*"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            return clean_single_line(
                match.group(0)
            )

    return (
        "GTA Online Eventwoche – "
        "Boni & Rabatte"
    )


def extract_bonus_items(text):
    items = []

    bonus_patterns = [
        (
            r"Community\s+(?:Mission\s+)?Series"
            r".{0,80}?3\s*[xX]",
            "Community Series – 3x GTA$ & RP"
        ),
        (
            r"Biker\s+Clubhouse\s+Contracts"
            r".{0,100}?2\s*[xX]",
            "Biker-Clubhaus-Aufträge – 2x GTA$ & RP"
        ),
        (
            r"Clubhouse\s+Contracts"
            r".{0,100}?2\s*[xX]",
            "Biker-Clubhaus-Aufträge – 2x GTA$ & RP"
        ),
        (
            r"MC\s+Work"
            r".{0,100}?2\s*[xX]",
            "MC-Arbeiten – 2x GTA$ & RP"
        ),
        (
            r"MC\s+Challenges"
            r".{0,100}?2\s*[xX]",
            "MC-Herausforderungen – 2x GTA$ & RP"
        ),
        (
            r"Street\s+Dealer\s+Sales"
            r".{0,100}?2\s*[xX]",
            "Street-Dealer-Verkäufe – 2x GTA$"
        ),
        (
            r"Bike\s+Service\s+Missions"
            r".{0,100}?2\s*[xX]",
            "Bike-Service-Missionen – 2x GTA$"
        ),
        (
            r"Hasta\s+La\s+Vista"
            r".{0,100}?2\s*[xX]",
            "Hasta La Vista – 2x GTA$ & RP"
        ),
        (
            r"Every\s+Bullet\s+Counts"
            r".{0,100}?2\s*[xX]",
            "Every Bullet Counts – 2x GTA$ & RP"
        ),
    ]

    for pattern, result in bonus_patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        ):
            items.append(result)

    return unique_items(items)


def extract_free_items(text):
    items = []

    # --------------------------------------------------------
    # Grapeseed Clubhouse
    # --------------------------------------------------------

    if re.search(
        r"Grapeseed\s+Clubhouse"
        r".{0,150}?"
        r"(?:FREE|free|KOSTENLOS)",
        text,
        flags=re.IGNORECASE
    ):
        items.append(
            "Grapeseed Clubhouse – KOSTENLOS"
        )

    # --------------------------------------------------------
    # Penaud La Coureuse
    # --------------------------------------------------------

    if re.search(
        r"Penaud\s+La\s+Coureuse"
        r".{0,250}?"
        r"(?:FREE|free|KOSTENLOS)",
        text,
        flags=re.IGNORECASE
    ):
        items.append(
            "Penaud La Coureuse – kostenlos "
            "durch die wöchentliche Herausforderung"
        )

    # --------------------------------------------------------
    # Allgemeine kostenlose Fahrzeuge/Gegenstände
    # --------------------------------------------------------

    patterns = [
        (
            r"([A-Z][A-Za-z0-9' -]{2,50})"
            r".{0,80}?"
            r"(?:FREE|free)",
        )
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches[:10]:
            value = clean_single_line(match)

            if (
                "GTA Online" not in value
                and len(value) > 3
                and len(value) < 80
            ):
                if value.lower() not in {
                    "weekly challenge",
                    "members",
                    "access",
                }:
                    items.append(
                        f"{value} – KOSTENLOS"
                    )

    return unique_items(items)


def extract_vehicle_items(text):
    items = []

    vehicle_names = [
        "S95",
        "Nimbus",
        "Vindicator",
        "Baller ST",
        "Cheetah Classic",
        "Vivanite",
        "Penumbra FF",
        "Patriot Stretch",
        "Shinobi",
        "Vortex",
        "Growler",
        "Defiler",
        "Aleutian",
        "Warrener HKR",
        "Rampant Rocket Tricycle",
        "Rampant Rocket",
    ]

    for vehicle in vehicle_names:
        if re.search(
            rf"\b{re.escape(vehicle)}\b",
            text,
            flags=re.IGNORECASE
        ):
            items.append(vehicle)

    return unique_items(items)


def extract_discount_items(text):
    items = []

    discount_patterns = [
        r"([A-Z][A-Za-z0-9' -]{2,50})"
        r".{0,80}?"
        r"[-–]?\s*\d{1,2}%\s*(?:OFF|discount)",

        r"([A-Z][A-Za-z0-9' -]{2,50})"
        r".{0,80}?"
        r"\d{1,2}%\s*Rabatt",
    ]

    for pattern in discount_patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches:
            value = clean_single_line(match)

            if (
                3 <= len(value) <= 70
                and value.lower()
                not in {
                    "gta online",
                    "this week",
                    "weekly update",
                }
            ):
                items.append(value)

    return unique_items(items)[:20]


def extract_weekly_challenge(text):
    patterns = [
        r"Weekly Challenge.{0,300}",
        r"wöchentliche Herausforderung.{0,300}",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            value = clean_single_line(
                match.group(0)
            )

            value = translate_text(value)

            return value

    # Aktueller allgemeiner Fallback
    if re.search(
        r"selling all kinds of product",
        text,
        flags=re.IGNORECASE
    ):
        return (
            "Verdiene GTA$1.000.000 durch den Verkauf "
            "aller Arten von Waren und erhalte zusätzlich "
            "GTA$1.000.000 sowie den Junk Tracksuit."
        )

    return ""


async def get_gtabase_data():
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        page = await browser.new_page()

        try:
            await page.goto(
                GTABASE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(5000)

            raw_text = await page.locator(
                "body"
            ).inner_text()

            text = clean_text(raw_text)

            period_match = re.search(
                r"(January|February|March|April|May|June|July|"
                r"August|September|October|November|December)"
                r"\s+\d{1,2}"
                r"(?:\s*[-–]\s*\d{1,2})?"
                r"(?:,\s*\d{4})?",
                text,
                flags=re.IGNORECASE
            )

            period = ""

            if period_match:
                period = format_period(
                    period_match.group(0)
                )

            title = translate_text(
                find_weekly_title(text)
            )

            bonuses = extract_bonus_items(
                text
            )

            free_items = extract_free_items(
                text
            )

            vehicles = extract_vehicle_items(
                text
            )

            discounts = extract_discount_items(
                text
            )

            weekly_challenge = (
                extract_weekly_challenge(text)
            )

            return {
                "period": period,
                "title": title,
                "bonuses": bonuses,
                "free_items": free_items,
                "vehicles": vehicles,
                "discounts": discounts,
                "weekly_challenge": weekly_challenge,
            }

        finally:
            await browser.close()


# ============================================================
# MITTWOCH – DIE WOCHENZEITUNG
# ============================================================

def build_wednesday_message(data):
    period = data.get(
        "period",
        ""
    )

    title = data.get(
        "title",
        "GTA Online Eventwoche – Boni & Rabatte"
    )

    bonuses = data.get(
        "bonuses",
        []
    )

    free_items = data.get(
        "free_items",
        []
    )

    vehicles = data.get(
        "vehicles",
        []
    )

    discounts = data.get(
        "discounts",
        []
    )

    weekly_challenge = data.get(
        "weekly_challenge",
        ""
    )

    sections = []

    # --------------------------------------------------------
    # Kopf
    # --------------------------------------------------------

    sections.append(
        "🗞️ **LS-INSIDER**\n\n"
        "# **DIESE WOCHE IN LOS SANTOS**\n\n"
        f"**{period}**"
    )

    # --------------------------------------------------------
    # EVENT
    # --------------------------------------------------------

    sections.append(
        "📰 **EVENT**\n"
        f"{title}"
    )

    # --------------------------------------------------------
    # BONI
    # --------------------------------------------------------

    if bonuses:
        bonus_text = "\n".join(
            f"• {item}"
            for item in bonuses
        )

        sections.append(
            "💰 **BONI**\n"
            f"{bonus_text}"
        )

    # --------------------------------------------------------
    # GESCHENKE
    # --------------------------------------------------------

    if free_items:
        free_text = "\n".join(
            f"• {item}"
            for item in free_items
        )

        sections.append(
            "🎁 **GESCHENKE FÜR DIE COMMUNITY**\n"
            f"{free_text}"
        )

    # --------------------------------------------------------
    # FAHRZEUGE
    # --------------------------------------------------------

    if vehicles:
        vehicle_text = "\n".join(
            f"• {item}"
            for item in vehicles
        )

        sections.append(
            "🚗 **NEU AUF DEN STRASSEN**\n"
            f"{vehicle_text}"
        )

    # --------------------------------------------------------
    # RABATTE
    # --------------------------------------------------------

    if discounts:
        discount_text = "\n".join(
            f"• {item}"
            for item in discounts
        )

        sections.append(
            "🏷️ **SONDERANGEBOTE**\n"
            f"{discount_text}"
        )

    # --------------------------------------------------------
    # WEEKLY CHALLENGE
    # --------------------------------------------------------

    if weekly_challenge:
        sections.append(
            "🏆 **WEEKLY CHALLENGE**\n"
            f"• {weekly_challenge}"
        )

    # --------------------------------------------------------
    # AKTUELLE WOCHE
    # --------------------------------------------------------

    if period:
        sections.append(
            "📅 **AKTUELLE WOCHE**\n"
            f"• {period}"
        )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    sections.append(
        "🔗 **VOLLSTÄNDIGER LINK**\n"
        f"<{GTABASE_URL}>"
    )

    return "\n\n".join(
        sections
    )


# ============================================================
# ROCKSTAR NEWSWIRE
# ============================================================

def extract_date_from_text(text):
    if not text:
        return None

    patterns = [
        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{1,2})"
        r",\s*"
        r"(\d{4})"
        r"\b",

        r"\b"
        r"(\d{1,2})\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{4})"
        r"\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        try:
            if match.group(1).isdigit():
                day = int(match.group(1))
                month = match.group(2)
                year = int(match.group(3))
            else:
                month = match.group(1)
                day = int(match.group(2))
                year = int(match.group(3))

            month_number = {
                name.lower(): number
                for number, name in enumerate(
                    [
                        "",
                        "January",
                        "February",
                        "March",
                        "April",
                        "May",
                        "June",
                        "July",
                        "August",
                        "September",
                        "October",
                        "November",
                        "December",
                    ]
                )
            }[month.lower()]

            return datetime(
                year,
                month_number,
                day
            )

        except Exception:
            continue

    return None


async def extract_article_content(page):
    """
    Holt bewusst NICHT mehr den kompletten <body>.
    Dadurch werden Navigation, Footer, Cookie-Texte usw.
    nicht mehr als Artikelinhalt ausgewertet.
    """

    selectors = [
        "article",
        "main article",
        '[data-testid="article"]',
        "main",
    ]

    for selector in selectors:

        locator = page.locator(
            selector
        )

        try:
            count = await locator.count()
        except Exception:
            count = 0

        for index in range(count):

            container = locator.nth(index)

            try:
                elements = await container.locator(
                    "h1, h2, h3, p"
                ).all_inner_texts()

                parts = []

                for element in elements:
                    value = clean_single_line(
                        element
                    )

                    if not value:
                        continue

                    parts.append(value)

                parts = unique_items(parts)

                result = "\n".join(parts)

                if len(result) >= 150:
                    return result

            except Exception:
                continue

    # --------------------------------------------------------
    # Fallback:
    # Nur Überschriften und Absätze der Seite,
    # niemals mehr den kompletten Body.
    # --------------------------------------------------------

    try:
        elements = await page.locator(
            "h1, h2, h3, p"
        ).all_inner_texts()

        parts = []

        for element in elements:
            value = clean_single_line(
                element
            )

            if value:
                parts.append(value)

        return "\n".join(
            unique_items(parts)
        )

    except Exception:
        return ""


async def get_rockstar_news():
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        page = await browser.new_page()

        try:
            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(5000)

            links = await page.locator(
                "a[href*='/newswire/']"
            ).all()

            candidates = []

            for link in links:

                try:
                    href = await link.get_attribute(
                        "href"
                    )

                    title = await link.inner_text()

                    title = clean_single_line(
                        title
                    )

                    if not href or not title:
                        continue

                    if href.startswith("/"):
                        href = (
                            "https://www.rockstargames.com"
                            + href
                        )

                    combined = (
                        title
                        + " "
                        + href
                    ).lower()

                    # ------------------------------------------------
                    # Nur relevante GTA-Online-Meldungen.
                    # ------------------------------------------------

                    relevant = contains_any(
                        combined,
                        [
                            "gta online",
                            "gta+",
                            "pegassi",
                            "business rivalries",
                            "los santos",
                            "gta-online",
                        ]
                    )

                    if not relevant:
                        continue

                    # ------------------------------------------------
                    # Datum aus Link/Umfeld suchen.
                    # ------------------------------------------------

                    date = extract_date_from_text(
                        title
                    )

                    if date is None:

                        try:
                            parent_text = clean_single_line(
                                await link.locator(
                                    "xpath=.."
                                ).inner_text()
                            )

                            date = extract_date_from_text(
                                parent_text
                            )

                        except Exception:
                            date = None

                    candidates.append(
                        {
                            "title": title,
                            "url": href,
                            "date": date,
                        }
                    )

                except Exception:
                    continue

            if not candidates:
                return None

            # --------------------------------------------------------
            # Neueste Meldung zuerst.
            # --------------------------------------------------------

            candidates.sort(
                key=lambda item: (
                    item["date"]
                    or datetime(
                        1970,
                        1,
                        1
                    )
                ),
                reverse=True
            )

            latest = candidates[0]

            # --------------------------------------------------------
            # Artikel öffnen
            # --------------------------------------------------------

            article_page = await browser.new_page()

            try:
                await article_page.goto(
                    latest["url"],
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                await article_page.wait_for_timeout(
                    3000
                )

                article_text = (
                    await extract_article_content(
                        article_page
                    )
                )

            finally:
                await article_page.close()

            clean_title = clean_single_line(
                latest["title"]
            )

            clean_title = translate_text(
                clean_title
            )

            return {
                "title": clean_title,
                "original_title": clean_single_line(
                    latest["title"]
                ),
                "url": latest["url"],
                "date": latest["date"],
                "body": clean_text(
                    article_text
                ),
            }

        finally:
            await browser.close()


# ============================================================
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

def detect_thursday_categories(
    news,
    previous_state
):
    title = clean_single_line(
        news.get("title", "")
    )

    body = clean_single_line(
        news.get("body", "")
    )

    url = news.get(
        "url",
        ""
    )

    combined = (
        title
        + " "
        + body
    ).lower()

    categories = []

    previous_url = previous_state.get(
        "rockstar_url"
    )

    previous_signature = previous_state.get(
        "rockstar_signature"
    )

    current_signature = make_signature(
        url,
        title,
        body
    )

    # --------------------------------------------------------
    # NEU / ÄNDERUNG
    # --------------------------------------------------------

    if not previous_url:
        categories.append(
            "🆕 **NEU**\n"
            "Eine neue Meldung ist eingetroffen."
        )

    elif previous_url != url:
        categories.append(
            "🆕 **NEU**\n"
            "Eine neue Rockstar-Meldung wurde entdeckt."
        )

    elif previous_signature != current_signature:
        categories.append(
            "⚠️ **ÄNDERUNG**\n"
            "Eine bestehende Meldung wurde aktualisiert."
        )

    # --------------------------------------------------------
    # BONUS
    #
    # Bewusst nur echte Bonusbegriffe.
    # Nicht mehr einfach jedes Auftauchen von "free",
    # "GTA+" usw. als Bonus werten.
    # --------------------------------------------------------

    bonus_patterns = [
        r"\b2x\b",
        r"\b3x\b",
        r"\b4x\b",
        r"\b5x\b",
        r"\b6x\b",
        r"\bbonus\b",
        r"\bbonuses\b",
        r"\bdouble rewards\b",
        r"\btriple rewards\b",
        r"\bgta\$.*rp\b",
    ]

    if any(
        re.search(
            pattern,
            combined,
            flags=re.IGNORECASE
        )
        for pattern in bonus_patterns
    ):
        categories.append(
            "💰 **NEUER BONUS**\n"
            "In der Meldung wurden neue Boni entdeckt."
        )

    # --------------------------------------------------------
    # FAHRZEUG
    # --------------------------------------------------------

    vehicle_patterns = [
        r"\bnew vehicle\b",
        r"\bnew car\b",
        r"\bnew supercar\b",
        r"\bsupercar\b",
        r"\bvehicle\b",
        r"\bpegassi\b",
        r"\bhorus\b",
        r"\bcar\b",
        r"\bmotorcycle\b",
    ]

    if any(
        re.search(
            pattern,
            combined,
            flags=re.IGNORECASE
        )
        for pattern in vehicle_patterns
    ):
        categories.append(
            "🚗 **NEUE FAHRZEUGMELDUNG**\n"
            "Eine neue Fahrzeugmeldung wurde entdeckt."
        )

    # --------------------------------------------------------
    # BELOHNUNG
    #
    # "free" allein reicht NICHT.
    # --------------------------------------------------------

    reward_patterns = [
        r"\breward\b",
        r"\brewards\b",
        r"\bgift\b",
        r"\bgifts\b",
        r"\bfree vehicle\b",
        r"\bfree car\b",
        r"\bfree item\b",
        r"\bfree outfit\b",
        r"\bcomplimentary\b",
    ]

    if any(
        re.search(
            pattern,
            combined,
            flags=re.IGNORECASE
        )
        for pattern in reward_patterns
    ):
        categories.append(
            "🎁 **NEUE BELOHNUNG**\n"
            "Eine neue Belohnung wurde entdeckt."
        )

    # --------------------------------------------------------
    # Doppelte Kategorien vermeiden
    # --------------------------------------------------------

    result = []
    seen = set()

    for category in categories:

        key = category.split(
            "\n",
            1
        )[0]

        if key in seen:
            continue

        seen.add(key)
        result.append(category)

    return result, current_signature


def build_thursday_message(
    news,
    categories
):
    title = clean_single_line(
        news.get("title", "")
    )

    url = news.get(
        "url",
        ""
    )

    category_text = "\n\n".join(
        categories
    )

    sections = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        (
            "🤫 *Psst... eine neue Meldung ist eingetroffen.*\n"
            "Unsere Informanten haben etwas Neues in Los Santos entdeckt."
        ),
    ]

    if category_text:
        sections.append(
            category_text
        )

    sections.append(
        "📡 **INFORMANTENBERICHT**\n"
        f"**{title}**"
    )

    sections.append(
        "💬 *„Wir behalten Los Santos im Auge. "
        "Sobald sich etwas bewegt, wissen wir es.“*"
    )

    sections.append(
        "🔗 **ORIGINALMELDUNG ÖFFNEN**\n"
        f"<{url}>"
    )

    return "\n\n".join(
        sections
    )


# ============================================================
# HAUPTLOGIK
# ============================================================

async def run_test():
    print(
        "========================================"
    )
    print(
        "LS-INSIDER TESTMODE"
    )
    print(
        "========================================"
    )

    # --------------------------------------------------------
    # MITTWOCH DESIGN
    # --------------------------------------------------------

    print(
        "\n[1/2] Hole GTA Online Eventwoche..."
    )

    weekly_data = await get_gtabase_data()

    if weekly_data:
        wednesday_message = (
            build_wednesday_message(
                weekly_data
            )
        )

        print(
            "\n--- MITTWOCH ---\n"
        )

        print(
            wednesday_message
        )

        send_discord(
            wednesday_message
        )

        print(
            "\nMittwoch-Test wurde gesendet."
        )

    else:
        print(
            "Keine Wocheninformationen gefunden."
        )

    # --------------------------------------------------------
    # DONNERSTAG DESIGN
    # --------------------------------------------------------

    print(
        "\n[2/2] Hole aktuellste Rockstar-Meldung..."
    )

    news = await get_rockstar_news()

    if news:

        print(
            "\nGefundene Meldung:"
        )

        print(
            news["title"]
        )

        print(
            news["url"]
        )

        state = load_state()

        categories, signature = (
            detect_thursday_categories(
                news,
                state
            )
        )

        thursday_message = (
            build_thursday_message(
                news,
                categories
            )
        )

        print(
            "\n--- DONNERSTAG ---\n"
        )

        print(
            thursday_message
        )

        send_discord(
            thursday_message
        )

        print(
            "\nDonnerstag-Test wurde gesendet."
        )

    else:
        print(
            "Keine passende Rockstar-Meldung gefunden."
        )

    # --------------------------------------------------------
    # Im Testmodus NICHT speichern.
    #
    # Dadurch können wir dieselben Meldungen mehrfach testen,
    # ohne dass der normale Änderungsmechanismus beeinflusst
    # wird.
    # --------------------------------------------------------

    print(
        "\nTEST_MODE ist aktiv – "
        "weekly_state.json wurde nicht verändert."
    )


async def run_normal():
    now = datetime.now(
        VIENNA
    )

    weekday = now.weekday()

    # --------------------------------------------------------
    # MITTWOCH
    # --------------------------------------------------------

    if weekday == 2:

        data = await get_gtabase_data()

        if not data:
            print(
                "Keine GTA-Wocheninformationen gefunden."
            )
            return

        message = build_wednesday_message(
            data
        )

        state = load_state()

        signature = make_signature(
            data.get("period"),
            data.get("title"),
            data.get("bonuses"),
            data.get("free_items"),
            data.get("vehicles"),
            data.get("discounts"),
            data.get("weekly_challenge"),
        )

        if state.get(
            "wednesday_signature"
        ) == signature:

            print(
                "Mittwochsbeitrag bereits veröffentlicht."
            )

            return

        send_discord(
            message
        )

        state[
            "wednesday_signature"
        ] = signature

        save_state(
            state
        )

        print(
            "Mittwochsbeitrag veröffentlicht."
        )

        return

    # --------------------------------------------------------
    # DONNERSTAG
    # --------------------------------------------------------

    if weekday == 3:

        news = await get_rockstar_news()

        if not news:
            print(
                "Keine passende Rockstar-Meldung gefunden."
            )
            return

        state = load_state()

        categories, signature = (
            detect_thursday_categories(
                news,
                state
            )
        )

        # ----------------------------------------------------
        # Keine neue oder geänderte Information:
        # NICHT posten.
        # ----------------------------------------------------

        if not categories:
            print(
                "Keine neue oder geänderte Information – "
                "kein Donnerstagspost."
            )
            return

        message = build_thursday_message(
            news,
            categories
        )

        send_discord(
            message
        )

        state[
            "rockstar_url"
        ] = news["url"]

        state[
            "rockstar_signature"
        ] = signature

        save_state(
            state
        )

        print(
            "Donnerstag-Geheimbericht veröffentlicht."
        )

        return

    # --------------------------------------------------------
    # Andere Tage
    # --------------------------------------------------------

    print(
        "Heute ist kein LS-Insider-Posting-Tag."
    )


async def main():

    if TEST_MODE:
        await run_test()
    else:
        await run_normal()


if __name__ == "__main__":
    asyncio.run(
        main()
    )
