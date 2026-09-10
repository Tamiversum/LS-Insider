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
#
# True:
#   Mittwoch UND Donnerstag werden sofort getestet.
#
# False:
#   Der normale Wochentag entscheidet automatisch.
#
# WICHTIG:
# Für unseren aktuellen Test bitte TRUE lassen.
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


# ============================================================
# DEUTSCHE ÜBERSETZUNG
# ============================================================

TRANSLATIONS = {

    # Allgemein
    "Weekly Update": "Eventwoche",
    "GTA Online Weekly Update": "GTA Online Eventwoche",
    "Bonuses & Discounts": "Boni & Rabatte",
    "Bonuses": "Boni",
    "Discounts": "Rabatte",

    # GTA-Begriffe
    "GTA$ & RP": "GTA$ & RP",
    "GTA$": "GTA$",
    "RP": "RP",
    "Free": "Kostenlos",
    "FREE": "KOSTENLOS",
    "Reward": "Belohnung",
    "Rewards": "Belohnungen",
    "Gift": "Geschenk",
    "Gifts": "Geschenke",

    # Fahrzeuge
    "New Vehicle": "Neues Fahrzeug",
    "New Vehicles": "Neue Fahrzeuge",
    "Supercar": "Supersportwagen",
    "Sports Car": "Sportwagen",
    "Muscle Car": "Muscle-Car",
    "Motorcycle": "Motorrad",
    "Motorcycles": "Motorräder",
    "Vehicle": "Fahrzeug",
    "Vehicles": "Fahrzeuge",

    # News
    "Early Access": "Vorabzugang",
    "Members Enjoy": "Mitglieder erhalten",
    "One Week of Early Access": "eine Woche Vorabzugang",
    "New": "Neu",
    "Changes": "Änderungen",
    "Change": "Änderung",

    # Aktionen
    "Complete": "Schließt ab",
    "Earn": "Verdient",
    "Win": "Gewinnt",
    "Get": "Erhaltet",
    "Claim": "Sichert euch",
    "Available": "Verfügbar",

    # Zeit
    "September": "September",
    "October": "Oktober",
    "November": "November",
    "December": "Dezember",
    "January": "Januar",
    "February": "Februar",
    "March": "März",
    "April": "April",
    "May": "Mai",
    "June": "Juni",
    "July": "Juli",
    "August": "August"
}


