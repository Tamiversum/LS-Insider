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
# LS-INSIDER
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
# TESTMODUS
# ============================================================

# TRUE:
#   Mittwoch UND Donnerstag werden getestet.
#
# FALSE:
#   Normaler Wochentag entscheidet automatisch.
#
TEST_MODE = True


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n+", "\n", text)

    return text.strip()


def unique_items(items):
    result = []

    for item in items:
        item = clean_text(item)

        if item and item not in result:
            result.append(item)

    return result


def make_signature(data):
    raw = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False
    )

    return hashlib.sha256(
        raw.encode("utf-8")
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

        message = message[
            split_at:
        ].lstrip()

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

        if response.status_code not in (200, 204):
            raise RuntimeError(
                "Discord-Webhook-Fehler: "
                f"{response.status_code} "
                f"{response.text}"
            )


def contains_any(text, keywords):
    lower = text.lower()

    return any(
        keyword.lower() in lower
        for keyword in keywords
    )


# ============================================================
# DEUTSCHE ÜBERSETZUNG
# ============================================================

MONTHS = {
    "January": "Januar",
    "February": "Februar",
    "March": "März",
    "April": "April",
    "May": "Mai",
    "June": "Juni",
    "July": "Juli",
    "August": "August",
    "September": "September",
    "October": "Oktober",
    "November": "November",
    "December": "Dezember"
}


def translate_dates(text):
    if not text:
        return ""

    result = text

    for english, german in MONTHS.items():
        result = re.sub(
            rf"\b{english}\b",
            german,
            result,
            flags=re.IGNORECASE
        )

    return result


def translate_text(text):
    if not text:
        return ""

    result = clean_text(text)

    # --------------------------------------------------------
    # KONKRETE ROCKSTAR-TITEL
    # --------------------------------------------------------

    exact_translations = {

        "Compete Across Entrepreneurial Endeavors in the GTA Online Business Rivalries Event":
            "Messt euch im Event „Business Rivalries“ mit der Konkurrenz eurer zahlreichen Unternehmen in GTA Online",

        "GTA Online 3. September 2026 Messt euch im Event „Business Rivalries“ mit der Konkurrenz eurer zahlreichen Unternehmen in GTA Online":
            "GTA Online – 3. September 2026: Messt euch im Event „Business Rivalries“ mit der Konkurrenz eurer zahlreichen Unternehmen in GTA Online",

        "GTA Online 10. September 2026 GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar":
            "GTA Online – 10. September 2026: GTA+-Mitglieder erhalten eine Woche Vorabzugang zum neuen Pegassi Horus",

        "GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar":
            "GTA+-Mitglieder erhalten eine Woche Vorabzugang zum neuen Pegassi Horus"
    }

    for english, german in exact_translations.items():

        if english.lower() in result.lower():
            result = re.sub(
                re.escape(english),
                german,
                result,
                flags=re.IGNORECASE
            )

    # --------------------------------------------------------
    # TYPISCHE GTA-BEGRIFFE
    # --------------------------------------------------------

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
            "Bonuses & Discounts",
            "Boni & Rabatte"
        ),

        (
            "Bonuses",
            "Boni"
        ),

        (
            "Discounts",
            "Rabatte"
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
            "New Supercar",
            "Neuer Supersportwagen"
        ),

        (
            "Supercar",
            "Supersportwagen"
        ),

        (
            "Sports Car",
            "Sportwagen"
        ),

        (
            "Motorcycle",
            "Motorrad"
        ),

        (
            "Motorcycles",
            "Motorräder"
        ),

        (
            "Vehicle",
            "Fahrzeug"
        ),

        (
            "Vehicles",
            "Fahrzeuge"
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
            "Free",
            "Kostenlos"
        ),

        (
            "FREE",
            "KOSTENLOS"
        ),

        (
            "Gift",
            "Geschenk"
        ),

        (
            "Gifts",
            "Geschenke"
        ),

        (
            "Complete at least one Weekly Challenge",
            "Schließt mindestens eine Weekly Challenge ab"
        ),

        (
            "over the next two weeks",
            "in den nächsten zwei Wochen"
        ),

        (
            "to claim",
            "um euch zu sichern"
        ),

        (
            "complimentary",
            "kostenlos"
        ),

        (
            "when you play GTA Online",
            "wenn ihr GTA Online spielt"
        ),

        (
            "between",
            "zwischen"
        ),

        (
            "September 24-30",
            "24.–30. September"
        ),

        (
            "September 10-16",
            "10.–16. September"
        ),

        (
            "September 3-9",
            "3.–9. September"
        ),

        (
            "Earn",
            "Verdient"
        ),

        (
            "selling all kinds of product",
            "durch den Verkauf aller Arten von Waren"
        ),

        (
            "to bank an extra",
            "um zusätzlich"
        ),

        (
            "plus the",
            "sowie den"
        ),

        (
            "and",
            "und"
        )
    ]

    # Lange Begriffe zuerst
    replacements.sort(
        key=lambda item: len(item[0]),
        reverse=True
    )

    for english, german in replacements:

        result = re.sub(
            re.escape(english),
            german,
            result,
            flags=re.IGNORECASE
        )

    result = translate_dates(result)

    # --------------------------------------------------------
    # NACHSORTIERUNG
    # --------------------------------------------------------

    result = re.sub(
        r"\b(\d{1,2})-(\d{1,2})\.\s*September\b",
        r"\1.–\2. September",
        result
    )

    result = result.replace(
        "Schließt mindestens eine Weekly Challenge ab in den nächsten zwei Wochen",
        "Schließt in den nächsten zwei Wochen mindestens eine Weekly Challenge ab"
    )

    result = result.replace(
        "um euch zu sichern ein",
        "um euch ein"
    )

    result = result.replace(
        "und kostenlos",
        "und kostenlos"
    )

    return clean_text(result)


