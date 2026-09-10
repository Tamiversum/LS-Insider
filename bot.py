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

GTABASE_URL = "https://www.gtabase.com/gta-online/weekly-update-bonuses-discounts"
ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"

STATE_FILE = "weekly_state.json"
VIENNA = ZoneInfo("Europe/Vienna")

# ------------------------------------------------------------
# TEST-MODUS
# ------------------------------------------------------------
# True  = Mittwoch + Donnerstag werden bei jedem manuellen Lauf
#         gepostet und der normale State wird NICHT verändert.
#
# False = normaler Automatikbetrieb.
TEST_MODE = True

DISCORD_LIMIT = 1900


# ============================================================
# ALLGEMEINE HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)

    return text.strip()


def clean_single_line(text):
    if not text:
        return ""

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

        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def make_signature(*parts):
    raw = "\n".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def limit_text(text, limit=DISCORD_LIMIT):
    if len(text) <= limit:
        return text

    return text[: limit - 3] + "..."


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not message:
        return

    chunks = []

    while len(message) > DISCORD_LIMIT:
        split_at = message.rfind("\n", 0, DISCORD_LIMIT)

        if split_at < 500:
            split_at = DISCORD_LIMIT

        chunks.append(message[:split_at])
        message = message[split_at:].lstrip()

    if message:
        chunks.append(message)

    for chunk in chunks:
        response = requests.post(
            WEBHOOK_URL,
            json={"content": chunk},
            timeout=30,
        )

        response.raise_for_status()

        print("Discord-Nachricht erfolgreich gesendet.")


# ============================================================
# ÜBERSETZUNG
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
    "December": "Dezember",
}


def translate_text(text):
    if not text:
        return ""

    replacements = {
        "GTA$ & RP Bonuses": "GTA$- & RP-Boni",
        "In-Game Discounts": "Rabatte im Spiel",
        "Showrooms & Test Rides": "Showrooms & Testfahrten",
        "Weekly Challenge": "Wöchentliche Herausforderung",
        "This Week's Bonuses & Discounts": "Weitere Boni & Rabatte",
        "Business Battle": "Business Battle",
        "Street Dealers": "Straßenhändler",
        "Bike Service Missions": "Motorrad-Service-Missionen",
        "Clubhouse Contracts": "Clubhaus-Aufträge",
        "Community Series Jobs": "Community-Series-Jobs",
        "Hasta la Vista": "Hasta la Vista",
        "Every Bullet Counts": "Every Bullet Counts",
        "Podium Vehicle": "Podiumsfahrzeug",
        "Prize Ride": "Prize Ride",
        "Luxury Autos": "Luxury Autos",
        "Premium Deluxe Motorsport": "Premium Deluxe Motorsport",
        "Test Ride": "Testfahrt",
        "Premium Test Ride": "Premium-Testfahrt",
        "GTA+ Members": "GTA+-Mitglieder",
        "Members": "Mitglieder",
        "Free": "KOSTENLOS",
    }

    for english, german in replacements.items():
        text = text.replace(english, german)

    for english, german in MONTHS.items():
        text = text.replace(english, german)

    return text


# ============================================================
# ZEITRAUM
# ============================================================