def translate_text(text):

    if not text:
        return ""

    result = text

    # Längere Begriffe zuerst ersetzen
    replacements = sorted(
        TRANSLATIONS.items(),
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

    # Typische Rockstar-Formulierungen
    result = re.sub(
        r"\bGTA Online\s+(\d{1,2}\.\s+\w+\s+\d{4})\s+",
        r"GTA Online \1 ",
        result,
        flags=re.IGNORECASE
    )

    result = result.replace(
        "Enjoy One Week",
        "erhaltet eine Woche"
    )

    result = result.replace(
        "enjoy one week",
        "erhaltet eine Woche"
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

            return translate_text(value)

    return ""


# ============================================================
# GTABASE
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
            return translate_text(candidate)

    if candidates:

        return translate_text(
            candidates[0]
        )

    return "GTA Online Eventwoche"


def extract_bonus_items(text):

    results = []

    patterns = [

        r"Community Series Jobs\s+3x",

        r"Clubhouse Contracts\s+2x",

        r"Hasta La Vista\s+2x",

        r"Every Bullet Counts\s+2x"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        value = match.group(0)

        value = re.sub(
            r"(?i)(community series jobs)\s+3x",
            r"Community Series Jobs – 3x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(clubhouse contracts)\s+2x",
            r"Clubhouse Contracts – 2x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(hasta la vista)\s+2x",
            r"Hasta La Vista – 2x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(every bullet counts)\s+2x",
            r"Every Bullet Counts – 2x GTA$ & RP",
            value
        )

        results.append(
            translate_text(value)
        )

    return unique_items(results)


def extract_free_items(text):

    results = []

    patterns = [

        r"Grapeseed Clubhouse\s+\$225,000\s+FREE\s+\$0",

        r"Complete at least one Weekly Challenge.*?"
        r"September 24-30"
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

            value = translate_text(
                value
            )

            results.append(value)

    return unique_items(results)


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

    matches = pattern.findall(text)

    for name, original, discount, sale in matches:

        name = clean_text(name)

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

        result = (
            f"{name} "
            f"{original} "
            f"{discount} "
            f"{sale}"
        )

        results.append(
            translate_text(result)
        )

    return unique_items(results)


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
            vehicles.append(name)

    return unique_items(
        vehicles
    )


def extract_weekly_challenge(text):

    patterns = [

        r"Earn GTA\$1,000,000"
        r"[^.]*?"
        r"Junk Tracksuit",

        r"Earn GTA\$[\d,]+"
        r"[^.]*?"
        r"Junk Tracksuit"
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

            return translate_text(
                value
            )

    return ""


async def get_gtabase_data():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="en-US"
        )

        try:

            print("🌐 Öffne GTABase...")

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
            print("🔎 ERKANNTE GTABASE-DATEN")
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
                f"🎁 Kostenlos: "
                f"{len(free_items)}"
            )
            print(
                f"🚗 Fahrzeuge: "
                f"{len(vehicles)}"
            )
            print(
                f"🏷️ Rabatte: "
                f"{len(discounts)}"
            )
            print(
                f"🏆 Challenge: "
                f"{'Ja' if challenge else 'Nein'}"
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

    # --------------------------------------------------------
    # EVENT
    # --------------------------------------------------------

    lines.append(
        "📰 **EVENT**"
    )

    lines.append(
        title
    )

    lines.append("")

    # --------------------------------------------------------
    # BONI
    # --------------------------------------------------------

    if bonuses:

        lines.append(
            "💰 **BONI**"
        )

        for item in bonuses:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # --------------------------------------------------------
    # GESCHENKE / KOSTENLOSE SACHEN
    # --------------------------------------------------------

    if free_items:

        lines.append(
            "🎁 **GESCHENKE FÜR DIE COMMUNITY**"
        )

        for item in free_items:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # --------------------------------------------------------
    # FAHRZEUGE
    # --------------------------------------------------------

    if vehicles:

        lines.append(
            "🚗 **NEU AUF DEN STRASSEN**"
        )

        for item in vehicles:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # --------------------------------------------------------
    # RABATTE
    # --------------------------------------------------------

    if discounts:

        lines.append(
            "🏷️ **SONDERANGEBOTE**"
        )

        for item in discounts:

            lines.append(
                f"• {item}"
            )

        lines.append("")

    # --------------------------------------------------------
    # WEEKLY CHALLENGE
    # --------------------------------------------------------

    if challenge:

        lines.append(
            "🏆 **WEEKLY CHALLENGE**"
        )

        lines.append(
            f"• {challenge}"
        )

        lines.append("")

    # --------------------------------------------------------
    # AKTUELLE WOCHE
    # --------------------------------------------------------

    lines.append(
        "📅 **AKTUELLE WOCHE**"
    )

    lines.append(
        f"• {period}"
    )

    lines.append("")

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

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

    save_state(state)

    print("")
    print(
        "✅ Mittwoch-Ausgabe wurde gepostet."
    )


# ============================================================
# ROCKSTAR NEWSWIRE
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

                    text = clean_text(
                        await link.inner_text()
                    )

                    if not href:
                        continue

                    if not text:
                        continue

                    if "/newswire/" not in href:
                        continue

                    if "gta-online" not in (
                        text.lower()
                        + " "
                        + href.lower()
                    ):
                        continue

                    if href.startswith("/"):
                        full_url = (
                            "https://www.rockstargames.com"
                            + href
                        )

                    else:
                        full_url = href

                    candidates.append(
                        {
                            "title": text,
                            "url": full_url
                        }
                    )

                except Exception:
                    pass

            # Doppelte Links entfernen
            unique_candidates = []

            seen_urls = set()

            for candidate in candidates:

                url = candidate["url"]

                if url in seen_urls:
                    continue

                seen_urls.add(url)

                unique_candidates.append(
                    candidate
                )

            if not unique_candidates:

                raise RuntimeError(
                    "Keine GTA-Online-News "
                    "auf Rockstar gefunden."
                )

            latest = unique_candidates[0]

            article_url = latest["url"]
            article_title = clean_text(
                latest["title"]
            )

            print("")
            print(
                "📰 Gefundene Rockstar-Meldung:"
            )

            print(
                article_title
            )

            print(
                article_url
            )

            # ------------------------------------------------
            # ARTIKEL ÖFFNEN
            # ------------------------------------------------

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

            # Titel und Inhalt übersetzen
            german_title = translate_text(
                article_title
            )

            german_body = translate_text(
                article_body
            )

            return {
                "title": german_title,
                "original_title": article_title,
                "url": article_url,
                "body": german_body
            }

        finally:

            await browser.close()


# ============================================================
# DONNERSTAG – INFORMATIONEN ERKENNEN
# ============================================================

def contains_any(text, keywords):

    lower = text.lower()

    for keyword in keywords:

        if keyword.lower() in lower:
            return True

    return False


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
    # NEU
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

        previous_signature = make_signature(
            previous_article
        )

        current_signature = make_signature(
            article
        )

        if previous_signature != current_signature:

            categories.append(
                (
                    "⚠️ **ÄNDERUNG**",
                    "Die bestehende Rockstar-Meldung "
                    "wurde aktualisiert."
                )
            )

    # --------------------------------------------------------
    # BONUS
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
        body,
        bonus_keywords
    ):

        categories.append(
            (
                "💰 **NEUER BONUS**",
                "In der neuen Meldung wurden "
                "Bonusbelohnungen entdeckt."
            )
        )

    # --------------------------------------------------------
    # FAHRZEUG
    # --------------------------------------------------------

    vehicle_keywords = [
        "new vehicle",
        "new car",
        "new supercar",
        "supercar",
        "sportwagen",
        "supersportwagen",
        "vehicle",
        "fahrzeug",
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
    # BELOHNUNG
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

    return categories


# ============================================================
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

def build_thursday_message(
    article,
    categories
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
        "Unsere Informanten haben "
        "etwas Neues in Los Santos entdeckt."
    )

    lines.append("")

    # --------------------------------------------------------
    # DYNAMISCHE KATEGORIEN
    # --------------------------------------------------------

    for heading, content in categories:

        lines.append(
            heading
        )

        lines.append(
            content
        )

        lines.append("")

    # --------------------------------------------------------
    # ORIGINALTITEL
    # --------------------------------------------------------

    lines.append(
        "📡 **INFORMANTENBERICHT**"
    )

    lines.append(
        f"**{title}**"
    )

    lines.append("")

    # --------------------------------------------------------
    # ABSCHLUSS
    # --------------------------------------------------------

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

    categories = extract_thursday_categories(
        article,
        previous_article
    )

    # --------------------------------------------------------
    # KEINE RELEVANTE ÄNDERUNG
    # --------------------------------------------------------

    if not categories:

        print("")
        print(
            "ℹ️ Keine neue oder geänderte "
            "relevante Rockstar-Information."
        )

        return

    message = build_thursday_message(
        article,
        categories
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

    save_state(state)

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
        "Der Test sendet beide finalen Designs."
    )

    print(
        "Der Status wird NICHT gespeichert."
    )

    # --------------------------------------------------------
    # MITTWOCH TEST
    # --------------------------------------------------------

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
        "📤 Sende endgültiges "
        "Mittwoch-Design..."
    )

    send_discord(
        wednesday_message
    )

    print("")
    print(
        "✅ Mittwoch-Test gesendet."
    )

    # --------------------------------------------------------
    # DONNERSTAG TEST
    # --------------------------------------------------------

    print("")
    print(
        "🕵️ TEST 2/2 – DONNERSTAG"
    )

    article = await get_rockstar_news()

    # Im Test wird die aktuelle Meldung
    # garantiert als NEU behandelt.

    test_categories = [
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

    bonus_keywords = [
        "2x",
        "3x",
        "4x",
        "5x",
        "bonus",
        "bonuses",
        "gta$ & rp"
    ]

    if contains_any(
        article_text,
        bonus_keywords
    ):

        test_categories.append(
            (
                "💰 **NEUER BONUS**",
                "Bonusbelohnungen wurden "
                "in der Meldung erkannt."
            )
        )

    vehicle_keywords = [
        "vehicle",
        "new car",
        "new supercar",
        "supercar",
        "fahrzeug",
        "pegassi",
        "bravado",
        "grotti"
    ]

    if contains_any(
        article_text,
        vehicle_keywords
    ):

        test_categories.append(
            (
                "🚗 **NEUE FAHRZEUGMELDUNG**",
                "Eine neue Fahrzeugmeldung "
                "wurde erkannt."
            )
        )

    reward_keywords = [
        "free",
        "reward",
        "gift",
        "belohnung",
        "geschenk",
        "kostenlos"
    ]

    if contains_any(
        article_text,
        reward_keywords
    ):

        test_categories.append(
            (
                "🎁 **NEUE BELOHNUNG**",
                "Eine Belohnung oder kostenlose "
                "Sache wurde erkannt."
            )
        )

    thursday_message = build_thursday_message(
        article,
        test_categories
    )

    print("")
    print(
        "📤 Sende endgültiges "
        "Donnerstag-Design..."
    )

    send_discord(
        thursday_message
    )

    print("")
    print(
        "✅ Donnerstag-Test gesendet."
    )

    # --------------------------------------------------------
    # ENDE
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print(
        "🏁 BEIDE DESIGN-TESTS ABGESCHLOSSEN"
    )
    print("=" * 70)

    print("")
    print(
        "ℹ️ weekly_state.json wurde "
        "im Testmodus NICHT verändert."
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

    # --------------------------------------------------------
    # MITTWOCH
    # --------------------------------------------------------

    if weekday == 2:

        print("")
        print(
            "🟢 MITTWOCH-MODUS"
        )

        gtabase_data = await get_gtabase_data()

        run_wednesday(
            gtabase_data
        )

    # --------------------------------------------------------
    # DONNERSTAG
    # --------------------------------------------------------

    elif weekday == 3:

        print("")
        print(
            "🟣 DONNERSTAG-MODUS"
        )

        await run_thursday()

    # --------------------------------------------------------
    # ANDERE TAGE
    # --------------------------------------------------------

    else:

        print("")
        print(
            "ℹ️ Heute ist kein LS-Insider-Tag."
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