# ============================================================
# ZEITRAUM
# ============================================================

def detect_period(text):

    patterns = [

        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2}"
        r"\s*[-–]"
        r"\s*"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2}"
        r"\b",

        r"\b"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2}"
        r"\s*[-–]"
        r"\s*\d{1,2}"
        r"\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = clean_text(
                match.group(0)
            )

            value = translate_dates(
                value
            )

            value = re.sub(
                r"(\w+)\s+(\d{1,2})",
                r"\2. \1",
                value,
                count=0
            )

            return value

    return ""


# ============================================================
# GTABASE – TITEL
# ============================================================

async def find_weekly_title(page):

    elements = await page.locator(
        "h1, h2, h3"
    ).all()

    candidates = []

    for element in elements:

        try:

            text = clean_text(
                await element.inner_text()
            )

            if text:
                candidates.append(text)

        except Exception:
            pass

    candidates = unique_items(
        candidates
    )

    keywords = [
        "weekly update",
        "weekly bonuses",
        "bonuses & discounts",
        "bonuses",
        "discounts"
    ]

    for candidate in candidates:

        lower = candidate.lower()

        if any(
            keyword in lower
            for keyword in keywords
        ):

            return translate_text(
                candidate
            )

    if candidates:

        return translate_text(
            candidates[0]
        )

    return "GTA Online Eventwoche"


# ============================================================
# BONI
# ============================================================

def extract_bonus_items(text):

    results = []

    patterns = [

        (
            r"Community Series Jobs\s+3x",
            "Community Series Jobs – 3x GTA$ & RP"
        ),

        (
            r"Clubhouse Contracts\s+2x",
            "Clubhouse Contracts – 2x GTA$ & RP"
        ),

        (
            r"MC Work\s+2x",
            "MC-Arbeiten – 2x GTA$ & RP"
        ),

        (
            r"MC Challenges\s+2x",
            "MC-Herausforderungen – 2x GTA$ & RP"
        ),

        (
            r"Hasta La Vista\s+2x",
            "Hasta La Vista – 2x GTA$ & RP"
        ),

        (
            r"Every Bullet Counts\s+2x",
            "Every Bullet Counts – 2x GTA$ & RP"
        ),

        (
            r"Street Dealer Sales\s+2x",
            "Verkäufe bei Street Dealers – 2x GTA$"
        ),

        (
            r"Bike Service Missions\s+2x",
            "Bike-Service-Missionen – 2x GTA$"
        )
    ]

    for pattern, replacement in patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):

            results.append(
                replacement
            )

    return unique_items(
        results
    )


