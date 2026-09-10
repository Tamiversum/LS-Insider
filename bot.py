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

DISCORD_LIMIT = 1900

# ============================================================
# TESTPHASE
# ============================================================
#
# True:
#   Mittwoch und Donnerstag werden sofort getestet.
#
# False:
#   Normalbetrieb nach Wochentag.
#
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


def limit_text(text, limit=DISCORD_LIMIT):
    if len(text) <= limit:
        return text

    shortened = text[:limit - 30]

    split_at = shortened.rfind("\n")

    if split_at > 500:
        shortened = shortened[:split_at]

    return shortened.rstrip() + "\n…"


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
    if not text:
        return ""

    value = clean_single_line(text)

    current_year = datetime.now(
        VIENNA
    ).year

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

        day_start = int(match.group(2))
        day_end = int(match.group(3))

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

        day = int(match.group(2))
        year = int(match.group(3))

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
        ("GTA Online Weekly Update", "GTA Online Eventwoche"),
        ("Weekly Update", "Eventwoche"),
        ("Bonuses", "Boni"),
        ("Discounts", "Rabatte"),
        ("Discount", "Rabatt"),
        ("New Vehicles", "Neue Fahrzeuge"),
        ("New Vehicle", "Neues Fahrzeug"),
        ("Supercar", "Supersportwagen"),
        ("Motorcycle", "Motorrad"),
        ("Vehicle", "Fahrzeug"),
        ("Early Access", "Vorabzugang"),
        ("Rewards", "Belohnungen"),
        ("Reward", "Belohnung"),
        ("Weekly Challenge", "Wöchentliche Herausforderung"),
        ("FREE", "KOSTENLOS"),
        ("Free", "Kostenlos"),
        ("Gift", "Geschenk"),
        ("Gifts", "Geschenke"),
    ]

    for english, german in replacements:
        value = re.sub(
            re.escape(english),
            german,
            value,
            flags=re.IGNORECASE
        )

    return translate_dates(value)


# ============================================================
# GTABASE – WOCHENINFORMATIONEN
# ============================================================

def find_weekly_title(text):
    patterns = [
        r"GTA Online Weekly Update.{0,180}",
        r"GTA Online Eventwoche.{0,180}",
        r"Weekly Update.{0,180}",
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

    return "GTA Online Eventwoche – Boni & Rabatte"


def extract_period(text):
    patterns = [
        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2}"
        r"\s*[-–]\s*\d{1,2}"
        r"(?:,\s*\d{4})?"
        r"\b",

        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2}"
        r"(?:,\s*\d{4})?"
        r"\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            return format_period(
                match.group(0)
            )

    return ""


def extract_bonus_items(text):
    items = []

    patterns = [
        (
            r"Community\s+(?:Mission\s+)?Series"
            r".{0,100}?(?:3\s*[xX]|triple)",
            "Community Series – 3x GTA$ & RP"
        ),
        (
            r"Biker\s+Clubhouse\s+Contracts"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Biker-Clubhaus-Aufträge – 2x GTA$ & RP"
        ),
        (
            r"Clubhouse\s+Contracts"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Biker-Clubhaus-Aufträge – 2x GTA$ & RP"
        ),
        (
            r"MC\s+Work"
            r".{0,100}?(?:2\s*[xX]|double)",
            "MC-Arbeiten – 2x GTA$ & RP"
        ),
        (
            r"MC\s+Challenges"
            r".{0,100}?(?:2\s*[xX]|double)",
            "MC-Herausforderungen – 2x GTA$ & RP"
        ),
        (
            r"Street\s+Dealer\s+Sales"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Street-Dealer-Verkäufe – 2x GTA$"
        ),
        (
            r"Bike\s+Service\s+Missions"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Bike-Service-Missionen – 2x GTA$"
        ),
        (
            r"Hasta\s+La\s+Vista"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Hasta La Vista – 2x GTA$ & RP"
        ),
        (
            r"Every\s+Bullet\s+Counts"
            r".{0,100}?(?:2\s*[xX]|double)",
            "Every Bullet Counts – 2x GTA$ & RP"
        ),
    ]

    for pattern, result in patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        ):
            items.append(result)

    generic_patterns = [
        r"([A-Z][A-Za-z0-9'’& -]{2,60})"
        r".{0,100}?"
        r"\b([2-3])x\b"
        r".{0,50}?"
        r"(GTA\$|RP|rewards?)"
    ]

    for pattern in generic_patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches[:10]:
            name = clean_single_line(match[0])
            multiplier = match[1]

            if len(name) < 4:
                continue

            items.append(
                f"{name} – {multiplier}x GTA$ & RP"
            )

    return unique_items(items)[:12]


