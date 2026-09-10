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


def print_section(title, items):
    print("")
    print("=" * 60)
    print(title)
    print("=" * 60)

    if not items:
        print("Keine Einträge gefunden.")
        return

    for item in items:
        print(f"• {item}")


def unique_items(items):
    result = []

    for item in items:
        item = clean_text(item)

        if item and item not in result:
            result.append(item)

    return result


# =========================================================
# ZEITRAUM ERKENNEN
# =========================================================

def detect_period(text):

    patterns = [

        # Beispiel:
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

        # Beispiel:
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
            return clean_text(
                match.group(0)
            )

    return ""


# =========================================================
# TITEL DES WOCHENARTIKELS FINDEN
# =========================================================

async def find_weekly_title(page):

    selectors = [
        "h1",
        "h2",
        "h3"
    ]

    candidates = []

    for selector in selectors:

        elements = await page.locator(
            selector
        ).all()

        for element in elements:

            try:

                text = clean_text(
                    await element.inner_text()
                )

                if not text:
                    continue

                candidates.append(text)

            except Exception:
                pass

    candidates = unique_items(
        candidates
    )

    # Bevorzugt Titel mit typischen
    # Weekly-Update-Begriffen.
    keywords = [
        "weekly update",
        "weekly bonuses",
        "bonuses",
        "discounts",
        "gta online"
    ]

    for candidate in candidates:

        lower = candidate.lower()

        if any(
            keyword in lower
            for keyword in keywords
        ):
            return candidate

    if candidates:
        return candidates[0]

    return ""


# =========================================================
# RELEVANTE ABSCHNITTE AUSLESEN
# =========================================================

async def read_sections(page):

    sections = []

    headings = await page.locator(
        "h1, h2, h3, h4"
    ).all()

    for heading in headings:

        try:

            heading_text = clean_text(
                await heading.inner_text()
            )

            if not heading_text:
                continue

            heading_lower = (
                heading_text.lower()
            )

            # Nur interessante Bereiche
            interesting_keywords = [
                "bonus",
                "bonuses",
                "reward",
                "rewards",
                "discount",
                "discounts",
                "vehicle",
                "vehicles",
                "showroom",
                "luxury autos",
                "premium deluxe",
                "podium",
                "prize ride",
                "weekly challenge",
                "free",
                "free item",
                "gta+",
                "event"
            ]

            if not any(
                keyword in heading_lower
                for keyword in interesting_keywords
            ):
                continue

            texts = []

            current = heading

            # Die nächsten Elemente nach
            # der Überschrift untersuchen.
            for _ in range(20):

                current = current.locator(
                    "xpath=following-sibling::*[1]"
                )

                if await current.count() == 0:
                    break

                tag = await current.evaluate(
                    "(element) => "
                    "element.tagName.toLowerCase()"
                )

                if tag in (
                    "h1",
                    "h2",
                    "h3",
                    "h4"
                ):
                    break

                if tag in (
                    "p",
                    "li"
                ):

                    text = clean_text(
                        await current.inner_text()
                    )

                    if text:
                        texts.append(text)

            texts = unique_items(
                texts
            )

            if texts:

                sections.append({
                    "heading": heading_text,
                    "items": texts
                })

        except Exception:
            pass

    return sections


# =========================================================
# FALLBACK: LISTEN UND TABELLEN
# =========================================================

async def read_lists(page):

    results = []

    elements = await page.locator(
        "li, td"
    ).all()

    for element in elements:

        try:

            text = clean_text(
                await element.inner_text()
            )

            if not text:
                continue

            if len(text) < 3:
                continue

            if len(text) > 300:
                continue

            results.append(text)

        except Exception:
            pass

    return unique_items(
        results
    )


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
            print("=" * 60)
            print("🌐 LS-INSIDER GTABASE-TEST")
            print("=" * 60)

            print("")
            print(
                "🔎 Öffne GTABase..."
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
                f"✅ Seite geladen: "
                f"{page.url}"
            )

            # -------------------------------------------------
            # TITEL
            # -------------------------------------------------

            title = await find_weekly_title(
                page
            )

            print("")
            print(
                f"📰 Titel: {title}"
            )

            # -------------------------------------------------
            # GESAMTEN TEXT
            # -------------------------------------------------

            body_text = clean_text(
                await page.locator(
                    "body"
                ).inner_text()
            )

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

            print(
                f"📅 Erkannter Zeitraum: "
                f"{period or 'NICHT GEFUNDEN'}"
            )

            # -------------------------------------------------
            # ABSCHNITTE
            # -------------------------------------------------

            sections = await read_sections(
                page
            )

            print_section(
                "📊 GEFUNDENE WOCHEN-BEREICHE",
                []
            )

            if sections:

                for section in sections:

                    print("")
                    print(
                        f"### {section['heading']}"
                    )

                    for item in section["items"]:
                        print(
                            f"• {item}"
                        )

            else:

                print(
                    "Keine passenden Bereiche "
                    "über Überschriften gefunden."
                )

            # -------------------------------------------------
            # FALLBACK-LISTEN
            # -------------------------------------------------

            lists = await read_lists(
                page
            )

            print_section(
                "📋 GEFUNDENE LISTEN-/TABELLENINHALTE",
                lists[:80]
            )

            # -------------------------------------------------
            # TESTERGEBNIS
            # -------------------------------------------------

            print("")
            print("=" * 60)
            print("🏁 GTABASE-TEST ABGESCHLOSSEN")
            print("=" * 60)

            print("")
            print(
                "Discord wurde NICHT angesprochen."
            )

            print(
                "last_article.txt wurde NICHT verändert."
            )

            print("")
            print(
                "Wenn hier die aktuellen "
                "Wocheninformationen erscheinen,"
            )

            print(
                "können wir daraus den echten "
                "Mittwoch-Bot bauen."
            )

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
            error
        )

        raise


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