# ============================================================
# KOSTENLOSE SACHEN
# ============================================================

def extract_free_items(text):

    results = []

    if re.search(
        r"Grapeseed Clubhouse.*?FREE",
        text,
        re.IGNORECASE
    ):

        results.append(
            "Grapeseed Clubhouse – KOSTENLOS"
        )

    match = re.search(
        r"Complete at least one Weekly Challenge.*?"
        r"free Penaud La Coureuse.*?"
        r"September 24.?30",
        text,
        re.IGNORECASE
    )

    if match:

        results.append(
            "Mindestens eine Weekly Challenge "
            "abschließen, um die Penaud La Coureuse "
            "und ein kostenloses HSW-Upgrade zu erhalten "
            "(24.–30. September)."
        )

    return unique_items(
        results
    )


# ============================================================
# RABATTE
# ============================================================

def extract_discount_items(text):

    results = []

    pattern = re.compile(
        r"([A-Z][A-Za-z0-9'&.\-]*(?:\s+[A-Z][A-Za-z0-9'&.\-]*){0,5})"
        r"\s+"
        r"(\$[\d,]+)"
        r"\s+"
        r"(-\d{1,2}%)"
        r"\s+"
        r"(\$[\d,]+)"
    )

    matches = pattern.findall(
        text
    )

    blocked = [
        "GTA 6",
        "Vehicles Weapons",
        "Map Locations",
        "GTA Content",
        "Weekly Update",
        "This Week",
        "Showrooms",
        "Test Rides"
    ]

    for name, original, discount, sale in matches:

        name = clean_text(
            name
        )

        if any(
            blocked_text.lower()
            in name.lower()
            for blocked_text in blocked
        ):
            continue

        if name.lower() in {
            "vehicles",
            "vehicle",
            "showrooms",
            "test rides",
            "discounts",
            "in game discounts"
        }:
            continue

        results.append(
            f"{name} {original} "
            f"{discount} {sale}"
        )

    return unique_items(
        results
    )


# ============================================================
# FAHRZEUGE
# ============================================================

def extract_vehicle_items(discounts):

    vehicles = []

    for item in discounts:

        match = re.match(
            r"^(.*?)\s+\$[\d,]+\s+-\d{1,2}%\s+\$[\d,]+$",
            item
        )

        if not match:
            continue

        name = clean_text(
            match.group(1)
        )

        if name:
            vehicles.append(
                name
            )

    return unique_items(
        vehicles
    )


# ============================================================
# WEEKLY CHALLENGE
# ============================================================

def extract_weekly_challenge(text):

    match = re.search(
        r"Earn GTA\$1,000,000"
        r"[^.]*?"
        r"Junk Tracksuit",
        text,
        re.IGNORECASE
    )

    if not match:

        match = re.search(
            r"Earn GTA\$[\d,]+"
            r"[^.]*?"
            r"Junk Tracksuit",
            text,
            re.IGNORECASE
        )

    if not match:
        return ""

    return (
        "Verdient GTA$1.000.000 durch den "
        "Verkauf aller Arten von Waren und "
        "erhaltet zusätzlich GTA$1.000.000 "
        "sowie den Junk Tracksuit."
    )


# ============================================================
# GTABASE ABRUFEN
# ============================================================

