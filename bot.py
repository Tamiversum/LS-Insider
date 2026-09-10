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

# TESTMODUS:
# True = Der Bot behandelt den heutigen Tag als Mittwoch.
# False = Der echte Wochentag wird verwendet.
TEST_WEDNESDAY = True


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

        if response.status_code not in (
            200,
            204
        ):
            raise RuntimeError(
                "Discord-Webhook-Fehler: "
                f"{response.status_code} "
                f"{response.text}"
            )


def detect_period(text):

    patterns = [

        r"\b("
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")\s+\d{1,2}"
        r"\s*[-–]\s*"
        r"("
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")\s+\d{1,2}\b",

        r"\b("
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")\s+\d{1,2}"
        r"\s*[-–]\s*\d{1,2}\b"
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


async def read_page_items(page):

    elements = await page.locator(
        "li, td"
    ).all()

    results = []

    for element in elements:

        try:

            text = clean_text(
                await element.inner_text()
            )

            if not text:
                continue

            if len(text) < 3:
                continue

            if len(text) > 400:
                continue

            results.append(text)

        except Exception:
            pass

    return unique_items(results)


def extract_bonus_items(items):

    results = []

    for item in items:

        upper = item.upper()

        if re.search(
            r"\b[2-9]X\b",
            upper
        ):

            if "%" not in item:
                results.append(item)

    return unique_items(results)


def extract_discount_items(items):

    results = []

    for item in items:

        if "%" not in item:
            continue

        if re.search(
            r"-\d{1,2}%",
            item
        ):
            results.append(item)

    return unique_items(results)


def extract_free_items(items):

    results = []

    for item in items:

        upper = item.upper()

        if "FREE" in upper:
            results.append(item)

        elif re.search(
            r"\$\s*0\b",
            item
        ):
            results.append(item)

    return unique_items(results)


def extract_vehicle_items(items):

    results = []

    vehicle_keywords = [
        "S95",
        "Nimbus",
        "Vindicator",
        "Baller",
        "Cheetah",
        "Vivanite",
        "Penumbra",
        "Patriot",
        "Shinobi",
        "Vortex",
        "Growler",
        "Defiler",
        "Aleutian",
        "Warrener",
        "Rampant Rocket",
        "vehicle",
        "test ride",
        "showroom",
        "podium",
        "prize ride"
    ]

    for item in items:

        lower = item.lower()

        if any(
            keyword.lower() in lower
            for keyword in vehicle_keywords
        ):
            results.append(item)

    return unique_items(results)


def extract_weekly_challenge(text):

    patterns = [

        r"Earn GTA\$1,000,000[^.]*\.",

        r"Earn GTA\$[\d,]+[^.]*\.",

        r"Weekly Challenge[^.]*\."
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

            items = await read_page_items(
                page
            )

            bonuses = extract_bonus_items(
                items
            )

            free_items = extract_free_items(
                items
            )

            discounts = extract_discount_items(
                items
            )

            vehicles = extract_vehicle_items(
                items
            )

            challenge = extract_weekly_challenge(
                body_text
            )

            return {
                "title": title,
                "period": period,
                "bonuses": bonuses,
                "free_items": free_items,
                "discounts": discounts,
                "vehicles": vehicles,
                "weekly_challenge": challenge
            }

        finally:
            await browser.close()


def build_weekly_message(data):

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

    discounts = data.get(
        "discounts",
        []
    )

    vehicles = data.get(
        "vehicles",
        []
    )

    challenge = data.get(
        "weekly_challenge",
        ""
    )

    lines = []

    lines.append(
        "🚗 **LS-INSIDER – GTA ONLINE EVENTWOCHE**"
    )

    lines.append("")

    lines.append(
        "📰 **EVENT**"
    )

    lines.append(title)

    lines.append("")

    lines.append(
        "📅 **ZEITRAUM**"
    )

    lines.append(period)

    lines.append("")

    lines.append(
        "💰 **BONI**"
    )

    if bonuses:

        for item in bonuses[:12]:
            lines.append(
                f"• {item}"
            )

    else:
        lines.append(
            "• Keine Boni erkannt."
        )

    lines.append("")

    lines.append(
        "🎁 **KOSTENLOSE SACHEN**"
    )

    if free_items:

        for item in free_items[:10]:
            lines.append(
                f"• {item}"
            )

    else:
        lines.append(
            "• Keine kostenlosen Sachen erkannt."
        )

    lines.append("")

    lines.append(
        "🚗 **FAHRZEUGE**"
    )

    if vehicles:

        for item in vehicles[:15]:
            lines.append(
                f"• {item}"
            )

    else:
        lines.append(
            "• Keine Fahrzeugdaten erkannt."
        )

    lines.append("")

    lines.append(
        "🏷️ **RABATTE**"
    )

    if discounts:

        for item in discounts[:20]:
            lines.append(
                f"• {item}"
            )

    else:
        lines.append(
            "• Keine Rabatte erkannt."
        )

    lines.append("")

    lines.append(
        "🏆 **WEEKLY CHALLENGE**"
    )

    if challenge:

        lines.append(
            f"• {challenge}"
        )

    else:
        lines.append(
            "• Keine Weekly Challenge erkannt."
        )

    lines.append("")

    lines.append(
        "📅 **AKTUELLE WOCHE**"
    )

    lines.append(
        f"• {period}"
    )

    lines.append("")

    lines.append(
        "🔗 **QUELLE**"
    )

    lines.append(GTABASE_URL)

    return "\n".join(lines)


def run_wednesday(gtabase_data):

    state = load_state()

    period = gtabase_data.get(
        "period",
        ""
    )

    if not period:
        raise RuntimeError(
            "GTABase konnte keinen Zeitraum erkennen."
        )

    signature = make_signature(
        gtabase_data
    )

    previous_signature = state.get(
        "wednesday_signature"
    )

    if previous_signature == signature:

        print(
            "ℹ️ Diese Eventwoche wurde bereits gepostet."
        )

        return

    print(
        "📨 Neue GTABase-Eventwoche gefunden."
    )

    message = build_weekly_message(
        gtabase_data
    )

    print(
        "📤 Sende Eventwoche an Discord..."
    )

    send_discord(message)

    state["week_period"] = period
    state["wednesday_signature"] = signature
    state["wednesday_data"] = gtabase_data

    save_state(state)

    print(
        "✅ Mittwoch-Ausgabe wurde gepostet."
    )


async def get_rockstar_data():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="de-DE"
        )

        try:

            print(
                "🌐 Öffne Rockstar Newswire..."
            )

            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                7000
            )

            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text()
            )

            links = await page.locator(
                "a"
            ).all()

            candidates = []

            for link in links:

                try:

                    text = clean_text(
                        await link.inner_text()
                    )

                    href = await link.get_attribute(
                        "href"
                    )

                    if not text or not href:
                        continue

                    lower = text.lower()

                    if (
                        "gta online" in lower
                        or "event" in lower
                        or "bonus" in lower
                        or "rewards" in lower
                        or "business" in lower
                        or "race" in lower
                    ):

                        if href.startswith("/"):
                            href = (
                                "https://www.rockstargames.com"
                                + href
                            )

                        candidates.append(
                            {
                                "title": text,
                                "url": href
                            }
                        )

                except Exception:
                    pass

            clean_candidates = []

            for candidate in candidates:

                if candidate not in clean_candidates:
                    clean_candidates.append(candidate)

            if not clean_candidates:

                return {
                    "title": "",
                    "url": ROCKSTAR_URL,
                    "text": body_text
                }

            latest = clean_candidates[0]

            return {
                "title": latest["title"],
                "url": latest["url"],
                "text": body_text
            }

        finally:
            await browser.close()