def extract_free_items(text):
    items = []

    known_free_patterns = [
        (
            r"Grapeseed\s+Clubhouse"
            r".{0,180}?"
            r"(?:FREE|free|KOSTENLOS)",
            "Grapeseed Clubhouse – KOSTENLOS"
        ),
        (
            r"Penaud\s+La\s+Coureuse"
            r".{0,300}?"
            r"(?:FREE|free|KOSTENLOS)",
            "Penaud La Coureuse – KOSTENLOS"
        ),
    ]

    for pattern, result in known_free_patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        ):
            items.append(result)

    patterns = [
        r"([A-Z][A-Za-z0-9'’& -]{2,60})"
        r".{0,100}?"
        r"\bFREE\b",

        r"([A-Z][A-Za-z0-9'’& -]{2,60})"
        r".{0,100}?"
        r"\bfree\b",
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches[:15]:
            value = clean_single_line(match)

            if not value:
                continue

            forbidden = [
                "gta online",
                "weekly challenge",
                "members",
                "access",
                "rewards",
                "bonus",
            ]

            if any(
                word in value.lower()
                for word in forbidden
            ):
                continue

            if 4 <= len(value) <= 70:
                items.append(
                    f"{value} – KOSTENLOS"
                )

    return unique_items(items)[:10]


def extract_vehicle_items(text):
    items = []

    vehicle_names = [
        "Pegassi Horus",
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
        "La Coureuse",
    ]

    for vehicle in vehicle_names:
        if re.search(
            rf"\b{re.escape(vehicle)}\b",
            text,
            flags=re.IGNORECASE
        ):
            items.append(
                translate_text(vehicle)
            )

    generic_patterns = [
        r"new\s+([A-Z][A-Za-z0-9'’ -]{2,50})",
        r"new\s+vehicle\s*[:\-]?\s*([A-Z][A-Za-z0-9'’ -]{2,50})",
    ]

    for pattern in generic_patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches[:10]:
            value = clean_single_line(match)

            if 3 <= len(value) <= 60:
                items.append(
                    translate_text(value)
                )

    return unique_items(items)[:10]


def extract_discount_items(text):
    items = []

    patterns = [
        (
            r"([A-Z][A-Za-z0-9'’& -]{2,60})"
            r".{0,100}?"
            r"(\d{1,2})%\s*(?:OFF|discount)"
        ),
        (
            r"([A-Z][A-Za-z0-9'’& -]{2,60})"
            r".{0,100}?"
            r"(\d{1,2})%\s*Rabatt"
        ),
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches[:20]:
            name = clean_single_line(match[0])
            percentage = match[1]

            if not (3 <= len(name) <= 70):
                continue

            forbidden = [
                "gta online",
                "this week",
                "weekly update",
                "members",
            ]

            if any(
                word in name.lower()
                for word in forbidden
            ):
                continue

            items.append(
                f"{name} – {percentage}% Rabatt"
            )

    return unique_items(items)[:15]


def extract_weekly_challenge(text):
    patterns = [
        r"Weekly Challenge.{0,350}",
        r"wöchentliche Herausforderung.{0,350}",
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

            return translate_text(value)

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

            period = extract_period(text)

            title = translate_text(
                find_weekly_title(text)
            )

            bonuses = extract_bonus_items(text)
            free_items = extract_free_items(text)
            vehicles = extract_vehicle_items(text)
            discounts = extract_discount_items(text)

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
    period = data.get("period", "")
    title = data.get(
        "title",
        "GTA Online Eventwoche"
    )

    bonuses = data.get("bonuses", [])
    free_items = data.get("free_items", [])
    vehicles = data.get("vehicles", [])
    discounts = data.get("discounts", [])
    weekly_challenge = data.get(
        "weekly_challenge",
        ""
    )

    sections = [
        "🗞️ **LS-INSIDER**",
        "",
        "# **DIESE WOCHE IN LOS SANTOS**",
    ]

    if period:
        sections.extend([
            "",
            f"📅 **{period}**",
        ])

    sections.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "📰 **DIE WOCHENMELDUNG**",
        f"**{limit_text(title, 500)}**",
    ])

    if bonuses:
        sections.extend([
            "",
            "💰 **BONI & AKTIONEN**",
        ])

        for item in bonuses[:8]:
            sections.append(f"• {item}")

    if free_items:
        sections.extend([
            "",
            "🎁 **KOSTENLOSE SACHEN**",
        ])

        for item in free_items[:8]:
            sections.append(f"• {item}")

    if vehicles:
        sections.extend([
            "",
            "🚗 **NEU AUF DEN STRASSEN**",
        ])

        for item in vehicles[:8]:
            sections.append(f"• {item}")

    if discounts:
        sections.extend([
            "",
            "🏷️ **RABATTE**",
        ])

        for item in discounts[:10]:
            sections.append(f"• {item}")

    if weekly_challenge:
        sections.extend([
            "",
            "🏆 **WÖCHENTLICHE HERAUSFORDERUNG**",
            f"• {weekly_challenge}",
        ])

    if period:
        sections.extend([
            "",
            "📅 **AKTUELLE WOCHE**",
            f"• {period}",
        ])

    sections.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🔗 **VOLLSTÄNDIGER ARTIKEL**",
        f"<{GTABASE_URL}>",
        "",
        "🗞️ *Dein LS-Insider – immer bestens informiert.*",
    ])

    return limit_text(
        "\n".join(sections)
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

    month_numbers = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

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

            return datetime(
                year,
                month_numbers[month.lower()],
                day
            )

        except Exception:
            continue

    return None


async def extract_article_content(page):
    selectors = [
        "article",
        "main article",
        '[data-testid="article"]',
        "main",
    ]

    for selector in selectors:
        locator = page.locator(selector)

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
                    value = clean_single_line(element)

                    if value:
                        parts.append(value)

                parts = unique_items(parts)

                result = "\n".join(parts)

                if len(result) >= 150:
                    return result

            except Exception:
                continue

    try:
        elements = await page.locator(
            "h1, h2, h3, p"
        ).all_inner_texts()

        parts = []

        for element in elements:
            value = clean_single_line(element)

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

                    title = clean_single_line(title)

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

                    date = extract_date_from_text(title)

                    if date is None:
                        try:
                            parent_text = (
                                clean_single_line(
                                    await link.locator(
                                        "xpath=.."
                                    ).inner_text()
                                )
                            )

                            date = extract_date_from_text(
                                parent_text
                            )

                        except Exception:
                            date = None

                    candidates.append({
                        "title": title,
                        "url": href,
                        "date": date,
                    })

                except Exception:
                    continue

            if not candidates:
                return None

            candidates.sort(
                key=lambda item: (
                    item["date"]
                    or datetime(1970, 1, 1)
                ),
                reverse=True
            )

            latest = candidates[0]

            article_page = await browser.new_page()

            try:
                await article_page.goto(
                    latest["url"],
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                await article_page.wait_for_timeout(3000)

                article_text = (
                    await extract_article_content(
                        article_page
                    )
                )

            finally:
                await article_page.close()

            clean_title = translate_text(
                latest["title"]
            )

            return {
                "title": clean_title,
                "original_title": clean_single_line(
                    latest["title"]
                ),
                "url": latest["url"],
                "date": latest["date"],
                "body": clean_text(article_text),
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

    url = news.get("url", "")

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

    result = []
    seen = set()

    for category in categories:
        key = category.split("\n", 1)[0]

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

    url = news.get("url", "")

    category_text = "\n\n".join(categories)

    sections = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        (
            "🤫 *Psst... eine neue Meldung ist eingetroffen.*\n"
            "Unsere Informanten haben etwas Neues in Los Santos entdeckt."
        ),
    ]

    if category_text:
        sections.append(category_text)

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

    return limit_text(
        "\n\n".join(sections)
    )


# ============================================================
# MITTWOCH
# ============================================================

async def run_wednesday():
    print("========================================")
    print("LS-INSIDER – MITTWOCH")
    print("DIE WOCHENZEITUNG")
    print("========================================")

    data = await get_gtabase_data()

    if not data:
        print(
            "Keine GTA-Wocheninformationen gefunden."
        )
        return

    message = build_wednesday_message(data)

    signature = make_signature(
        data.get("period"),
        data.get("title"),
        data.get("bonuses"),
        data.get("free_items"),
        data.get("vehicles"),
        data.get("discounts"),
        data.get("weekly_challenge"),
    )

    state = load_state()

    if state.get(
        "wednesday_signature"
    ) == signature:

        print(
            "Mittwochsbeitrag bereits veröffentlicht."
        )

        return

    print("\n--- MITTWOCHSPOST ---\n")
    print(message)

    send_discord(message)

    state["wednesday_signature"] = signature

    save_state(state)

    print(
        "\nMittwochsbeitrag veröffentlicht."
    )


# ============================================================
# DONNERSTAG
# ============================================================

async def run_thursday():
    print("========================================")
    print("LS-INSIDER – DONNERSTAG")
    print("GEHEIMBERICHT")
    print("========================================")

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

    print("\n--- DONNERSTAGSPOST ---\n")
    print(message)

    send_discord(message)

    state["rockstar_url"] = news["url"]
    state["rockstar_signature"] = signature

    save_state(state)

    print(
        "\nDonnerstag-Geheimbericht veröffentlicht."
    )


# ============================================================
# TESTMODUS
# ============================================================

async def run_test():
    print("========================================")
    print("LS-INSIDER TESTMODE")
    print("========================================")

    print(
        "\n[1/2] Teste Mittwoch – Wochenzeitung..."
    )

    weekly_data = await get_gtabase_data()

    if weekly_data:

        wednesday_message = (
            build_wednesday_message(
                weekly_data
            )
        )

        print("\n--- MITTWOCH ---\n")
        print(wednesday_message)

        send_discord(wednesday_message)

        print(
            "\nMittwoch-Test wurde gesendet."
        )

    else:
        print(
            "Keine Wocheninformationen gefunden."
        )

    print(
        "\n[2/2] Teste Donnerstag – Geheimbericht..."
    )

    news = await get_rockstar_news()

    if news:

        print("\nGefundene Meldung:")
        print(news["title"])
        print(news["url"])

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

        print("\n--- DONNERSTAG ---\n")
        print(thursday_message)

        send_discord(thursday_message)

        print(
            "\nDonnerstag-Test wurde gesendet."
        )

    else:
        print(
            "Keine passende Rockstar-Meldung gefunden."
        )

    print(
        "\nTEST_MODE ist aktiv."
    )


# ============================================================
# NORMALBETRIEB
# ============================================================

async def run_normal():
    now = datetime.now(VIENNA)

    weekday = now.weekday()

    # Montag = 0
    # Dienstag = 1
    # Mittwoch = 2
    # Donnerstag = 3

    if weekday == 2:
        await run_wednesday()
        return

    if weekday == 3:
        await run_thursday()
        return

    print(
        "Heute ist kein LS-Insider-Posting-Tag."
    )


# ============================================================
# START
# ============================================================

async def main():

    if TEST_MODE:
        await run_test()
    else:
        await run_normal()


if __name__ == "__main__":
    asyncio.run(main())