def extract_period(text):
    if not text:
        return None

    patterns = [
        r"from\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\s+to\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
        r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\s*[–-]\s*([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            month1, day1, year1, month2, day2, year2 = match.groups()

            return (
                f"{day1}. {MONTHS.get(month1, month1)} {year1} – "
                f"{day2}. {MONTHS.get(month2, month2)} {year2}"
            )

    # Fallback: einzelne Datumsangaben
    dates = re.findall(
        r"\b([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\b",
        text,
    )

    if len(dates) >= 2:
        month1, day1, year1 = dates[0]
        month2, day2, year2 = dates[1]

        return (
            f"{day1}. {MONTHS.get(month1, month1)} {year1} – "
            f"{day2}. {MONTHS.get(month2, month2)} {year2}"
        )

    return None


def current_week_period():
    now = datetime.now(VIENNA)

    start = now.date()
    end = start

    # GTA-Woche: Donnerstag bis Mittwoch
    days_since_thursday = (start.weekday() - 3) % 7
    start = start.fromordinal(start.toordinal() - days_since_thursday)
    end = start.fromordinal(start.toordinal() + 6)

    return (
        f"{start.day}. {MONTHS.get(start.strftime('%B'), start.strftime('%B'))} "
        f"{start.year} – "
        f"{end.day}. {MONTHS.get(end.strftime('%B'), end.strftime('%B'))} "
        f"{end.year}"
    )


# ============================================================
# HTML / PLAYWRIGHT
# ============================================================

async def fetch_gtabase():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = await browser.new_page(
            viewport={"width": 1440, "height": 2200},
            locale="en-US",
        )

        try:
            await page.goto(
                GTABASE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)

            text = await page.locator("body").inner_text()

            print(f"GTABase geladen: {len(text)} Zeichen")

            return clean_text(text)

        finally:
            await browser.close()


async def fetch_rockstar_news():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = await browser.new_page(
            viewport={"width": 1440, "height": 2200},
            locale="de-DE",
        )

        try:
            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(7000)

            # Cookie-Hinweise möglichst entfernen
            try:
                buttons = page.locator("button")

                for i in range(await buttons.count()):
                    button = buttons.nth(i)

                    try:
                        text = await button.inner_text()

                        if any(
                            word in text.lower()
                            for word in [
                                "accept",
                                "akzeptieren",
                                "alle akzeptieren",
                            ]
                        ):
                            await button.click(timeout=1000)
                            break

                    except Exception:
                        pass

            except Exception:
                pass

            await page.wait_for_timeout(2000)

            articles = await page.locator("article").all()

            results = []

            for article in articles:
                try:
                    article_text = clean_text(await article.inner_text())

                    if "GTA Online" not in article_text:
                        continue

                    link = await article.locator("a").first.get_attribute("href")

                    if link and link.startswith("/"):
                        link = "https://www.rockstargames.com" + link

                    if not link:
                        continue

                    title = ""

                    for selector in ["h1", "h2", "h3", "h4"]:
                        locator = article.locator(selector)

                        if await locator.count():
                            title = clean_single_line(
                                await locator.first.inner_text()
                            )

                            if title:
                                break

                    if not title:
                        lines = article_text.splitlines()

                        for line in lines:
                            line = clean_single_line(line)

                            if (
                                len(line) > 25
                                and "GTA Online" in line
                            ):
                                title = line
                                break

                    if title:
                        results.append(
                            {
                                "title": title,
                                "url": link,
                                "text": article_text,
                            }
                        )

                except Exception:
                    continue

            # Fallback über Links
            if not results:
                links = await page.locator("a").all()

                for link_locator in links:
                    try:
                        href = await link_locator.get_attribute("href")
                        text = clean_single_line(
                            await link_locator.inner_text()
                        )

                        if (
                            href
                            and "newswire" in href.lower()
                            and text
                            and len(text) > 20
                        ):
                            if href.startswith("/"):
                                href = (
                                    "https://www.rockstargames.com"
                                    + href
                                )

                            results.append(
                                {
                                    "title": text,
                                    "url": href,
                                    "text": text,
                                }
                            )

                    except Exception:
                        continue

            unique_results = []
            seen_urls = set()

            for item in results:
                if item["url"] in seen_urls:
                    continue

                seen_urls.add(item["url"])
                unique_results.append(item)

            print(
                f"Rockstar GTA-Online-Artikel gefunden: "
                f"{len(unique_results)}"
            )

            return unique_results

        finally:
            await browser.close()


# ============================================================
# GTABASE SECTIONS
# ============================================================

def split_lines(text):
    lines = []

    for line in text.splitlines():
        line = clean_single_line(line)

        if line:
            lines.append(line)

    return lines


def find_heading(lines, heading):
    heading_lower = heading.lower()

    for index, line in enumerate(lines):
        if line.lower() == heading_lower:
            return index

    return -1


def extract_section(lines, start_heading, end_headings):
    start = find_heading(lines, start_heading)

    if start == -1:
        return []

    end = len(lines)

    for heading in end_headings:
        position = find_heading(lines[start + 1 :], heading)

        if position != -1:
            absolute = start + 1 + position

            if absolute < end:
                end = absolute

    return lines[start + 1 : end]


# ============================================================
# BONI
# ============================================================

def is_multiplier(line):
    return bool(
        re.fullmatch(
            r"\d+(?:\.\d+)?x",
            line.strip(),
            flags=re.IGNORECASE,
        )
    )


def extract_bonus_items(lines):
    section = extract_section(
        lines,
        "GTA$ & RP Bonuses",
        [
            "In-Game Discounts",
            "Showrooms & Test Rides",
            "Weekly Challenge",
        ],
    )

    results = []

    pending = None

    ignore = {
        "gta$ & rp bonuses",
        "gta$ & rp bonuses this week",
        "bonus",
        "bonuses",
    }

    for line in section:
        if line.lower() in ignore:
            continue

        if is_multiplier(line):
            if pending:
                item = f"{pending} – {line}"
                results.append(item)

            continue

        if re.fullmatch(r"\d+", line):
            continue

        if "$" in line and re.search(r"\$\s?[\d,]+", line):
            continue

        pending = line

    return unique_items(results)


# ============================================================
# RABATTE
# ============================================================

def looks_like_price(line):
    return bool(
        re.search(
            r"\$[\d,]+",
            line,
        )
    )


def extract_discount_items(lines):
    section = extract_section(
        lines,
        "In-Game Discounts",
        [
            "Showrooms & Test Rides",
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    results = []

    pending_name = None

    ignored = {
        "in-game discounts",
        "discounts",
        "vehicles",
        "properties",
        "weapons",
        "clothing",
        "other",
    }

    for line in section:
        lower = line.lower()

        if lower in ignored:
            continue

        # Rabatt / kostenlos
        match = re.search(r"-(\d+)%", line)

        if match:
            percentage = int(match.group(1))

            if pending_name:
                results.append(
                    {
                        "name": pending_name,
                        "percentage": percentage,
                    }
                )

            continue

        if re.search(r"\bfree\b", lower) or re.search(
            r"\bkostelos\b",
            lower,
        ):
            if pending_name:
                results.append(
                    {
                        "name": pending_name,
                        "percentage": 100,
                        "free": True,
                    }
                )

            continue

        # Preiszeilen nicht als Namen übernehmen
        if looks_like_price(line):
            continue

        if line.startswith("~~"):
            continue

        # Überschriften und offensichtliche Seitenelemente
        if len(line) < 2:
            continue

        if any(
            bad in lower
            for bad in [
                "remove ads",
                "vip membership",
                "support us",
                "advertisement",
                "cookie",
                "sign in",
                "login",
            ]
        ):
            continue

        pending_name = line

    # Doppelte Einträge entfernen
    cleaned = []
    seen = set()

    for item in results:
        key = (
            item["name"].lower(),
            item["percentage"],
        )

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(item)

    cleaned.sort(
        key=lambda item: (
            -item["percentage"],
            item["name"].lower(),
        )
    )

    return cleaned


# ============================================================
# KOSTENLOSE SACHEN
# ============================================================

def extract_free_items(lines):
    results = []

    # --------------------------------------------------------
    # Gratis-Immobilien / Property
    # --------------------------------------------------------
    discount_section = extract_section(
        lines,
        "In-Game Discounts",
        [
            "Showrooms & Test Rides",
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    pending_name = None

    for line in discount_section:
        lower = line.lower()

        if re.search(r"\bfree\b", lower) or "kostelos" in lower:
            if pending_name:
                results.append(
                    f"{pending_name} – KOSTENLOS"
                )

            continue

        if looks_like_price(line):
            continue

        if line.startswith("~~"):
            continue

        if len(line) > 2:
            if not any(
                bad in lower
                for bad in [
                    "remove ads",
                    "vip membership",
                    "support us",
                    "advertisement",
                ]
            ):
                pending_name = line

    # --------------------------------------------------------
    # Gratis La Coureuse
    # --------------------------------------------------------
    full_text = "\n".join(lines)

    if re.search(
        r"free\s+Penaud\s+La\s+Coureuse",
        full_text,
        flags=re.IGNORECASE,
    ):
        results.append(
            "Penaud La Coureuse – KOSTENLOS "
            "(bei erfüllter Voraussetzung)"
        )

    # --------------------------------------------------------
    # Gratis Kleidung aus Business Battles
    # --------------------------------------------------------
    clothing_items = [
        "Six Figure Tee",
        "Ride or Die Tee",
        "Red and White Ammu-Nation Cap",
        "Bourgeoix Tee",
    ]

    for clothing in clothing_items:
        if clothing.lower() in full_text.lower():
            results.append(
                f"{clothing} – kostenlose Belohnung"
            )

    return unique_items(results)


# ============================================================
# FAHRZEUGE
# ============================================================

VEHICLE_NAMES = [
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
    "Zhaba",
    "Neon",
    "GT750",
    "FMJ MK V",
    "HSW Cyclone II",
    "Penaud La Coureuse",
]


def extract_vehicle_items(lines):
    full_text = "\n".join(lines)

    found = []

    for vehicle in VEHICLE_NAMES:
        if vehicle.lower() in full_text.lower():
            found.append(vehicle)

    return unique_items(found)


def extract_new_vehicle_items(lines):
    full_text = "\n".join(lines)

    results = []

    # GTA+ Early Access / neu
    patterns = [
        r"Pegassi Horus",
        r"Penaud La Coureuse",
    ]

    for pattern in patterns:
        if re.search(
            pattern,
            full_text,
            flags=re.IGNORECASE,
        ):
            if pattern == "Penaud La Coureuse":
                # Nur aufnehmen, wenn eine echte neue/reward Meldung
                # vorhanden ist.
                if re.search(
                    r"free\s+Penaud\s+La\s+Coureuse",
                    full_text,
                    flags=re.IGNORECASE,
                ):
                    results.append("Penaud La Coureuse")

            else:
                results.append(pattern)

    return unique_items(results)


def extract_showroom_vehicles(lines):
    section = extract_section(
        lines,
        "Showrooms & Test Rides",
        [
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    text = "\n".join(section)

    results = []

    for vehicle in VEHICLE_NAMES:
        if vehicle.lower() in text.lower():
            results.append(vehicle)

    return unique_items(results)


# ============================================================
# WEEKLY CHALLENGE
# ============================================================

def extract_weekly_challenge(lines):
    section = extract_section(
        lines,
        "Weekly Challenge",
        [
            "This Week's Bonuses & Discounts",
        ],
    )

    if not section:
        return None

    text = clean_single_line(" ".join(section))

    if not text:
        return None

    # Wer den GTABase-Text enthält, aber die eigentliche Challenge
    # nicht sauber erkennt, bekommt trotzdem einen sinnvollen
    # Fallback.
    reward = ""

    if re.search(
        r"\$1,000,000",
        text,
        flags=re.IGNORECASE,
    ):
        reward = "Extra GTA$1.000.000"

    if "Junk Tracksuit" in text:
        if reward:
            reward += " + Junk Tracksuit"
        else:
            reward = "Junk Tracksuit"

    if reward:
        return (
            "GTA$1.000.000 durch den Verkauf aller Arten von "
            f"Produkten – Belohnung: {reward}"
        )

    return translate_text(text)


# ============================================================
# WEITERE BONI / AKTIONEN
# ============================================================

def extract_additional_weekly_content(lines):
    section = extract_section(
        lines,
        "This Week's Bonuses & Discounts",
        [],
    )

    if not section:
        return []

    text = "\n".join(section)

    results = []

    patterns = [
        (
            r"Kortz Center Heist Primary Targets",
            "Kortz Center Heist: I Hear Voices, I, Fruit, Until Death",
        ),
        (
            r"Salvage Yard Robberies",
            "Salvage Yard Robberies",
        ),
        (
            r"Premium Race",
            "Premium Race: Eight Figure Bonus",
        ),
        (
            r"Time Trial",
            "Time Trial: Sawmill",
        ),
        (
            r"HSW Time Trial",
            "HSW Time Trial: Del Perro Beach → Murietta Heights",
        ),
        (
            r"Tactical SMG",
            "Tactical SMG – 40 % Rabatt",
        ),
        (
            r"Railgun",
            "Railgun – 40 % Rabatt für GTA+",
        ),
    ]

    for pattern, result in patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            results.append(result)

    return unique_items(results)


# ============================================================
# GTA+
# ============================================================

def extract_gta_plus(lines):
    full_text = "\n".join(lines)

    results = []

    if re.search(
        r"Pegassi Horus",
        full_text,
        flags=re.IGNORECASE,
    ) and re.search(
        r"GTA\+",
        full_text,
        flags=re.IGNORECASE,
    ):
        results.append(
            "Pegassi Horus – GTA+ Early Access"
        )

    if re.search(
        r"Bike Service Missions.*6x",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        results.append(
            "Bike Service Missions – 6x GTA$ für GTA+"
        )

    if re.search(
        r"Railgun.*40%",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        results.append(
            "Railgun – 40 % Rabatt für GTA+"
        )

    return unique_items(results)


# ============================================================
# MITTWOCH – WOCHENZEITUNG
# ============================================================

def build_wednesday_message(
    period,
    bonuses,
    free_items,
    new_vehicles,
    showroom_vehicles,
    discounts,
    challenge,
    additional_content,
    gta_plus,
):
    message = []

    message.append("🗞️ **LS-INSIDER**")
    message.append("")
    message.append("# **DIESE WOCHE IN LOS SANTOS**")
    message.append("")
    message.append(f"📅 **{period}**")
    message.append("")
    message.append("━━━━━━━━━━━━━━━━━━━━")
    message.append("")

    # --------------------------------------------------------
    # WOCHENMELDUNG
    # --------------------------------------------------------
    message.append("📰 **DIE WOCHENMELDUNG**")
    message.append(
        "GTA Online – aktuelle Eventwoche, Boni, "
        "Belohnungen und Rabatte"
    )
    message.append("")

    # --------------------------------------------------------
    # BONI
    # --------------------------------------------------------
    if bonuses:
        message.append("💰 **BONI & AKTIONEN**")

        for bonus in bonuses:
            message.append(f"• {translate_text(bonus)}")

        message.append("")

    # --------------------------------------------------------
    # KOSTENLOSE SACHEN
    # --------------------------------------------------------
    if free_items:
        message.append("🎁 **KOSTENLOSE SACHEN**")

        for item in free_items:
            message.append(f"• {translate_text(item)}")

        message.append("")

    # --------------------------------------------------------
    # FAHRZEUGE
    # --------------------------------------------------------
    if new_vehicles:
        message.append("🚗 **NEU AUF DEN STRASSEN**")

        for vehicle in new_vehicles:
            message.append(f"• {vehicle}")

        message.append("")

    # --------------------------------------------------------
    # SHOWROOM / ROTATION
    # --------------------------------------------------------
    if showroom_vehicles:
        message.append("🚗 **FAHRZEUGE DIESER WOCHE**")

        for vehicle in showroom_vehicles:
            message.append(f"• {vehicle}")

        message.append("")

    # --------------------------------------------------------
    # RABATTE
    # --------------------------------------------------------
    if discounts:
        message.append("🏷️ **RABATTE**")

        grouped = {}

        for item in discounts:
            percentage = item["percentage"]

            grouped.setdefault(
                percentage,
                [],
            ).append(item)

        for percentage in sorted(
            grouped.keys(),
            reverse=True,
        ):
            if percentage == 100:
                message.append("• **KOSTENLOS**")
            else:
                message.append(
                    f"• **{percentage} % RABATT**"
                )

            for item in grouped[percentage]:
                message.append(
                    f"  └ {translate_text(item['name'])}"
                )

        message.append("")

    # --------------------------------------------------------
    # WEEKLY CHALLENGE
    # --------------------------------------------------------
    if challenge:
        message.append("🏆 **WÖCHENTLICHE HERAUSFORDERUNG**")
        message.append(f"• {translate_text(challenge)}")
        message.append("")

    # --------------------------------------------------------
    # WEITERE AKTIVITÄTEN
    # --------------------------------------------------------
    if additional_content:
        message.append("🎯 **WEITERE AKTIONEN**")

        for item in additional_content:
            message.append(f"• {translate_text(item)}")

        message.append("")

    # --------------------------------------------------------
    # GTA+
    # --------------------------------------------------------
    if gta_plus:
        message.append("⭐ **GTA+**")

        for item in gta_plus:
            message.append(f"• {translate_text(item)}")

        message.append("")

    # --------------------------------------------------------
    # AKTUELLE WOCHE
    # --------------------------------------------------------
    message.append("📅 **AKTUELLE WOCHE**")
    message.append(f"• {period}")
    message.append("")

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------
    message.append("━━━━━━━━━━━━━━━━━━━━")
    message.append("")
    message.append("🔗 **VOLLSTÄNDIGER ARTIKEL**")
    message.append(GTABASE_URL)
    message.append("")
    message.append(
        "🗞️ *Dein LS-Insider – immer bestens informiert.*"
    )

    return "\n".join(message)


# ============================================================
# MITTWOCH AUSFÜHREN
# ============================================================

async def run_wednesday():
    print("=" * 60)
    print("LS-INSIDER MITTWOCH – WOCHENZEITUNG")
    print("=" * 60)

    text = await fetch_gtabase()

    if not text:
        raise RuntimeError(
            "GTABase konnte nicht ausgelesen werden."
        )

    lines = split_lines(text)

    print(f"Zeilen erkannt: {len(lines)}")

    # --------------------------------------------------------
    # Zeitraum
    # --------------------------------------------------------
    period = extract_period(text)

    if not period:
        period = current_week_period()

    # --------------------------------------------------------
    # Inhalte
    # --------------------------------------------------------
    bonuses = extract_bonus_items(lines)
    free_items = extract_free_items(lines)
    new_vehicles = extract_new_vehicle_items(lines)
    showroom_vehicles = extract_showroom_vehicles(lines)
    discounts = extract_discount_items(lines)
    challenge = extract_weekly_challenge(lines)
    additional_content = extract_additional_weekly_content(lines)
    gta_plus = extract_gta_plus(lines)

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------
    print()
    print("ERKANNTE BONI:")
    for item in bonuses:
        print(" -", item)

    print()
    print("ERKANNTE KOSTENLOSE SACHEN:")
    for item in free_items:
        print(" -", item)

    print()
    print("ERKANNTE NEUE FAHRZEUGE:")
    for item in new_vehicles:
        print(" -", item)

    print()
    print("ERKANNTE FAHRZEUGE DIESER WOCHE:")
    for item in showroom_vehicles:
        print(" -", item)

    print()
    print("ERKANNTE RABATTE:")
    for item in discounts:
        print(
            f" - {item['percentage']} % | "
            f"{item['name']}"
        )

    print()
    print("WEEKLY CHALLENGE:")
    print(" -", challenge)

    print()
    print("WEITERE AKTIONEN:")
    for item in additional_content:
        print(" -", item)

    print()
    print("GTA+:")
    for item in gta_plus:
        print(" -", item)

    # --------------------------------------------------------
    # Nachricht erzeugen
    # --------------------------------------------------------
    message = build_wednesday_message(
        period=period,
        bonuses=bonuses,
        free_items=free_items,
        new_vehicles=new_vehicles,
        showroom_vehicles=showroom_vehicles,
        discounts=discounts,
        challenge=challenge,
        additional_content=additional_content,
        gta_plus=gta_plus,
    )

    print()
    print("=" * 60)
    print("MITTWOCH-NACHRICHT")
    print("=" * 60)
    print(message)
    print("=" * 60)

    # --------------------------------------------------------
    # Signatur
    # --------------------------------------------------------
    signature = make_signature(
        period,
        bonuses,
        free_items,
        new_vehicles,
        showroom_vehicles,
        discounts,
        challenge,
        additional_content,
        gta_plus,
    )

    # --------------------------------------------------------
    # TEST-MODUS
    # --------------------------------------------------------
    if TEST_MODE:
        print()
        print(
            "TEST-MODUS aktiv – Mittwoch wird gesendet."
        )

        send_discord(message)

        print(
            "weekly_state.json wird im Test-Modus "
            "NICHT verändert."
        )

        return

    # --------------------------------------------------------
    # NORMALER MODUS – DOPPELTE POSTS VERHINDERN
    # --------------------------------------------------------
    state = load_state()

    if state.get("wednesday_signature") == signature:
        print(
            "Mittwoch wurde für diese Woche bereits "
            "gepostet. Kein doppelter Post."
        )
        return

    send_discord(message)

    state["wednesday_signature"] = signature
    state["wednesday_period"] = period
    state["wednesday_posted_at"] = (
        datetime.now(VIENNA).isoformat()
    )

    save_state(state)

    print("Mittwoch-State gespeichert.")


# ============================================================
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

def article_fingerprint(article):
    return make_signature(
        article.get("title", ""),
        article.get("url", ""),
    )


def build_thursday_message(article):
    title = clean_single_line(
        article.get("title", "")
    )

    url = article.get("url", "")

    text = article.get("text", "")

    lower = text.lower()

    categories = []

    if any(
        word in lower
        for word in [
            "new",
            "arrives",
            "available",
            "early access",
        ]
    ):
        categories.append(
            "🆕 **Neu**"
        )

    if any(
        word in lower
        for word in [
            "change",
            "changed",
            "updated",
            "update",
        ]
    ):
        categories.append(
            "⚠️ **Änderung**"
        )

    if any(
        word in lower
        for word in [
            "bonus",
            "2x",
            "3x",
            "4x",
            "5x",
        ]
    ):
        categories.append(
            "💰 **neuer Bonus**"
        )

    if any(
        word in lower
        for word in [
            "vehicle",
            "car",
            "motorcycle",
            "pegassi",
            "bravado",
            "benefactor",
        ]
    ):
        categories.append(
            "🚗 **neue Fahrzeugmeldung**"
        )

    if any(
        word in lower
        for word in [
            "reward",
            "free",
            "clothing",
            "prize",
        ]
    ):
        categories.append(
            "🎁 **neue Belohnung**"
        )

    categories = unique_items(categories)

    message = []

    message.append("🕵️ **LS-INSIDER – GEHEIMBERICHT**")
    message.append("")
    message.append(
        "🤫 **Psst … eine neue Meldung ist eingetroffen.**"
    )
    message.append("")
    message.append(
        "Unser Informant hat Neuigkeiten aus Los Santos "
        "durchgegeben:"
    )
    message.append("")

    if title:
        message.append(f"**{title}**")
        message.append("")

    for category in categories:
        message.append(category)

    if categories:
        message.append("")

    message.append(
        "🕵️ **INFORMANTENBERICHT**"
    )
    message.append(
        "Die aktuelle Rockstar-Meldung wurde überprüft "
        "und ist für den LS-Insider eingetroffen."
    )
    message.append("")

    message.append(
        "💬 *„Mehr darf ich dazu im Moment nicht sagen … "
        "aber behaltet Los Santos im Auge.“*"
    )
    message.append("")

    message.append(
        "🔗 **ORIGINALMELDUNG ÖFFNEN**"
    )
    message.append(url)

    return "\n".join(message)


async def run_thursday():
    print("=" * 60)
    print("LS-INSIDER DONNERSTAG – GEHEIMBERICHT")
    print("=" * 60)

    articles = await fetch_rockstar_news()

    if not articles:
        print(
            "Keine passenden Rockstar-GTA-Online-Artikel gefunden."
        )
        return

    latest = articles[0]

    print(
        "Neuester GTA-Online-Artikel:",
        latest.get("title"),
    )

    signature = article_fingerprint(latest)

    message = build_thursday_message(latest)

    print()
    print(message)
    print()

    # --------------------------------------------------------
    # TEST-MODUS
    # --------------------------------------------------------
    if TEST_MODE:
        print(
            "TEST-MODUS aktiv – Donnerstag wird gesendet."
        )

        send_discord(message)

        print(
            "weekly_state.json wird im Test-Modus "
            "NICHT verändert."
        )

        return

    # --------------------------------------------------------
    # NORMALER MODUS
    # --------------------------------------------------------
    state = load_state()

    if state.get("thursday_signature") == signature:
        print(
            "Keine neue Rockstar-Meldung. "
            "Kein Donnerstag-Post."
        )
        return

    send_discord(message)

    state["thursday_signature"] = signature
    state["thursday_title"] = latest.get("title", "")
    state["thursday_url"] = latest.get("url", "")
    state["thursday_posted_at"] = (
        datetime.now(VIENNA).isoformat()
    )

    save_state(state)

    print("Donnerstag-State gespeichert.")


# ============================================================
# TEST
# ============================================================

async def run_test():
    print()
    print("=" * 70)
    print("LS-INSIDER TESTLAUF")
    print("=" * 70)
    print(
        "TEST_MODE=True: Es wird gepostet, "
        "aber der State bleibt unverändert."
    )
    print("=" * 70)
    print()

    await run_wednesday()

    print()
    print("=" * 70)
    print()

    await run_thursday()


# ============================================================
# HAUPTPROGRAMM
# ============================================================

async def main():
    if TEST_MODE:
        await run_test()
    else:
        now = datetime.now(VIENNA)

        # Mittwoch = 2
        if now.weekday() == 2:
            await run_wednesday()

        # Donnerstag = 3
        elif now.weekday() == 3:
            await run_thursday()

        else:
            print(
                "Heute ist kein regulärer LS-Insider-Tag."
            )


if __name__ == "__main__":
    asyncio.run(main())