def extract_relevant_rockstar_data(text):

    lower = text.lower()

    multipliers = sorted(
        set(
            re.findall(
                r"\b[2-9]x\b",
                lower
            )
        )
    )

    discounts = sorted(
        set(
            re.findall(
                r"-\d{1,2}%",
                text
            )
        )
    )

    gta_money = sorted(
        set(
            re.findall(
                r"GTA\$[\d,]+",
                text,
                re.IGNORECASE
            )
        )
    )

    return {
        "multipliers": multipliers,
        "discounts": discounts,
        "gta_money": gta_money
    }


def compare_week_data(
    gtabase_data,
    rockstar_data
):

    gta_relevant = {

        "bonuses": sorted(
            gtabase_data.get(
                "bonuses",
                []
            )
        ),

        "discounts": sorted(
            gtabase_data.get(
                "discounts",
                []
            )
        ),

        "free_items": sorted(
            gtabase_data.get(
                "free_items",
                []
            )
        )
    }

    rockstar_relevant = (
        extract_relevant_rockstar_data(
            rockstar_data.get(
                "text",
                ""
            )
        )
    )

    gta_multipliers = sorted(
        set(
            re.findall(
                r"\b[2-9]x\b",
                " ".join(
                    gta_relevant["bonuses"]
                ).lower()
            )
        )
    )

    gta_discounts = sorted(
        set(
            re.findall(
                r"-\d{1,2}%",
                " ".join(
                    gta_relevant["discounts"]
                )
            )
        )
    )

    differences = []

    if (
        gta_multipliers
        and rockstar_relevant["multipliers"]
        and gta_multipliers
        != rockstar_relevant["multipliers"]
    ):
        differences.append(
            "Die Bonus-Multiplikatoren unterscheiden sich."
        )

    if (
        gta_discounts
        and rockstar_relevant["discounts"]
        and gta_discounts
        != rockstar_relevant["discounts"]
    ):
        differences.append(
            "Die Rabattangaben unterscheiden sich."
        )

    return differences


