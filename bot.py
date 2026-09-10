import asyncio
import re
from playwright.async_api import async_playwright


# =========================================================
# GTABASE
# =========================================================

GTABASE_URL = (
    "https://www.gtabase.com/"
    "gta-online/weekly-update-bonuses-discounts"
)


# =========================================================
# HILFSFUNKTIONEN
# =========================================================

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


def print_list(title, items):
    print("")
    print("=" * 70)
    print(title)
    print("=" * 70)

    if not items:
        print("Keine Einträge gefunden.")
        return

    for item in items:
        print(f"• {item}")


# =========================================================
# ZEITRAUM
# =========================================================

def detect_period(text):

    patterns = [

        # September 10 - September 16
        r"\b("
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")\s+\d{1,2}"
        r"\s*[-–]\s*"
        r"("
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")\s+\d{1,2}\b",

        # September 10 - 16
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
            return clean_text(match.group(0))

    return ""


# =========================================================
# WOCHENARTIKEL
# =========================================================

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

    candidates = unique_items(candidates)

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


# =========================================================
# BONUS-DATEN
# =========================================================

def extract_bonus_items(items):

    results = []

    bonus_keywords = [
        "3x",
        "2x",
        "4x",
        "5x",
        "bonus"
    ]

    for item in items:

        upper = item.upper()

        if any(
            keyword.upper() in upper
            for keyword in bonus_keywords
        ):

            if "$" not in item or "FREE" not in upper:
                results.append(item)

    return unique_items(results)


# =========================================================
# RABATTE
# =========================================================

def extract_discount_items(items):

    results = []

    for item in items:

        if "%" not in item:
            continue

        # Wir suchen typische Rabatt-Zeilen.
        if re.search(
            r"-\d{1,2}%",
            item
        ):

            results.append(item)

    return unique_items(results)


# =========================================================
# KOSTENLOSE SACHEN
# =========================================================

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


# =========================================================
# WOCHEN-CHALLENGE
# =========================================================

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


# =========================================================
# FAHRZEUGE / SHOWROOMS
# =========================================================

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


# =========================================================
# ALLE RELEVANTEN LISTEN/TABELLEN AUSLESEN
# =========================================================

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


# =========================================================
# GTABASE AUSLESEN
# =========================================================

async def get_gtabase_data():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            locale="en-US"
        )

        try:

            print("")
            print("=" * 70)
            print("🚗 LS-INSIDER – GTABASE WOCHEN-TEST")
            print("=" * 70)

            print("")
            print("🔎 Öffne GTABase...")

            await page.goto(
                GTABASE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                5000
            )

            print(
                f"✅ Seite geladen: {page.url}"
            )

            # -------------------------------------------------
            # TITEL
            # -------------------------------------------------

            title = await find_weekly_title(
                page
            )

            print("")
            print(
                f"📰 Wochenartikel:"
            )

            print(
                title or "NICHT GEFUNDEN"
            )

            # -------------------------------------------------
            # SEITENTEXT
            # -------------------------------------------------

            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text()
            )

            print("")
            print(
                f"📄 Seiteninhalt: "
                f"{len(body_text)} Zeichen"
            )

            # -------------------------------------------------
            # ZEITRAUM
            # -------------------------------------------------

            period = detect_period(
                body_text
            )

            print("")
            print(
                f"📅 Zeitraum: "
                f"{period or 'NICHT GEFUNDEN'}"
            )

            # -------------------------------------------------
            # LISTEN / TABELLEN
            # -------------------------------------------------

            all_items = await read_page_items(
                page
            )

            print("")
            print(
                f"📋 Gefundene Datenzeilen: "
                f"{len(all_items)}"
            )

            # -------------------------------------------------
            # BONI
            # -------------------------------------------------

            bonuses = extract_bonus_items(
                all_items
            )

            print_list(
                "💰 GTA$ & RP BONI",
                bonuses
            )

            # -------------------------------------------------
            # KOSTENLOSE SACHEN
            # -------------------------------------------------

            free_items = extract_free_items(
                all_items
            )

            print_list(
                "🎁 KOSTENLOSE SACHEN",
                free_items
            )

            # -------------------------------------------------
            # RABATTE
            # -------------------------------------------------

            discounts = extract_discount_items(
                all_items
            )

            print_list(
                "🏷️ RABATTE",
                discounts
            )

            # -------------------------------------------------
            # FAHRZEUGE
            # -------------------------------------------------

            vehicles = extract_vehicle_items(
                all_items
            )

            print_list(
                "🚗 FAHRZEUGE / SHOWROOMS",
                vehicles
            )

            # -------------------------------------------------
            # WEEKLY CHALLENGE
            # -------------------------------------------------

            challenge = extract_weekly_challenge(
                body_text
            )

            print_list(
                "🏆 WEEKLY CHALLENGE",
                [challenge] if challenge else []
            )

            # -------------------------------------------------
            # TESTERGEBNIS
            # -------------------------------------------------

            print("")
            print("=" * 70)
            print("🏁 GTABASE-TEST ABGESCHLOSSEN")
            print("=" * 70)

            print("")
            print("❌ Discord wurde NICHT angesprochen.")
            print("❌ last_article.txt wurde NICHT verändert.")
            print("")
            print("✅ Dieser Test dient nur dazu,")
            print("   die Daten sauber zu sortieren.")

            return True

        finally:

            await browser.close()


# =========================================================
# HAUPTPROGRAMM
# =========================================================

async def main():

    try:

        success = await get_gtabase_data()

        if success:

            print("")
            print(
                "✅ TEST ERFOLGREICH BEENDET"
            )

    except Exception as error:

        print("")
        print(
            "❌ FEHLER BEIM GTABASE-TEST:"
        )

        print(
            repr(error)
        )

        raise


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
