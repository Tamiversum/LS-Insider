import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

GTABASE_URL = (
    "https://www.gtabase.com/"
    "gta-online/weekly-update-bonuses-discounts"
)

ROCKSTAR_URL = (
    "https://www.rockstargames.com/de/newswire?tag_id=735"
)

STATE_FILE = "weekly_state.json"

VIENNA = ZoneInfo("Europe/Vienna")

DISCORD_LIMIT = 1900

# ============================================================
# TESTMODUS
# ============================================================
# True = Mittwoch- UND Donnerstag-Post werden jetzt getestet.
# Nach dem Test auf False setzen.
TEST_MODE = True


# ============================================================
# ALLGEMEINE HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
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

        if response.status_code not in (200, 204):
            raise RuntimeError(
                "Discord-Webhook-Fehler: "
                f"{response.status_code} "
                f"{response.text}"
            )

        print("✅ Discord-Nachricht erfolgreich gesendet.")


# ============================================================
# GTABASE
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
            return clean_text(
                match.group(0)
            )

    return ""


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
            return candidate

    return candidates[0] if candidates else ""


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

        value = clean_text(
            match.group(0)
        )

        value = re.sub(
            r"(?i)(community series jobs)\s+3x",
            r"\1 – 3x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(clubhouse contracts)\s+2x",
            r"\1 – 2x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(hasta la vista)\s+2x",
            r"\1 – 2x GTA$ & RP",
            value
        )

        value = re.sub(
            r"(?i)(every bullet counts)\s+2x",
            r"\1 – 2x GTA$ & RP",
            value
        )

        results.append(value)

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
            results.append(
                clean_text(
                    match.group(0)
                )
            )

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
        name = clean_text(name)

        if any(
            blocked_text.lower() in name.lower()
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
            f"{name} {original} {discount} {sale}"
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

    return unique_items(vehicles)


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
            return clean_text(
                match.group(0)
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
                f"✅ GTABase geladen: {page.url}"
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
            print("🔎 GTABASE-DATEN")
            print(f"📰 Titel: {title}")
            print(f"📅 Zeitraum: {period}")
            print(f"💰 Boni: {len(bonuses)}")
            print(f"🎁 Kostenlos: {len(free_items)}")
            print(f"🚗 Fahrzeuge: {len(vehicles)}")
            print(f"🏷️ Rabatte: {len(discounts)}")
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
# MITTWOCH-DESIGN
# ============================================================

def build_wednesday_message(data):
    title = data.get(
        "title",
        "GTA Online Weekly Update"
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

    lines = [
        "🗞️ **LS-INSIDER**",
        "",
        "# **DIESE WOCHE IN LOS SANTOS**",
        "",
        f"**{period}**",
        "",
        "📰 **EVENT**",
        title
    ]

    if bonuses:
        lines.extend([
            "",
            "💰 **BONI**"
        ])

        for item in bonuses:
            lines.append(
                f"• {item}"
            )

    if free_items:
        lines.extend([
            "",
            "🎁 **KOSTENLOSE SACHEN**"
        ])

        for item in free_items:
            lines.append(
                f"• {item}"
            )

    if vehicles:
        lines.extend([
            "",
            "🚗 **FAHRZEUGE**"
        ])

        for item in vehicles:
            lines.append(
                f"• {item}"
            )

    if discounts:
        lines.extend([
            "",
            "🏷️ **RABATTE**"
        ])

        for item in discounts:
            lines.append(
                f"• {item}"
            )

    if challenge:
        lines.extend([
            "",
            "🏆 **WEEKLY CHALLENGE**",
            f"• {challenge}"
        ])

    lines.extend([
        "",
        "📅 **AKTUELLE WOCHE**",
        f"• {period}",
        "",
        "🔗 **VOLLSTÄNDIGER LINK**",
        f"<{GTABASE_URL}>"
    ])

    return "\n".join(lines)


# ============================================================
# ROCKSTAR
# ============================================================

def normalize_rockstar_title(title):
    title = clean_text(title)

    title = re.sub(
        r"^GTA Online:\s*",
        "",
        title,
        flags=re.IGNORECASE
    )

    return title


async def get_rockstar_news():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="de-DE"
        )

        try:
            print("🌐 Öffne Rockstar Newswire...")

            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                7000
            )

            print(
                f"✅ Rockstar geladen: {page.url}"
            )

            articles = await page.locator(
                "a[href*='/newswire/']"
            ).all()

            found = []

            for article in articles:
                try:
                    title = clean_text(
                        await article.inner_text()
                    )

                    href = await article.get_attribute(
                        "href"
                    )

                    if not title or not href:
                        continue

                    if "/newswire/" not in href:
                        continue

                    if href.startswith("/"):
                        href = (
                            "https://www.rockstargames.com"
                            + href
                        )

                    found.append({
                        "title": normalize_rockstar_title(
                            title
                        ),
                        "url": href
                    })

                except Exception:
                    continue

            unique = []
            seen = set()

            for article in found:
                key = (
                    article["title"],
                    article["url"]
                )

                if key in seen:
                    continue

                seen.add(key)
                unique.append(article)

            for article in unique:
                title = article["title"].lower()

                if (
                    "gta online" in title
                    or "gta online" in article["url"].lower()
                ):
                    print(
                        f"📰 Rockstar: {article['title']}"
                    )

                    return article

            if unique:
                print(
                    f"📰 Rockstar: {unique[0]['title']}"
                )

                return unique[0]

            return None

        finally:
            await browser.close()


# ============================================================
# DONNERSTAG-DESIGN
# ============================================================

def build_thursday_message(article):
    title = article.get(
        "title",
        "Neue Rockstar-Meldung"
    )

    url = article.get(
        "url",
        ROCKSTAR_URL
    )

    lines = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        "",
        "🤫 **Psst... eine neue Meldung ist eingetroffen.**",
        "",
        "Unsere Informanten haben etwas Neues "
        "in Los Santos entdeckt.",
        "",
        "🆕 **NEU**",
        f"• {title}",
        "",
        "📡 **INFORMANTENBERICHT**",
        "• Neue Informationen wurden im "
        "Rockstar Newswire entdeckt.",
        "",
        "💬 *„Wir behalten Los Santos im Auge. "
        "Sobald sich etwas bewegt, wissen wir es.“*",
        "",
        "🔗 **ORIGINALMELDUNG ÖFFNEN**",
        f"<{url}>"
    ]

    return "\n".join(lines)