def build_special_message(
    rockstar_data,
    differences
):

    title = rockstar_data.get(
        "title",
        "Offizieller Rockstar-Artikel"
    )

    url = rockstar_data.get(
        "url",
        ROCKSTAR_URL
    )

    lines = []

    lines.append(
        "🚨 **LS-INSIDER – ROCKSTAR SONDERAUSGABE**"
    )

    lines.append("")

    lines.append(
        "📰 **OFFIZIELLE ROCKSTAR-KONTROLLE**"
    )

    lines.append(title)

    lines.append("")

    lines.append(
        "⚠️ **ABWEICHUNG ZUR MITTWOCH-AUSGABE**"
    )

    if differences:

        for difference in differences:
            lines.append(
                f"• {difference}"
            )

    else:

        lines.append(
            "• Rockstar hat die Wocheninformationen "
            "aktualisiert bzw. bestätigt."
        )

    lines.append("")

    lines.append(
        "🔗 **OFFIZIELLER ROCKSTAR-LINK**"
    )

    lines.append(url)

    return "\n".join(lines)


def run_thursday(
    gtabase_data,
    rockstar_data
):

    state = load_state()

    signature = make_signature(
        rockstar_data
    )

    previous_signature = state.get(
        "thursday_signature"
    )

    if previous_signature == signature:

        print(
            "ℹ️ Dieser Rockstar-Stand wurde "
            "bereits kontrolliert."
        )

        return

    differences = compare_week_data(
        gtabase_data,
        rockstar_data
    )

    if not differences:

        print(
            "✅ Rockstar-Daten stimmen "
            "mit der Mittwochsausgabe überein."
        )

        state["thursday_signature"] = signature

        save_state(state)

        return

    print(
        "🚨 Abweichung gefunden – "
        "Sonderausgabe wird gepostet."
    )

    message = build_special_message(
        rockstar_data,
        differences
    )

    send_discord(message)

    state["thursday_signature"] = signature

    save_state(state)

    print(
        "✅ Rockstar-Sonderausgabe wurde gepostet."
    )


async def main():

    now = datetime.now(VIENNA)

    if TEST_WEDNESDAY:

        current_weekday = 2

        print("")
        print(
            "🧪 TESTMODUS AKTIV"
        )

        print(
            "➡️ Heute wird als MITTWOCH behandelt."
        )

    else:

        current_weekday = now.weekday()

    print("")
    print("=" * 70)
    print("🚗 LS-INSIDER")
    print("=" * 70)

    print(
        f"🕐 Wien: "
        f"{now.strftime('%d.%m.%Y %H:%M:%S')}"
    )

    print(
        f"📅 Verwendeter Wochentag: "
        f"{current_weekday}"
    )

    gtabase_data = await get_gtabase_data()

    if not gtabase_data.get("period"):

        raise RuntimeError(
            "GTABase-Daten sind unvollständig."
        )

    print(
        f"📰 {gtabase_data.get('title')}"
    )

    print(
        f"📅 {gtabase_data.get('period')}"
    )

    if current_weekday == 2:

        print("")
        print(
            "🟢 Mittwoch-Modus"
        )

        run_wednesday(
            gtabase_data
        )

    elif current_weekday == 3:

        print("")
        print(
            "🟣 Donnerstag-Modus"
        )

        rockstar_data = (
            await get_rockstar_data()
        )

        run_thursday(
            gtabase_data,
            rockstar_data
        )

    else:

        print("")
        print(
            "ℹ️ Heute ist kein geplanter "
            "LS-Insider-Postingtag."
        )

    print("")
    print("=" * 70)
    print("🏁 LS-INSIDER ABGESCHLOSSEN")
    print("=" * 70)


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
