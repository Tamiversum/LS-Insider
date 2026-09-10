import asyncio
import hashlib
import html
import json
import os
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

IGTA_NEWS_URL = "https://www.igrandtheftauto.com/gtaonline/news"
ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"
STATE_FILE = "weekly_state.json"
VIENNA = ZoneInfo("Europe/Vienna")

# Fuer den ersten Test True lassen. Danach LS_INSIDER_TEST_MODE=false setzen.
TEST_MODE = os.getenv("LS_INSIDER_TEST_MODE", "true").lower() == "true"

DISCORD_LIMIT = 1900
REQUEST_TIMEOUT = 45
BROWSER_WAIT_MS = 4000


# ============================================================
# GRUNDLAGEN
# ============================================================

def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_line(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^[•·▪◦➜→]+\s*", "", value)
    value = re.sub(r"^[|]+\s*", "", value)
    return value.strip()


def unique_items(items):
    result = []
    seen = set()
    for item in items:
        item = clean_line(str(item))
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def make_signature(*parts: str) -> str:
    raw = "||".join(clean_text(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_state() -> dict:
    if TEST_MODE:
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    if TEST_MODE:
        return
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def split_for_discord(text: str, limit: int = DISCORD_LIMIT):
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0

    for line in text.splitlines():
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def send_discord(text: str) -> None:
    chunks = split_for_discord(text)
    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            WEBHOOK_URL,
            json={
                "content": chunk,
                "allowed_mentions": {"parse": []},
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        print(f"Discord: Nachricht {index}/{len(chunks)} gesendet ({len(chunk)} Zeichen)")


# ============================================================
# WOCHENZEITRAUM
# ============================================================

def target_thursday_date(today: date | None = None) -> date:
    """Liefert den fuer den jeweiligen Wochentag passenden Donnerstag.

    Mittwoch/Donnerstag -> bevorstehender bzw. aktueller Donnerstag.
    Freitag/Samstag/Sonntag -> letzter Donnerstag.
    Montag/Dienstag -> naechster Donnerstag.
    Das macht manuelle Tests nach Mitternacht ebenfalls korrekt.
    """
    today = today or datetime.now(VIENNA).date()
    weekday = today.weekday()
    if weekday <= 2:
        return today + timedelta(days=(3 - weekday))
    if weekday == 3:
        return today
    return today - timedelta(days=(weekday - 3))


def current_week_dates(today: date | None = None):
    start = target_thursday_date(today)
    return start, start + timedelta(days=6)


def format_period(start: date, end: date) -> str:
    return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


# ============================================================
# GENERISCHER HTML-PARSER
# ============================================================

class ArticleParser(HTMLParser):
    TRACKED_TAGS = {"h1", "h2", "h3", "h4", "p", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.TRACKED_TAGS:
            self._stack.append([tag, []])

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                tracked_tag, parts = self._stack.pop(index)
                text = clean_line(" ".join(parts))
                if text:
                    self.entries.append({"tag": tracked_tag, "text": text})
                break

    def handle_data(self, data):
        data = clean_line(data)
        if not data:
            return
        for item in self._stack:
            item[1].append(data)

    def close(self):
        while self._stack:
            tag, parts = self._stack.pop()
            text = clean_line(" ".join(parts))
            if text:
                self.entries.append({"tag": tag, "text": text})
        super().close()


def parse_html_entries(page_html: str):
    parser = ArticleParser()
    parser.feed(page_html or "")
    parser.close()
    return parser.entries


def normalize_heading(value: str) -> str:
    value = clean_line(value).casefold().replace("’", "'")
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9äöüß' +]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def visible_lines(text: str):
    return unique_items((clean_line(line) for line in (text or "").splitlines()))


# ============================================================
# iGRANDTHEFTAUTO – ERKENNUNG
# ============================================================

IGTA_MAIN_SECTIONS = {
    "bonuses and rewards": "Bonuses and Rewards",
    "bonuses": "Bonuses and Rewards",
    "free penaud la coureuse and hsw upgrade": "Free Penaud La Coureuse and HSW Upgrade",
    "free penaud la coureuse": "Free Penaud La Coureuse and HSW Upgrade",
    "discounts": "Discounts",
    "discounts and sales": "Discounts",
    "vehicles": "Vehicles",
    "vehicle": "Vehicles",
    "vehicles and more": "Vehicles",
    "challenges": "Challenges",
    "challenge": "Challenges",
}

IGTA_SUBSECTIONS = {
    "luxury autos showroom": "Luxury Autos",
    "luxury autos": "Luxury Autos",
    "premium deluxe motorsport showroom": "Premium Deluxe",
    "premium deluxe motorsport": "Premium Deluxe",
    "premium deluxe": "Premium Deluxe",
    "hao's premium test ride": "Hao's Premium Test Ride",
    "hao's special works": "Hao's Premium Test Ride",
    "ls car meet test rides": "LS Car Meet Test Rides",
    "ls car meet": "LS Car Meet Test Rides",
    "the diamond casino and resort lucky wheel": "Lucky Wheel",
    "lucky wheel": "Lucky Wheel",
    "weekly challenge": "Weekly Challenge",
    "ls car meet prize ride": "LS Car Meet Prize Ride",
    "premium race": "Premium Race",
    "hsw time trial": "HSW Time Trial",
    "time trial": "Time Trial",
}


def looks_like_igta_weekly_title(text: str) -> bool:
    return bool(re.search(r"this week in gta online\s*:", text or "", re.I))


def detect_igta_main_section(text: str):
    norm = normalize_heading(text.rstrip(":"))
    if norm in IGTA_MAIN_SECTIONS:
        return IGTA_MAIN_SECTIONS[norm]
    return None


def detect_igta_subsection(text: str):
    norm = normalize_heading(text.rstrip(":"))
    for key, label in IGTA_SUBSECTIONS.items():
        normalized_key = normalize_heading(key)
        if norm == normalized_key:
            return label
        # iGTA kann zusaetzliche Hinweise an die Unterueberschrift haengen,
        # z. B. "Premium Deluxe Motorsport Showroom - all 30% off".
        if norm.startswith(normalized_key + " "):
            return label
    return None


def entries_from_visible_igta_text(text: str):
    entries = []
    for line in visible_lines(text):
        tag = "p"
        if looks_like_igta_weekly_title(line):
            tag = "h1"
        elif detect_igta_main_section(line):
            tag = "h2"
        elif detect_igta_subsection(line):
            tag = "h3"
        entries.append({"tag": tag, "text": line})
    return entries


def extract_igta_title(entries):
    for entry in entries:
        text = clean_line(entry["text"])
        if looks_like_igta_weekly_title(text):
            return text
    for entry in entries:
        if entry["tag"] == "h1" and entry["text"]:
            return clean_line(entry["text"])
    return ""


def extract_igta_intro(entries):
    title = extract_igta_title(entries)
    found_title = False
    for entry in entries:
        text = clean_line(entry["text"])
        if text == title:
            found_title = True
            continue
        if found_title and entry["tag"] == "p" and len(text) >= 50:
            if not detect_igta_main_section(text):
                return text
    return ""


def entries_to_igta_sections(entries):
    """Baut Hauptbereiche auf und behält alle Unterüberschriften darin."""
    sections = {}
    current = None
    article_title = extract_igta_title(entries)

    for entry in entries:
        text = clean_line(entry.get("text", ""))
        tag = entry.get("tag", "p")
        if not text or text == article_title:
            continue

        detected = detect_igta_main_section(text)
        if detected:
            current = detected
            sections.setdefault(current, [])
            continue

        if current:
            sections[current].append({"tag": tag, "text": text})

    return sections


def get_igta_section(sections, *names):
    wanted = {normalize_heading(name) for name in names}
    for key, values in sections.items():
        key_norm = normalize_heading(key)
        if key_norm in wanted:
            return values
    return []


def section_texts(entries, tags=("p", "li")):
    return [clean_line(entry["text"]) for entry in entries if entry["tag"] in tags]


# ============================================================
# iGTA – EXTRAKTION
# ============================================================

def detect_event_name(title: str, intro: str) -> str:
    combined = f"{title} {intro}".casefold()
    known = [
        ("business rivalries", "Business Rivalries"),
        ("brand wars", "Brand Wars"),
        ("summer heist", "Summer Heist"),
        ("money fronts", "Money Fronts"),
        ("motor madness", "Motor Madness"),
    ]
    for needle, name in known:
        if needle in combined:
            return name
    return "GTA Online Eventwoche"


def german_event_headline(event_name: str) -> str:
    mapping = {
        "Business Rivalries": "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE",
    }
    return mapping.get(event_name, event_name.upper())


def build_german_intro() -> str:
    return (
        "Die Geschäfte in Los Santos laufen auf Hochtouren. Diese Woche warten wieder "
        "erhöhte Auszahlungen, Rabatte und besondere Angebote auf euch."
    )


def translate_bonus_line(line: str) -> str:
    result = clean_line(line)
    replacements = [
        (r"2x gta\$\s+and\s+rp", "2X GTA$ und RP"),
        (r"3x gta\$\s+and\s+rp", "3X GTA$ und RP"),
        (r"2x gta\$", "2X GTA$"),
        (r"3x gta\$", "3X GTA$"),
        (r"double rewards", "doppelte Belohnungen"),
        (r"triple rewards", "dreifache Belohnungen"),
        (r"biker clubhouse contracts", "Biker-Clubhaus-Verträge"),
        (r"mc work", "MC-Arbeiten"),
        (r"mc challenges", "MC-Herausforderungen"),
        (r"bike service missions", "Motorrad-Service-Missionen"),
        (r"street dealer sales", "Verkäufe bei Straßenhändlern"),
        (r"community mission series", "Community-Missionsserie"),
        (r"featured series", "Featured Series"),
        (r"business battles", "Business Battles"),
        (r"\(6x for gta\+ members\)", "(6X für GTA+ Mitglieder)"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)
    result = re.sub(r"\s+on\s+", " für ", result, flags=re.I)
    result = re.sub(r"\s+and\s+", " und ", result, flags=re.I)
    return re.sub(r"\s+", " ", result).strip()


def extract_bonuses(sections):
    entries = get_igta_section(sections, "Bonuses and Rewards", "Bonuses")
    result = []
    for line in section_texts(entries):
        low = line.casefold()
        if "clothing rewards" in low or "guaranteed clothing rewards" in low:
            continue
        if re.search(r"\b[23]x\b", low) or "double" in low or "triple" in low:
            result.append(translate_bonus_line(line))
    return unique_items(result)


def extract_gifts(sections):
    result = []

    free_entries = get_igta_section(
        sections,
        "Free Penaud La Coureuse and HSW Upgrade",
        "Free Penaud La Coureuse",
    )
    free_blob = " ".join(section_texts(free_entries)).casefold()

    has_free_car = "penaud la coureuse" in free_blob
    has_weekly_condition = "complete at least one weekly challenge" in free_blob
    has_hsw = "hsw upgrade" in free_blob or "hsw" in free_blob

    if has_free_car:
        if has_weekly_condition and has_hsw:
            result.append(
                "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September."
            )
        elif has_weekly_condition:
            result.append(
                "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen."
            )
        else:
            result.append("Penaud La Coureuse kostenlos.")

    bonuses = get_igta_section(sections, "Bonuses and Rewards", "Bonuses")
    bonus_lines = section_texts(bonuses)
    for index, line in enumerate(bonus_lines):
        low = line.casefold()
        if "clothing rewards" in low or ("business battle" in low and "clothing" in low):
            clothing = []
            for following in bonus_lines[index + 1:index + 4]:
                if any(x in following.casefold() for x in ("tee", "cap", "shirt")):
                    clothing.append(following.strip(" ."))
            if clothing:
                result.append(
                    "Kostenlose Kleidung durch Business Battles: "
                    + ", ".join(unique_items(clothing))
                    + "."
                )
            else:
                result.append("Kostenlose Kleidung durch Business Battles.")
            break

    return unique_items(result)


def display_vehicle_name(text: str) -> str:
    text = clean_line(text)
    return re.sub(r"\s*\([^)]{2,}\)", "", text).strip()


def extract_vehicles(sections):
    entries = get_igta_section(sections, "Vehicles", "Vehicle", "Vehicles and More")
    groups = []
    current_label = None

    for entry in entries:
        text = clean_line(entry["text"])
        if not text:
            continue

        subgroup = detect_igta_subsection(text)
        if subgroup:
            current_label = subgroup
            continue

        if entry["tag"] not in {"p", "li"} or not current_label:
            continue

        # Fahrzeugzeilen enthalten auf iGTA normalerweise die Klasse in Klammern.
        if re.search(r"\([^)]{2,}\)", text):
            groups.append((current_label, display_vehicle_name(text)))
        elif current_label == "Lucky Wheel" and len(text) < 100:
            groups.append((current_label, display_vehicle_name(text)))

    result = []
    seen_groups = set()
    grouped = {}
    for label, vehicle in groups:
        key = (label.casefold(), vehicle.casefold())
        if key in seen_groups:
            continue
        seen_groups.add(key)
        grouped.setdefault(label, []).append(vehicle)

    for label, items in grouped.items():
        result.append(f"{label}: {', '.join(unique_items(items))}")

    return result


def translate_challenge_line(line: str) -> str:
    result = clean_line(line)
    replacements = [
        (r"earn gta\$1,000,000", "Verdient GTA$1.000.000"),
        (r"selling all kinds of product", "durch den Verkauf aller Arten von Waren"),
        (r"to bank an extra gta\$1,000,000", "und erhaltet zusätzlich GTA$1.000.000"),
        (r"and the junk tracksuit", "sowie den Junk-Trainingsanzug"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)
    return re.sub(r"\s+", " ", result).strip()


def extract_weekly_challenge(sections):
    entries = get_igta_section(sections, "Challenges", "Challenge")
    current = False

    for entry in entries:
        text = clean_line(entry["text"])
        subgroup = detect_igta_subsection(text)
        if subgroup:
            current = subgroup == "Weekly Challenge"
            continue
        if current and entry["tag"] in {"p", "li"}:
            low = text.casefold()
            if any(x in low for x in ("gta$1,000,000", "weekly challenge", "junk tracksuit")):
                return translate_challenge_line(text)

    for text in section_texts(entries):
        low = text.casefold()
        if "gta$1,000,000" in low and "selling" in low:
            return translate_challenge_line(text)

    return ""


def extract_discounts(sections):
    entries = get_igta_section(sections, "Discounts", "Discounts and Sales", "Discounts & Sales")
    free_items = []
    by_percent = {}

    for line in section_texts(entries):
        match = re.search(r"(\d{1,3})%\s*(?:off|discount)", line, re.I)
        if match:
            percent = int(match.group(1))
            item = re.sub(r"\s*[-—–:]?\s*\d{1,3}%\s*(?:off|discount).*?$", "", line, flags=re.I).strip()
            if item:
                by_percent.setdefault(percent, []).append(display_vehicle_name(item))
            continue

        if re.search(r"\bfree\b", line, re.I):
            item = re.sub(r"\s*[-—–:]?\s*free.*?$", "", line, flags=re.I).strip()
            if item:
                free_items.append(item)

    result = []
    if free_items:
        result.append("KOSTENLOS: " + ", ".join(unique_items(free_items)))

    for percent in sorted(by_percent, reverse=True):
        items = unique_items(by_percent[percent])
        result.append(f"{percent}% Rabatt: " + ", ".join(items))

    return result


# ============================================================
# MITTWOCH – WOCHENZEITUNG
# ============================================================

def make_wednesday_post(article_html: str, source_url: str, visible_text: str = ""):
    html_entries = parse_html_entries(article_html)
    visible_entries = entries_from_visible_igta_text(visible_text)

    # Sichtbarer Text ist fuer iGTA gleichberechtigt und wird bevorzugt,
    # wenn er eine erkennbare Artikelstruktur enthaelt.
    html_sections = entries_to_igta_sections(html_entries)
    visible_sections = entries_to_igta_sections(visible_entries)

    if len(visible_sections) >= 3:
        sections = visible_sections
        source_entries = visible_entries
    else:
        sections = html_sections
        source_entries = html_entries
        for key, values in visible_sections.items():
            if not sections.get(key) and values:
                sections[key] = values

    title = extract_igta_title(source_entries) or extract_igta_title(html_entries)
    intro = extract_igta_intro(source_entries) or extract_igta_intro(html_entries)

    if not looks_like_igta_weekly_title(title):
        raise RuntimeError("iGrandTheftAuto-Artikel konnte nicht eindeutig erkannt werden.")

    start, end = current_week_dates()
    title_start, title_end = extract_igta_period(title)
    if title_start:
        start, end = title_start, title_end

    expected_start, expected_end = current_week_dates()
    if start != expected_start:
        start, end = expected_start, expected_end

    event_name = detect_event_name(title, intro)
    headline = german_event_headline(event_name)

    bonuses = extract_bonuses(sections)
    vehicles = extract_vehicles(sections)
    discounts = extract_discounts(sections)
    gifts = extract_gifts(sections)
    challenge = extract_weekly_challenge(sections)

    found = {
        "Boni": bonuses,
        "Fahrzeuge": vehicles,
        "Rabatte": discounts,
        "Geschenke": gifts,
        "Challenge": challenge,
    }
    populated = sum(bool(value) for value in found.values())

    print(
        "Parser: "
        + ", ".join(
            f"{key}={'OK' if bool(value) else 'FEHLT'}" for key, value in found.items()
        )
    )

    # Sicherheit: niemals einen leeren Wochenpost senden.
    if populated < 3:
        raise RuntimeError(
            f"iGTA-Parser hat nur {populated}/5 Wochenbereiche erkannt. "
            "Es wird bewusst nichts an Discord gesendet."
        )

    lines = [
        "🗞️ **LS-INSIDER**",
        "",
        f"**{headline}**",
        f"📅 **{format_period(start, end)}**",
        build_german_intro(),
        "",
    ]

    if bonuses:
        lines += ["💰 **BONI**"]
        lines += [f"• {item}" for item in bonuses]
        lines.append("")

    if vehicles:
        lines += ["🚗 **FAHRZEUGE**"]
        lines += [f"• {item}" for item in vehicles]
        lines.append("")

    if discounts:
        lines += ["🏷️ **SONDERANGEBOTE**"]
        lines += [f"• {item}" for item in discounts]
        lines.append("")

    if gifts:
        lines += ["🎁 **GESCHENKE**"]
        lines += [f"• {item}" for item in gifts]
        lines.append("")

    if challenge:
        lines += ["🏆 **WOCHENHERAUSFORDERUNG**", f"• {challenge}", ""]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**",
        f"<{source_url}>",
    ]

    post = "\n".join(lines)
    if len(post) > DISCORD_LIMIT:
        raise RuntimeError(
            f"Mittwochsbeitrag ist mit {len(post)} Zeichen zu lang. "
            "Es wird bewusst nichts an Discord gesendet."
        )

    return post, {
        "title": title,
        "period": format_period(start, end),
        "bonuses": bonuses,
        "vehicles": vehicles,
        "discounts": discounts,
        "gifts": gifts,
        "challenge": challenge,
    }


def extract_igta_period(title: str):
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        title or "",
        re.I,
    )
    if not match:
        return None, None

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    month_name, day, year = match.groups()
    start = date(int(year), months[month_name.casefold()], int(day))
    return start, start + timedelta(days=6)


# ============================================================
# BROWSER / iGTA
# ============================================================

async def build_browser_context(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        locale="en-US",
        timezone_id="Europe/Vienna",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 1000},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


async def get_article_visible_text(page, hints=()):
    selectors = ("article", "main")
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count():
                text = await locator.inner_text(timeout=10000)
                if text and (not hints or any(h.casefold() in text.casefold() for h in hints)):
                    return text
        except Exception:
            pass

    try:
        return await page.locator("body").inner_text(timeout=10000)
    except Exception:
        return ""


async def fetch_page(context, url: str, wait_ms: int = BROWSER_WAIT_MS, hints=()):
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(wait_ms)
        html_content = await page.content()
        visible_text = await get_article_visible_text(page, hints=hints)
        status = response.status if response else "?"
        print(
            f"Browser: {url} -> HTTP {status}, "
            f"HTML={len(html_content)}, Sichttext={len(visible_text)}"
        )
        return html_content, visible_text
    finally:
        await page.close()


async def fetch_igrandtheftauto_article():
    start, _ = current_week_dates()
    month = start.strftime("%B").lower()
    expected_url = (
        "https://www.igrandtheftauto.com/gtaonline/news/"
        f"this-week-in-gta-online-{month}-{start.day}-{start.year}"
    )

    print("\niGrandTheftAuto")
    print(f"Erwarteter Artikel: {expected_url}")

    async with async_playwright() as playwright:
        browser, context = await build_browser_context(playwright)
        try:
            html_content, visible_text = await fetch_page(
                context,
                expected_url,
                hints=("This Week in GTA Online", "Bonuses and Rewards"),
            )

            title = extract_igta_title(parse_html_entries(html_content))
            if not looks_like_igta_weekly_title(title):
                title = extract_igta_title(entries_from_visible_igta_text(visible_text))

            if looks_like_igta_weekly_title(title):
                print(f"Aktueller Artikel gefunden: {title}")
                return html_content, expected_url, visible_text

            # Fallback: Newsseite durchsuchen.
            print("Direkter Abruf nicht eindeutig. Suche auf der iGTA-Newsseite ...")
            _, listing_text = await fetch_page(
                context,
                IGTA_NEWS_URL,
                hints=("This Week in GTA Online",),
            )
            listing_lines = visible_lines(listing_text)
            marker = f"this-week-in-gta-online-{month}-{start.day}-{start.year}"
            listing_blob = " ".join(listing_lines)
            if marker in listing_blob.casefold():
                candidate_url = expected_url
                html_content, visible_text = await fetch_page(
                    context,
                    candidate_url,
                    hints=("This Week in GTA Online", "Bonuses and Rewards"),
                )
                return html_content, candidate_url, visible_text

            raise RuntimeError("Kein aktueller iGrandTheftAuto-Wochenartikel gefunden.")
        finally:
            await context.close()
            await browser.close()


# ============================================================
# ROCKSTAR – ARTIKELPARSER
# ============================================================

ROCKSTAR_BAD_SECTION_WORDS = {
    "recommended",
    "sign in",
    "privacy",
    "terms",
    "cookie",
    "follow us",
    "rockstar games",
}


def extract_rockstar_title_from_entries(entries, fallback=""):
    for entry in entries:
        if entry["tag"] == "h1" and entry["text"]:
            text = clean_line(entry["text"])
            if text.casefold() not in ROCKSTAR_BAD_SECTION_WORDS:
                return text
    for entry in entries:
        text = clean_line(entry["text"])
        if "gta online" in text.casefold() and len(text) < 180:
            return text
    return clean_line(fallback)


def extract_rockstar_content_entries(article_html: str, visible_text: str = ""):
    html_entries = parse_html_entries(article_html)
    visible = visible_lines(visible_text)

    # Artikeltext beginnt praktisch immer beim H1. Alles davor wird verworfen.
    if visible:
        title_index = next(
            (i for i, text in enumerate(visible)
             if ("gta online" in text.casefold() or "gta+" in text.casefold()) and len(text) < 220),
            None,
        )
        if title_index is not None:
            selected = visible[title_index:]
            # Navigation/Fußbereich nach langen Linklisten abschneiden.
            selected = selected[:220]
            return [
                {"tag": "h1" if i == 0 else "p", "text": text}
                for i, text in enumerate(selected)
                if clean_line(text)
            ]

    return html_entries


def article_sentences(entries):
    result = []
    for entry in entries:
        if entry["tag"] not in {"p", "li"}:
            continue
        text = clean_line(entry["text"])
        if len(text) < 35:
            continue
        low = text.casefold()
        if any(word in low for word in ROCKSTAR_BAD_SECTION_WORDS):
            continue
        result.append(text)
    return unique_items(result)


def classify_rockstar_article(title: str, entries):
    blob = " ".join([title] + article_sentences(entries)).casefold()
    categories = []

    # Neu: echter Hinweis auf neue Inhalte, nicht jedes Vorkommen von "new".
    if any(x in blob for x in (
        "new ", "brand-new", "newly", "debut", "introducing", "one week of early access",
        "coming to gta online", "revealed",
    )):
        categories.append("🆕 **Neu**")

    if any(x in blob for x in (
        "vehicle", "supercar", "sports car", "pegassi", "horus", "car", "motorcycle",
    )):
        categories.append("🚗 **neue Fahrzeugmeldung**")

    if any(x in blob for x in (
        "gta$", "2x", "3x", "double rewards", "triple rewards", "bonus", "payout",
    )):
        categories.append("💰 **neuer Bonus**")

    if any(x in blob for x in (
        "free", "reward", "rewards", "unlock", "livery", "outfit", "clothing", "tee", "cap",
    )):
        categories.append("🎁 **neue Belohnung**")

    if any(x in blob for x in (
        "updated", "changed", "returning", "removed", "replaced", "now available", "continues",
    )):
        categories.append("⚠️ **Änderung**")

    return categories


def translate_rockstar_sentence(text: str) -> str:
    result = clean_line(text)
    replacements = [
        (r"GTA\+ Members", "GTA+ Mitglieder"),
        (r"GTA\+ member", "GTA+ Mitglied"),
        (r"one week of early access", "eine Woche frühen Zugang"),
        (r"early access", "frühen Zugang"),
        (r"new Pegassi Horus Supercar", "neuen Pegassi Horus"),
        (r"new Pegassi Horus", "neuen Pegassi Horus"),
        (r"available", "verfügbar"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)
    return re.sub(r"\s+", " ", result).strip()


def summarize_rockstar_article(title: str, entries):
    sentences = article_sentences(entries)
    title_low = title.casefold()

    # Konkreter aktueller Horus-Artikel.
    if "pegassi horus" in title_low and "early access" in title_low:
        return [
            "GTA+ Mitglieder erhalten eine Woche frühen Zugang zum neuen Pegassi Horus Supercar.",
            "Der Pegassi Horus ist damit zunächst im Rahmen des GTA+ Early Access verfügbar.",
        ]

    result = []
    for sentence in sentences:
        low = sentence.casefold()
        if any(x in low for x in (
            "gta+", "early access", "new ", "available", "reward", "vehicle", "gta$", "bonus",
        )):
            result.append(translate_rockstar_sentence(sentence))
        if len(result) == 2:
            break

    if len(result) < 2:
        for sentence in sentences:
            if 60 <= len(sentence) <= 320:
                result.append(translate_rockstar_sentence(sentence))
            if len(result) == 2:
                break

    return unique_items(result)[:2]


# ============================================================
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

def make_thursday_post(article: dict, article_html: str, visible_text: str = ""):
    raw_title = clean_line(article.get("title", ""))
    entries = extract_rockstar_content_entries(article_html, visible_text)
    title = extract_rockstar_title_from_entries(entries, raw_title)

    if not title:
        raise RuntimeError("Rockstar-Artikel hat keinen eindeutigen Titel geliefert.")

    categories = classify_rockstar_article(title, entries)
    summary = summarize_rockstar_article(title, entries)

    if not categories:
        categories = ["🆕 **Neu**"]

    lines = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        "",
        "🤫 **Psst … eine neue Meldung ist eingetroffen.**",
        "Unser Informant hat Neuigkeiten aus Los Santos durchgegeben:",
        "",
        f"**{title}**",
        "",
        *categories,
        "",
        "🕵️ **INFORMANTENBERICHT**",
    ]

    if summary:
        lines.extend(f"• {item}" for item in summary)
    else:
        lines.append(
            "Die aktuelle Rockstar-Meldung wurde überprüft und ist für den LS-Insider eingetroffen."
        )

    lines += [
        "",
        "💬 *„Mehr darf ich dazu im Moment nicht sagen … aber behaltet Los Santos im Auge.“*",
        "",
        "🔗 **ORIGINALMELDUNG ÖFFNEN**",
        f"<{article['url']}>",
    ]

    post = "\n".join(lines)
    if len(post) > DISCORD_LIMIT:
        raise RuntimeError("Donnerstagsbeitrag ist zu lang.")

    return post, {
        "title": title,
        "url": article["url"],
        "categories": categories,
        "summary": summary,
    }


async def fetch_rockstar_news():
    async with async_playwright() as playwright:
        browser, context = await build_browser_context(playwright)
        try:
            # Erst die Newsübersicht laden.
            page = await context.new_page()
            try:
                response = await page.goto(ROCKSTAR_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                cards = await page.locator("a[href*='/newswire/article/']").evaluate_all(
                    "els => els.map(e => ({href:e.href, text:(e.innerText||e.textContent||'').trim()}))"
                )
                print(f"Rockstar-Newsseite: HTTP {response.status if response else '?'} | Karten={len(cards)}")
            finally:
                await page.close()

            candidates = []
            seen = set()
            for card in cards:
                url = clean_text(card.get("href", ""))
                text = clean_line(card.get("text", ""))
                if not url or "/newswire/article/" not in url:
                    continue
                if url in seen:
                    continue
                seen.add(url)

                blob = f"{text} {url}".casefold()
                if not any(x in blob for x in ("gta online", "gta+", "grand theft auto")):
                    continue

                title = text.split("\n")[0] if text else ""
                candidates.append({"title": title, "url": url, "text": text})

            # Reihenfolge der Seite beibehalten; maximal die ersten 12 GTA-Kandidaten prüfen.
            for candidate in candidates[:12]:
                article_html, visible_text = await fetch_page(
                    context,
                    candidate["url"],
                    wait_ms=5000,
                    hints=("GTA Online", "GTA+"),
                )
                entries = extract_rockstar_content_entries(article_html, visible_text)
                title = extract_rockstar_title_from_entries(entries, candidate["title"])
                if is_relevant_rockstar_title(title, visible_text):
                    candidate["title"] = title
                    print(f"Rockstar-Artikel gefunden: {title}")
                    return candidate, article_html, visible_text

            raise RuntimeError("Kein relevanter aktueller Rockstar-GTA-Artikel gefunden.")
        finally:
            await context.close()
            await browser.close()


def is_relevant_rockstar_title(title: str, text: str = "") -> bool:
    blob = f"{title} {text}".casefold()
    return any(x in blob for x in ("gta online", "gta+", "grand theft auto online"))


# ============================================================
# AUSFÜHRUNG / DUPLIKATSCHUTZ
# ============================================================

async def run_wednesday():
    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    article_html, source_url, visible_text = await fetch_igrandtheftauto_article()
    post, data = make_wednesday_post(article_html, source_url, visible_text)

    start, end = current_week_dates()
    period_key = format_period(start, end)
    state = load_state()

    if not TEST_MODE and state.get("wednesday_period") == period_key:
        print(f"Mittwoch bereits gepostet: {period_key}")
        return

    print("\n--- MITTWOCH POST ---")
    print(post)
    print(f"Zeichen: {len(post)}")
    send_discord(post)

    if not TEST_MODE:
        state["wednesday_period"] = period_key
        state["wednesday_signature"] = make_signature("wednesday", period_key)
        save_state(state)


async def run_thursday():
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article, article_html, visible_text = await fetch_rockstar_news()
    post, data = make_thursday_post(article, article_html, visible_text)

    article_url = data["url"]
    state = load_state()

    if not TEST_MODE and state.get("thursday_url") == article_url:
        print(f"Donnerstag bereits gepostet: {article_url}")
        return

    print("\n--- DONNERSTAG POST ---")
    print(post)
    print(f"Zeichen: {len(post)}")
    send_discord(post)

    if not TEST_MODE:
        state["thursday_url"] = article_url
        state["thursday_signature"] = make_signature("thursday", article_url)
        save_state(state)


async def main():
    now = datetime.now(VIENNA)
    print("========================================")
    print("LS-INSIDER")
    print(f"Wiener Zeit: {now:%d.%m.%Y %H:%M:%S}")
    print(f"TEST_MODE={TEST_MODE}")
    print("State wird NICHT verändert." if TEST_MODE else "State wird gespeichert.")
    print("========================================")

    weekday = now.weekday()
    workflow_event = os.getenv("GITHUB_EVENT_NAME", "")

    if workflow_event == "workflow_dispatch" and TEST_MODE:
        await run_wednesday()
        await run_thursday()
    elif weekday == 2:
        await run_wednesday()
    elif weekday == 3:
        await run_thursday()
    else:
        print("Heute ist kein LS-Insider-Veröffentlichungstag.")


if __name__ == "__main__":
    asyncio.run(main())