# ============================================================
# TESTMODUS
# ============================================================

async def run_test():
    print("")
    print("=" * 70)
    print("🧪 LS-INSIDER TESTMODUS")
    print("=" * 70)

    # --------------------------------------------------------
    # MITTWOCH TEST
    # --------------------------------------------------------

    print("")
    print("🗞️ TEST 1/2 – MITTWOCH-DESIGN")
    print("🌐 Lade aktuelle GTABase-Daten...")

    gtabase_data = await get_gtabase_data()

    if not gtabase_data.get("period"):
        raise RuntimeError(
            "GTABase konnte keinen Zeitraum erkennen."
        )

    wednesday_message = build_wednesday_message(
        gtabase_data
    )

    print("")
    print("📤 Sende Mittwoch-Design an Discord...")

    send_discord(
        wednesday_message
    )

    # --------------------------------------------------------
    # KURZE PAUSE
    # --------------------------------------------------------

    print("")
    print("⏳ Kurze Pause vor dem zweiten Test...")
    await asyncio.sleep(3)

    # --------------------------------------------------------
    # DONNERSTAG TEST
    # --------------------------------------------------------

    print("")
    print("🕵️ TEST 2/2 – DONNERSTAG-DESIGN")
    print("🌐 Lade aktuelle Rockstar-Meldung...")

    article = await get_rockstar_news()

    if not article:
        raise RuntimeError(
            "Keine Rockstar-Meldung gefunden."
        )

    thursday_message = build_thursday_message(
        article
    )

    print("")
    print("📤 Sende Donnerstag-Design an Discord...")

    send_discord(
        thursday_message
    )

    print("")
    print("=" * 70)
    print("✅ BEIDE TESTS ERFOLGREICH GESENDET")
    print("=" * 70)


# ============================================================
# NORMALER MITTWOCH-BETRIEB
# ============================================================

def run_wednesday(data):
    state = load_state()

    period = data.get(
        "period",
        ""
    )

    if not period:
        raise RuntimeError(
            "GTABase konnte keinen Zeitraum erkennen."
        )

    signature = make_signature(data)

    if state.get(
        "wednesday_signature"
    ) == signature:

        print(
            "ℹ️ Diese Eventwoche wurde bereits gepostet."
        )

        return

    send_discord(
        build_wednesday_message(data)
    )

    state["week_period"] = period
    state["wednesday_signature"] = signature
    state["wednesday_data"] = data

    save_state(state)

    print(
        "✅ Mittwoch-Ausgabe wurde gepostet."
    )


# ============================================================
# NORMALER DONNERSTAG-BETRIEB
# ============================================================

def run_thursday(article):
    if not article:
        print(
            "ℹ️ Keine neue Rockstar-Meldung gefunden."
        )

        return

    state = load_state()

    signature = make_signature(
        article
    )

    if state.get(
        "thursday_signature"
    ) == signature:

        print(
            "ℹ️ Diese Rockstar-Meldung wurde bereits gepostet."
        )

        return

    send_discord(
        build_thursday_message(article)
    )

    state["thursday_signature"] = signature
    state["thursday_article"] = article

    save_state(state)

    print(
        "✅ Donnerstag-Geheimbericht wurde gepostet."
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

    # --------------------------------------------------------
    # TESTMODUS
    # --------------------------------------------------------

    if TEST_MODE:
        print("")
        print(
            "🧪 TESTMODUS IST AKTIV."
        )

        print(
            "➡️ Beide Designs werden einmal an Discord gesendet."
        )

        await run_test()

        return

    # --------------------------------------------------------
    # NORMALBETRIEB
    # --------------------------------------------------------

    weekday = now.weekday()

    print(
        f"📅 Wochentag: {weekday}"
    )

    if weekday == 2:
        print("")
        print(
            "🗞️ MITTWOCH – DIESE WOCHE IN LOS SANTOS"
        )

        data = await get_gtabase_data()

        if not data.get("period"):
            raise RuntimeError(
                "GTABase-Daten sind unvollständig."
            )

        run_wednesday(
            data
        )

    elif weekday == 3:
        print("")
        print(
            "🕵️ DONNERSTAG – GEHEIMBERICHT"
        )

        article = await get_rockstar_news()

        run_thursday(
            article
        )

    else:
        print("")
        print(
            "ℹ️ Heute ist weder Mittwoch noch Donnerstag."
        )

        print(
            "⏭️ Keine Discord-Ausgabe erforderlich."
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