async def get_gtabase_data():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="en-US"
        )

        try:

            print(
                "🌐 Öffne GTABase..."
            )

            await page.goto(
                GTABASE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                5000
            )

            print(
                f"✅ GTABase geladen: "
                f"{page.url}"
            )

            title = await find_weekly_title(
                page
            )

            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text()
            )

            period = detect_period(
                body_text
            )

            bonuses = extract_bonus_items(
                body_text
            )

            free_items = extract_free_items(
                body_text
            )

            discounts = extract_discount_items(
                body_text
            )

            vehicles = extract_vehicle_items(
                discounts
            )

            challenge = extract_weekly_challenge(
                body_text
            )

            print("")
            print(
                "🔎 GTABASE-DATEN"
            )
            print(
                f"📰 Titel: {title}"
            )
            print(
                f"📅 Zeitraum: {period}"
            )
            print(
                f"💰 Boni: {len(bonuses)}"
            )
            print(
                f"🎁 Kostenlos: {len(free_items)}"
            )
            print(
                f"🚗 Fahrzeuge: {len(vehicles)}"
            )
            print(
                f"🏷️ Rabatte: {len(discounts)}"
            )
            print(
                "🏆 Challenge: "
                + ("Ja" if challenge else "Nein")
            )

            return {
                "title": title,
                "period": period,
                "bonuses": bonuses,
                "free_items": free_items,
                "vehicles": vehicles,
                "discounts": discounts,
                "weekly_challenge": challenge
            }

        finally:

            await browser.close()


# ============================================================
# MITTWOCH – DIESE WOCHE IN LOS SANTOS
# ============================================================

def build_wednesday_message(data):

    title = data.get(
        "title",
        "GTA Online Eventwoche"
    )

    period = data.get(
        "period",
        "Unbekannt"
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

    challenge = data.get(
        "weekly_challenge",
        ""
    )

    lines = []

    lines.append(
        "🗞️ **LS-INSIDER**"
    )

    lines.append("")

    lines.append(
        "# **DIESE WOCHE IN LOS SANTOS**"
    )

    lines.append("")

    lines.append(
        f"**{period}**"
    )

    lines.append("")

    # EVENT
    lines.append(
        "📰 **EVENT**"
    )

    lines.append(
        title
    )

    lines.append("")

    # BONI
    if bonuses:

        lines.append(
            "💰 **BONI**"
        )

        for item in bonuses:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # GESCHENKE
    if free_items:

        lines.append(
            "🎁 **GESCHENKE FÜR DIE COMMUNITY**"
        )

        for item in free_items:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # FAHRZEUGE
    if vehicles:

        lines.append(
            "🚗 **NEU AUF DEN STRASSEN**"
        )

        for item in vehicles:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # RABATTE
    if discounts:

        lines.append(
            "🏷️ **SONDERANGEBOTE**"
        )

        for item in discounts:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # WEEKLY CHALLENGE
    if challenge:

        lines.append(
            "🏆 **WEEKLY CHALLENGE**"
        )

        lines.append(
            f"• {challenge}"
        )

        lines.append("")

    # AKTUELLE WOCHE
    lines.append(
        "📅 **AKTUELLE WOCHE**"
    )

    lines.append(
        f"• {period}"
    )

    lines.append("")

    # LINK
    lines.append(
        "🔗 **VOLLSTÄNDIGER LINK**"
    )

    lines.append(
        f"<{GTABASE_URL}>"
    )

    return "\n".join(lines)


# ============================================================
# MITTWOCH NORMALBETRIEB
# ============================================================

def run_wednesday(gtabase_data):

    state = load_state()

    period = gtabase_data.get(
        "period",
        ""
    )

    if not period:

        raise RuntimeError(
            "GTABase konnte keinen Zeitraum "
            "erkennen."
        )

    signature = make_signature(
        gtabase_data
    )

    previous_signature = state.get(
        "wednesday_signature"
    )

    if previous_signature == signature:

        print("")
        print(
            "ℹ️ Diese Eventwoche wurde "
            "bereits gepostet."
        )

        return

    message = build_wednesday_message(
        gtabase_data
    )

    print("")
    print(
        "📤 Sende Mittwoch-Ausgabe..."
    )

    send_discord(
        message
    )

    state["week_period"] = period
    state["wednesday_signature"] = signature
    state["wednesday_data"] = gtabase_data

    save_state(
        state
    )

    print("")
    print(
        "✅ Mittwoch-Ausgabe wurde gepostet."
    )


# ============================================================
# ROCKSTAR – DATUM ERKENNEN
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
        r",?\s+"
        r"(\d{4})"
        r"\b",

        r"\b"
        r"(\d{1,2})"
        r"\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{4})"
        r"\b"
    ]

    for index, pattern in enumerate(patterns):

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        try:

            if index == 0:

                month = match.group(1)
                day = int(match.group(2))
                year = int(match.group(3))

            else:

                day = int(match.group(1))
                month = match.group(2)
                year = int(match.group(3))

            month_number = list(
                MONTHS.keys()
            ).index(
                next(
                    item
                    for item in MONTHS
                    if item.lower()
                    == month.lower()
                )
            ) + 1

            return datetime(
                year,
                month_number,
                day
            )

        except Exception:
            pass

    return None


# ============================================================
# ROCKSTAR – GTA ONLINE MELDUNGEN FINDEN
# ============================================================

async def get_rockstar_news():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="de-DE"
        )

        try:

            print("")
            print(
                "🌐 Öffne Rockstar Newswire..."
            )

            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                6000
            )

            print(
                f"✅ Rockstar geladen: "
                f"{page.url}"
            )

            links = await page.locator(
                "a[href]"
            ).all()

            candidates = []

            for link in links:

                try:

                    href = await link.get_attribute(
                        "href"
                    )

                    title = clean_text(
                        await link.inner_text()
                    )

                    if not href:
                        continue

                    if not title:
                        continue

                    if "/newswire/" not in href:
                        continue

                    combined = (
                        title.lower()
                        + " "
                        + href.lower()
                    )

                    if "gta-online" not in combined:

                        # GTA Online kann auch über
                        # Business Rivalries etc.
                        # erkennbar sein.
                        gta_keywords = [
                            "gta online",
                            "business rivalries",
                            "pegassi",
                            "gta+",
                            "los santos"
                        ]

                        if not any(
                            keyword in combined
                            for keyword in gta_keywords
                        ):
                            continue

                    if href.startswith("/"):

                        full_url = (
                            "https://www.rockstargames.com"
                            + href
                        )

                    else:

                        full_url = href

                    # ------------------------------------------------
                    # ELTERNBEREICH LESEN
                    # ------------------------------------------------

                    parent_text = ""

                    try:

                        parent_text = clean_text(
                            await link.locator(
                                "xpath=.."
                            ).inner_text()
                        )

                    except Exception:
                        pass

                    combined_text = (
                        title
                        + "\n"
                        + parent_text
                    )

                    article_date = (
                        extract_date_from_text(
                            combined_text
                        )
                    )

                    candidates.append(
                        {
                            "title": title,
                            "url": full_url,
                            "date": (
                                article_date.isoformat()
                                if article_date
                                else ""
                            )
                        }
                    )

                except Exception:
                    pass

            # --------------------------------------------------------
            # DUPLIKATE ENTFERNEN
            # --------------------------------------------------------

            unique_candidates = []

            seen_urls = set()

            for candidate in candidates:

                if candidate["url"] in seen_urls:
                    continue

                seen_urls.add(
                    candidate["url"]
                )

                unique_candidates.append(
                    candidate
                )

            if not unique_candidates:

                raise RuntimeError(
                    "Keine passende GTA-Online-"
                    "Meldung auf Rockstar gefunden."
                )

            # --------------------------------------------------------
            # DATUMSORTIERUNG
            # --------------------------------------------------------

            unique_candidates.sort(
                key=lambda item: (
                    item["date"] or "0000-00-00"
                ),
                reverse=True
            )

            latest = unique_candidates[0]

            article_url = latest["url"]
            original_title = latest["title"]

            print("")
            print(
                "📰 Ausgewählte Rockstar-Meldung:"
            )

            print(
                original_title
            )

            print(
                f"📅 Datum: "
                f"{latest['date'] or 'nicht erkannt'}"
            )

            print(
                article_url
            )

            # --------------------------------------------------------
            # ARTIKEL ÖFFNEN
            # --------------------------------------------------------

            article_page = await browser.new_page(
                locale="de-DE"
            )

            try:

                await article_page.goto(
                    article_url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                await article_page.wait_for_timeout(
                    4000
                )

                article_body = clean_text(
                    await article_page.locator(
                        "body"
                    ).inner_text()
                )

            finally:

                await article_page.close()

            german_title = translate_text(
                original_title
            )

            german_body = translate_text(
                article_body
            )

            return {
                "title": german_title,
                "original_title": original_title,
                "url": article_url,
                "date": latest["date"],
                "body": german_body
            }

        finally:

            await browser.close()


# ============================================================
# DONNERSTAG – RELEVANTE INFORMATIONEN
# ============================================================

def extract_thursday_categories(
    article,
    previous_article
):

    title = article.get(
        "title",
        ""
    )

    body = article.get(
        "body",
        ""
    )

    url = article.get(
        "url",
        ""
    )

    previous_url = ""

    if previous_article:

        previous_url = previous_article.get(
            "url",
            ""
        )

    categories = []

    # --------------------------------------------------------
    # NEUE MELDUNG
    # --------------------------------------------------------

    if url != previous_url:

        categories.append(
            (
                "🆕 **NEU**",
                title
            )
        )

    # --------------------------------------------------------
    # ÄNDERUNG
    # --------------------------------------------------------

    elif previous_article:

        old_signature = make_signature(
            previous_article
        )

        new_signature = make_signature(
            article
        )

        if old_signature != new_signature:

            categories.append(
                (
                    "⚠️ **ÄNDERUNG**",
                    "Die bestehende Meldung wurde "
                    "von Rockstar aktualisiert."
                )
            )

    # --------------------------------------------------------
    # BONI
    # --------------------------------------------------------

    bonus_keywords = [
        "2x",
        "3x",
        "4x",
        "5x",
        "bonus",
        "bonuses",
        "gta$ & rp",
        "gta$ und rp",
        "doppelte",
        "dreifache"
    ]

    if contains_any(
        title + " " + body,
        bonus_keywords
    ):

        categories.append(
            (
                "💰 **NEUER BONUS**",
                "Neue Bonusbelohnungen "
                "wurden entdeckt."
            )
        )

    # --------------------------------------------------------
    # FAHRZEUGE
    # --------------------------------------------------------

    vehicle_keywords = [
        "new vehicle",
        "new car",
        "new supercar",
        "supercar",
        "fahrzeug",
        "supersportwagen",
        "pegassi",
        "bravado",
        "grotti",
        "benefactor",
        "declasse",
        "vapid",
        "progen",
        "truffade",
        "lampadati"
    ]

    if contains_any(
        title + " " + body,
        vehicle_keywords
    ):

        categories.append(
            (
                "🚗 **NEUE FAHRZEUGMELDUNG**",
                "Eine neue Fahrzeugmeldung "
                "wurde entdeckt."
            )
        )

    # --------------------------------------------------------
    # BELOHNUNGEN
    # --------------------------------------------------------

    reward_keywords = [
        "reward",
        "rewards",
        "free",
        "gift",
        "gifts",
        "belohnung",
        "belohnungen",
        "geschenk",
        "geschenke",
        "claim",
        "kostenlos"
    ]

    if contains_any(
        title + " " + body,
        reward_keywords
    ):

        categories.append(
            (
                "🎁 **NEUE BELOHNUNG**",
                "Eine neue Belohnung oder "
                "kostenlose Sache wurde entdeckt."
            )
        )

    return unique_items(
        [
            f"{heading}\n{content}"
            for heading, content in categories
        ]
    )


# ============================================================
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

def build_thursday_message(
    article,
    category_lines
):

    title = article.get(
        "title",
        "Neue Rockstar-Meldung"
    )

    url = article.get(
        "url",
        ROCKSTAR_URL
    )

    lines = []

    lines.append(
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**"
    )

    lines.append("")

    lines.append(
        "🤫 *Psst... eine neue Meldung "
        "ist eingetroffen.*"
    )

    lines.append(
        "Unsere Informanten haben etwas "
        "Neues in Los Santos entdeckt."
    )

    lines.append("")

    # Kategorien
    for item in category_lines:

        lines.append(
            item
        )

        lines.append("")

    lines.append(
        "📡 **INFORMANTENBERICHT**"
    )

    lines.append(
        f"**{title}**"
    )

    lines.append("")

    lines.append(
        "💬 *„Wir behalten Los Santos im Auge. "
        "Sobald sich etwas bewegt, wissen wir es.“*"
    )

    lines.append("")

    lines.append(
        "🔗 **ORIGINALMELDUNG ÖFFNEN**"
    )

    lines.append(
        f"<{url}>"
    )

    return "\n".join(lines)


# ============================================================
# DONNERSTAG NORMALBETRIEB
# ============================================================

async def run_thursday():

    state = load_state()

    article = await get_rockstar_news()

    previous_article = state.get(
        "thursday_article"
    )

    category_pairs = []

    # NEU / ÄNDERUNG
    title = article.get(
        "title",
        ""
    )

    url = article.get(
        "url",
        ""
    )

    previous_url = ""

    if previous_article:
        previous_url = previous_article.get(
            "url",
            ""
        )

    if url != previous_url:

        category_pairs.append(
            (
                "🆕 **NEU**",
                title
            )
        )

    elif previous_article:

        if make_signature(article) != make_signature(
            previous_article
        ):

            category_pairs.append(
                (
                    "⚠️ **ÄNDERUNG**",
                    "Die bestehende Meldung wurde "
                    "aktualisiert."
                )
            )

    # Bonus
    if contains_any(
        article.get("body", ""),
        [
            "2x",
            "3x",
            "4x",
            "5x",
            "bonus",
            "bonuses"
        ]
    ):

        category_pairs.append(
            (
                "💰 **NEUER BONUS**",
                "Neue Bonusbelohnungen wurden entdeckt."
            )
        )

    # Fahrzeuge
    if contains_any(
        title + " " + article.get("body", ""),
        [
            "new vehicle",
            "new car",
            "new supercar",
            "supercar",
            "vehicle",
            "fahrzeug",
            "pegassi",
            "bravado",
            "grotti"
        ]
    ):

        category_pairs.append(
            (
                "🚗 **NEUE FAHRZEUGMELDUNG**",
                "Eine neue Fahrzeugmeldung wurde entdeckt."
            )
        )

    # Belohnungen
    if contains_any(
        title + " " + article.get("body", ""),
        [
            "free",
            "reward",
            "gift",
            "belohnung",
            "geschenk",
            "kostenlos"
        ]
    ):

        category_pairs.append(
            (
                "🎁 **NEUE BELOHNUNG**",
                "Eine neue Belohnung oder kostenlose "
                "Sache wurde entdeckt."
            )
        )

    if not category_pairs:

        print("")
        print(
            "ℹ️ Keine neue oder geänderte "
            "Information."
        )

        return

    category_lines = []

    for heading, content in category_pairs:

        category_lines.append(
            f"{heading}\n{content}"
        )

    message = build_thursday_message(
        article,
        category_lines
    )

    print("")
    print(
        "📤 Sende Donnerstag-Geheimbericht..."
    )

    send_discord(
        message
    )

    state["thursday_article"] = article
    state["thursday_signature"] = make_signature(
        article
    )

    save_state(
        state
    )

    print("")
    print(
        "✅ Donnerstag-Geheimbericht "
        "wurde gepostet."
    )


# ============================================================
# TESTMODUS
# ============================================================

async def run_test():

    print("")
    print("=" * 70)
    print("🧪 LS-INSIDER TESTMODUS")
    print("=" * 70)

    print("")
    print(
        "Es werden beide finalen Designs gesendet."
    )

    print(
        "Der Veröffentlichungsstatus wird "
        "nicht gespeichert."
    )

    # ========================================================
    # MITTWOCH
    # ========================================================

    print("")
    print(
        "🗞️ TEST 1/2 – MITTWOCH"
    )

    gtabase_data = await get_gtabase_data()

    if not gtabase_data.get(
        "period"
    ):

        raise RuntimeError(
            "GTABase-Test fehlgeschlagen: "
            "Kein Zeitraum erkannt."
        )

    wednesday_message = build_wednesday_message(
        gtabase_data
    )

    print("")
    print(
        "📤 Sende Mittwoch-Design..."
    )

    send_discord(
        wednesday_message
    )

    print(
        "✅ Mittwoch-Test gesendet."
    )

    # ========================================================
    # DONNERSTAG
    # ========================================================

    print("")
    print(
        "🕵️ TEST 2/2 – DONNERSTAG"
    )

    article = await get_rockstar_news()

    # Im Test wird die gefundene aktuelle
    # Rockstar-Meldung immer als NEU behandelt.

    category_pairs = [
        (
            "🆕 **NEU**",
            article.get(
                "title",
                "Neue Rockstar-Meldung"
            )
        )
    ]

    article_text = (
        article.get("title", "")
        + " "
        + article.get("body", "")
    )

    if contains_any(
        article_text,
        [
            "2x",
            "3x",
            "4x",
            "5x",
            "bonus",
            "bonuses",
            "gta$ & rp"
        ]
    ):

        category_pairs.append(
            (
                "💰 **NEUER BONUS**",
                "Neue Bonusbelohnungen "
                "wurden entdeckt."
            )
        )

    if contains_any(
        article_text,
        [
            "new vehicle",
            "new car",
            "new supercar",
            "supercar",
            "vehicle",
            "fahrzeug",
            "pegassi",
            "bravado",
            "grotti"
        ]
    ):

        category_pairs.append(
            (
                "🚗 **NEUE FAHRZEUGMELDUNG**",
                "Eine neue Fahrzeugmeldung "
                "wurde entdeckt."
            )
        )

    if contains_any(
        article_text,
        [
            "free",
            "reward",
            "gift",
            "belohnung",
            "geschenk",
            "kostenlos"
        ]
    ):

        category_pairs.append(
            (
                "🎁 **NEUE BELOHNUNG**",
                "Eine neue Belohnung oder "
                "kostenlose Sache wurde entdeckt."
            )
        )

    category_lines = []

    for heading, content in category_pairs:

        category_lines.append(
            f"{heading}\n{content}"
        )

    thursday_message = build_thursday_message(
        article,
        category_lines
    )

    print("")
    print(
        "📤 Sende Donnerstag-Design..."
    )

    send_discord(
        thursday_message
    )

    print(
        "✅ Donnerstag-Test gesendet."
    )

    print("")
    print("=" * 70)
    print(
        "🏁 BEIDE TESTS ABGESCHLOSSEN"
    )
    print("=" * 70)

    print("")
    print(
        "ℹ️ Kein Veröffentlichungsstatus "
        "wurde gespeichert."
    )


# ============================================================
# HAUPTPROGRAMM
# ============================================================

async def main():

    now = datetime.now(
        VIENNA
    )

    print("")
    print("=" * 70)
    print("🚗 LS-INSIDER")
    print("=" * 70)

    print(
        f"🕐 Wien: "
        f"{now.strftime('%d.%m.%Y %H:%M:%S')}"
    )

    # ========================================================
    # TEST
    # ========================================================

    if TEST_MODE:

        await run_test()

        return

    # ========================================================
    # NORMALBETRIEB
    # ========================================================

    weekday = now.weekday()

    print(
        f"📅 Wochentag: {weekday}"
    )

    # Mittwoch
    if weekday == 2:

        print("")
        print(
            "🟢 MITTWOCH-MODUS"
        )

        gtabase_data = await get_gtabase_data()

        run_wednesday(
            gtabase_data
        )

    # Donnerstag
    elif weekday == 3:

        print("")
        print(
            "🟣 DONNERSTAG-MODUS"
        )

        await run_thursday()

    # Andere Tage
    else:

        print("")
        print(
            "ℹ️ Heute ist kein "
            "LS-Insider-Tag."
        )

    print("")
    print("=" * 70)
    print(
        "🏁 LS-INSIDER ABGESCHLOSSEN"
    )
    print("=" * 70)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except Exception as error:

        print("")
        print(
            "❌ LS-INSIDER FEHLER:"
        )

        print(
            repr(error)
        )

        raise
