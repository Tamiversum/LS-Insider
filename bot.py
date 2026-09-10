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

# Beim ersten erfolgreichen Test auf True lassen. Danach auf False stellen.
TEST_MODE = os.getenv("LS_INSIDER_TEST_MODE", "true").lower() == "true"

DISCORD_LIMIT = 1900
REQUEST_TIMEOUT = 45


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_line(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^[•·▪◦➜→]+\s*", "", value)
    value = re.sub(r"^\s*[|•·]+\s*", "", value)
    return value.strip()


def unique_items(items):
    seen = set()
    result = []
    for item in items:
        item = clean_line(item)
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
    for chunk in split_for_discord(text):
        response = requests.post(
            WEBHOOK_URL,
            json={
                "content": chunk,
                "allowed_mentions": {"parse": []},
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()


def target_thursday_date(today: date | None = None) -> date:
    today = today or datetime.now(VIENNA).date()
    days_until_thursday = (3 - today.weekday()) % 7
    return today + timedelta(days=days_until_thursday)


def current_week_dates(today: date | None = None):
    start = target_thursday_date(today)
    return start, start + timedelta(days=6)


def format_period(start: date, end: date) -> str:
    return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


class ArticleParser(HTMLParser):
    """Robuster DOM-naher Parser für Überschriften, Absätze und Listen."""
    TRACKED_TAGS = {"h1", "h2", "h3", "h4", "p", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self._tracked = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.TRACKED_TAGS:
            self._tracked.append([tag, []])

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self._tracked) - 1, -1, -1):
            if self._tracked[i][0] == tag:
                tracked_tag, parts = self._tracked.pop(i)
                text = clean_line(" ".join(parts))
                if text:
                    self.entries.append({"tag": tracked_tag, "text": text})
                break

    def handle_data(self, data):
        text = clean_line(data)
        if not text:
            return
        for item in self._tracked:
            item[1].append(text)

    def close(self):
        while self._tracked:
            tag, parts = self._tracked.pop()
            text = clean_line(" ".join(parts))
            if text:
                self.entries.append({"tag": tag, "text": text})
        super().close()


def parse_html_entries(page_html: str):
    parser = ArticleParser()
    parser.feed(page_html)
    parser.close()
    return parser.entries


IGTA_SECTION_NAMES = {
    "bonuses and rewards", "bonuses & rewards", "bonuses",
    "free penaud la coureuse and hsw upgrade", "free penaud la coureuse",
    "discounts", "discounts and sales", "discounts & sales",
    "vehicles", "vehicle", "challenges", "challenge",
}


def entries_to_sections(entries):
    """Erkennt die iGTA-Hauptsektionen robust, unabhängig von h1/h2-Markup."""
    sections = {}
    current = None
    article_title = normalize_heading(extract_article_title(entries)) if entries else ""

    for entry in entries:
        tag = entry.get("tag", "")
        text = clean_line(entry.get("text", ""))
        if not text:
            continue

        normalized = normalize_heading(text.rstrip(":"))
        if normalized == article_title:
            continue

        if normalized in IGTA_SECTION_NAMES:
            current = text.rstrip(":")
            sections.setdefault(current, [])
            continue

        # Manche iGTA-Versionen liefern Hauptüberschriften als h1/h2, aber
        # die tatsächlichen Überschriftentexte sind trotzdem eindeutig.
        # Unbekannte h1/h2 ignorieren wir, damit Navigation nicht als Artikel
        # gewertet wird. h3/h4 bleiben immer im aktuellen Bereich.
        if current is not None:
            sections[current].append({"tag": tag, "text": text})

    return sections

def normalize_heading(value: str) -> str:
    value = clean_line(value).casefold()
    value = value.replace("’", "'")
    value = re.sub(r"[^a-z0-9äöüß'& +]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_section(sections, *names):
    wanted = [normalize_heading(name) for name in names]

    for key, values in sections.items():
        normalized = normalize_heading(key)
        if normalized in wanted:
            return values
        for wanted_name in wanted:
            if normalized.startswith(wanted_name) or wanted_name in normalized:
                return values

    return []


def section_texts(entries, include_headings=False):
    allowed = {"p", "li"}
    if include_headings:
        allowed |= {"h3", "h4"}
    return [clean_line(entry["text"]) for entry in entries if entry["tag"] in allowed]


def looks_like_igta_weekly_title(text: str) -> bool:
    return bool(re.search(r"this week in gta online\s*:", text or "", re.I))


def extract_article_title(entries) -> str:
    preferred = []
    for entry in entries:
        text = clean_line(entry["text"])
        if entry["tag"] == "h1":
            if looks_like_igta_weekly_title(text):
                return text
            preferred.append(text)

    for text in preferred:
        if text and not any(x in text.casefold() for x in ("igrandtheftauto", "gta online news")):
            return text

    for entry in entries:
        if entry["tag"] == "h2" and entry["text"]:
            return entry["text"]
    return ""


def extract_intro(entries) -> str:
    title_index = -1
    title_text = extract_article_title(entries)
    for index, entry in enumerate(entries):
        if clean_line(entry["text"]) == title_text:
            title_index = index
            break

    start = title_index + 1 if title_index >= 0 else 0
    for entry in entries[start:]:
        if entry["tag"] == "p" and len(entry["text"]) > 50:
            return entry["text"]
    return ""


def extract_period_from_title(title: str):
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        title,
        re.I,
    )
    if not match:
        return None, None

    months = {
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
    month_name, day, year = match.groups()
    start = date(int(year), months[month_name.casefold()], int(day))
    return start, start + timedelta(days=6)


def detect_event_name(title: str, intro: str) -> str:
    combined = f"{title} {intro}".casefold()
    known = [
        "business rivalries",
        "san andreas mercenaries",
        "mercenaries",
        "drug wars",
    ]
    for name in known:
        if name in combined:
            return name.title()
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
        (r"2x gta\$\s*(?:and|&|\+)\s*rp", "2X GTA$ und RP"),
        (r"3x gta\$\s*(?:and|&|\+)\s*rp", "3X GTA$ und RP"),
        (r"2x gta\$", "2X GTA$"),
        (r"3x gta\$", "3X GTA$"),
        (r"2x rp", "2X RP"),
        (r"3x rp", "3X RP"),
        (r"double rewards", "doppelte Belohnungen"),
        (r"triple rewards", "dreifache Belohnungen"),
        (r"double gta\$", "2X GTA$"),
        (r"triple gta\$", "3X GTA$"),
        (r"on biker clubhouse contracts", "für Biker-Clubhaus-Verträge"),
        (r"biker clubhouse contracts", "Biker-Clubhaus-Verträge"),
        (r"mc work", "MC-Arbeiten"),
        (r"mc challenges", "MC-Herausforderungen"),
        (r"street dealer sales", "Verkäufe bei Straßenhändlern"),
        (r"bike service missions", "Motorrad-Service-Missionen"),
        (r"community mission series", "Community-Missionsserie"),
        (r"featured series", "Featured Series"),
        (r"business battles", "Business Battles"),
        (r"\(6x for gta\+ members\)", "(6X für GTA+ Mitglieder)"),
    ]

    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)

    result = re.sub(r"\s+", " ", result).strip()
    return result


def extract_bonuses(sections):
    entries = get_section(sections, "Bonuses and Rewards", "Bonuses & Rewards", "Bonuses")
    result = []

    for line in section_texts(entries):
        low = line.casefold()
        if "business battle" in low and ("clothing" in low or "clothes" in low):
            continue
        if re.search(r"\b(?:2|3)x\b", low) or any(
            token in low for token in ("double", "triple", "bonus", "reward")
        ):
            translated = translate_bonus_line(line)
            # Kleine Restbereinigung für natürlichere deutsche Sätze.
            translated = re.sub(r"\band\b", "und", translated, flags=re.I)
            translated = re.sub(r"\bon\b", "für", translated, flags=re.I)
            result.append(translated)

    return unique_items(result)


def extract_gifts(sections):
    result = []

    free_entries = get_section(
        sections,
        "Free Penaud La Coureuse and HSW upgrade",
        "Free Penaud La Coureuse",
        "Penaud La Coureuse",
    )
    free_text = section_texts(free_entries)

    free_blob = " ".join(free_text).casefold()
    for variant in ("penaude la coureuse", "penauda la coureuse", "penaude", "penauda", "pena ud la coureuse"):
        free_blob = free_blob.replace(variant, "penaud la coureuse")

    has_free_car = "penaud la coureuse" in free_blob
    has_weekly_condition = "complete at least one weekly challenge" in free_blob or "at least one weekly challenge" in free_blob
    has_hsw = "hsw" in free_blob

    if has_free_car or has_weekly_condition:
        if has_hsw:
            result.append(
                "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; inklusive HSW-Upgrade im genannten Einlösezeitraum."
            )
        else:
            result.append(
                "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen."
            )

    bonuses = get_section(sections, "Bonuses and Rewards", "Bonuses & Rewards", "Bonuses")
    bonus_lines = section_texts(bonuses)
    for index, line in enumerate(bonus_lines):
        low = line.casefold()
        if "guaranteed clothing rewards" in low or "business battle" in low and "clothing" in low:
            clothing_parts = []
            for following in bonus_lines[index:index + 4]:
                fl = following.casefold()
                if any(token in fl for token in ("tee", "cap", "shirt")):
                    clothing_parts.append(following)
            if clothing_parts:
                cleaned_clothing = []
                for part in clothing_parts:
                    part = re.sub(r"\s*[,;]?$", "", part)
                    part = re.sub(r"^.*?:\s*", "", part)
                    part = re.sub(r",\s*or\s+", ", ", part, flags=re.I)
                    cleaned_clothing.append(part.rstrip("."))
                result.append(
                    "Kostenlose Kleidung durch Business Battles: " + ", ".join(unique_items(cleaned_clothing)) + "."
                )
            else:
                result.append("Kostenlose Kleidung durch Business Battles.")
            break

    return unique_items(result)


def display_discount_item(item: str) -> str:
    # Fahrzeugklassen wie (Motorcycle) oder (SUV) werden für Discord weggelassen.
    return clean_line(re.sub(r"\s*\([^)]{2,}\)", "", item))


def extract_discounts(sections):
    entries = get_section(sections, "Discounts", "Discounts and Sales", "Discounts & Sales")
    free_items = []
    by_percent = {}

    for line in section_texts(entries):
        line = clean_line(line)
        match = re.search(r"(\d{1,3})%\s*(?:off|discount)", line, re.I)
        if match:
            percent = int(match.group(1))
            item = re.sub(
                r"\s*[-—–:]?\s*\d{1,3}%\s*(?:off|discount).*?$",
                "",
                line,
                flags=re.I,
            ).strip()
            item = re.sub(r"^\s*[-•·]+\s*", "", item)
            if item:
                by_percent.setdefault(percent, []).append(item)
            continue

        if re.search(r"\bfree\b", line, re.I):
            item = re.sub(r"\s*[-—–:]?\s*free.*?$", "", line, flags=re.I).strip()
            if item:
                free_items.append(item)

    result = []
    if free_items:
        result.append("KOSTENLOS: " + ", ".join(unique_items(free_items)))

    for percent in sorted(by_percent.keys(), reverse=True):
        display_items = unique_items(display_discount_item(item) for item in by_percent[percent])
        result.append(f"{percent}% Rabatt: " + ", ".join(display_items))

    return unique_items(result)


def clean_vehicle_name(line: str) -> str:
    line = clean_line(line)
    line = re.sub(r"\s*\[[^\]]*\]", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def display_vehicle_name(line: str) -> str:
    # Fahrzeugklasse in Klammern ist für die Wochenzeitung nicht nötig und
    # spart viel Platz, damit der gesamte Beitrag in eine Discord-Nachricht passt.
    return clean_line(re.sub(r"\s*\([^)]{2,}\)", "", line))


def extract_vehicles(sections):
    entries = get_section(sections, "Vehicles", "Vehicle", "Vehicles and More")
    groups = []
    current_group = None

    category_map = {
        "luxury autos": "Luxury Autos",
        "luxury autos showroom": "Luxury Autos",
        "premium deluxe": "Premium Deluxe",
        "premium deluxe motorsport": "Premium Deluxe",
        "hao's special works": "Hao's Premium Test Ride",
        "hao's premium test ride": "Hao's Premium Test Ride",
        "ls car meet": "LS Car Meet Test Rides",
        "ls car meet test rides": "LS Car Meet Test Rides",
        "the diamond casino and resort lucky wheel": "Lucky Wheel",
        "lucky wheel": "Lucky Wheel",
    }

    def detect_category(text):
        low = normalize_heading(text.rstrip(":"))
        for key, label in category_map.items():
            normalized_key = normalize_heading(key)
            if low == normalized_key or low.startswith(normalized_key) or normalized_key in low:
                return label
        return None

    def switch_group(label):
        nonlocal current_group
        if groups and groups[-1][0] == label:
            current_group = groups[-1]
        else:
            current_group = [label, []]
            groups.append(current_group)

    for entry in entries:
        text = clean_line(entry["text"])
        if not text:
            continue

        if entry["tag"] in {"h3", "h4"}:
            matched = detect_category(text)
            if matched:
                switch_group(matched)
            continue

        # iGTA kann Unterüberschriften auch als fett markierten p-Block
        # mit Doppelpunkt ausliefern (z. B. LS Car Meet Test Rides:).
        if entry["tag"] == "p" and text.endswith(":"):
            matched = detect_category(text)
            if matched:
                switch_group(matched)
                continue

        if entry["tag"] not in {"p", "li"} or not current_group:
            continue

        looks_like_vehicle = bool(re.search(r"\([^)]{2,}\)", text))
        if looks_like_vehicle and len(text) <= 180:
            current_group[1].append(clean_vehicle_name(text))
        elif current_group[0] == "Lucky Wheel" and len(text) <= 100:
            current_group[1].append(clean_vehicle_name(text))

    if not groups:
        fallback = []
        for text in section_texts(entries):
            if re.search(r"\([^)]{2,}\)", text):
                fallback.append(clean_vehicle_name(text))
        if fallback:
            groups = [["Fahrzeuge", fallback]]

    result = []
    for label, items in groups:
        items = unique_items(items)
        if items:
            display_items = unique_items(display_vehicle_name(item) for item in items)
            result.append(f"{label}: {', '.join(display_items)}")

    return unique_items(result)


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
    entries = get_section(sections, "Challenges", "Challenge")
    inside = False

    for entry in entries:
        text = clean_line(entry["text"])
        low = text.casefold()

        if entry["tag"] in {"h3", "h4"}:
            inside = "weekly challenge" in low
            continue

        # Auch eine normale p-Zeile mit Doppelpunkt kann die Unterüberschrift sein.
        if entry["tag"] == "p" and text.rstrip().endswith(":"):
            inside = "weekly challenge" in low
            if inside:
                continue

        if inside and entry["tag"] in {"p", "li"} and text:
            return translate_challenge_line(text)

    # Fallback: direkte Erkennung typischer Formulierungen.
    for text in section_texts(entries, include_headings=True):
        low = text.casefold().replace(" ", "")
        if "weeklychallenge" in low:
            continue
        if "gta$1,000,000" in low or "gta$1000000" in low:
            return translate_challenge_line(text)
        if "bankanextr agta$1,000,000" in low:
            return text
        if "bankanextr a" in low and "gta$1,000,000" in low:
            return text

    return ""


def make_wednesday_post(article_html: str, source_url: str):
    entries = parse_html_entries(article_html)
    sections = entries_to_sections(entries)

    title = extract_article_title(entries)
    intro = extract_intro(entries)
    start, end = extract_period_from_title(title)

    expected_start, expected_end = current_week_dates()
    if not start:
        start, end = expected_start, expected_end
    elif start != expected_start:
        # Der nächste donnerstagsbasierte Veröffentlichungszeitraum hat Vorrang.
        start, end = expected_start, expected_end

    event_name = detect_event_name(title, intro)
    headline = german_event_headline(event_name)

    bonuses = extract_bonuses(sections)
    gifts = extract_gifts(sections)
    discounts = extract_discounts(sections)
    vehicles = extract_vehicles(sections)
    challenge = extract_weekly_challenge(sections)

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
        lines.extend(f"• {item}" for item in bonuses)
        lines.append("")

    if vehicles:
        lines += ["🚗 **FAHRZEUGE**"]
        lines.extend(f"• {item}" for item in vehicles)
        lines.append("")

    if discounts:
        lines += ["🏷️ **SONDERANGEBOTE**"]
        lines.extend(f"• {item}" for item in discounts)
        lines.append("")

    if gifts:
        lines += ["🎁 **GESCHENKE**"]
        lines.extend(f"• {item}" for item in gifts)
        lines.append("")

    if challenge:
        lines += ["🏆 **WOCHENHERAUSFORDERUNG**", f"• {challenge}", ""]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**",
        f"<{source_url}>",
    ]

    post = "\n".join(lines)
    data = {
        "title": title,
        "period": format_period(start, end),
        "bonuses": bonuses,
        "vehicles": vehicles,
        "discounts": discounts,
        "gifts": gifts,
        "challenge": challenge,
    }
    return post, data


async def fetch_page_html(context, url: str, wait_ms: int = 3500) -> str:
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(wait_ms)
        html_content = await page.content()
        status = response.status if response else "?"
        print(f"Playwright: {url} -> HTTP {status}, {len(html_content)} Zeichen")
        return html_content
    finally:
        await page.close()


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
        locale="de-DE",
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


async def fetch_igrandtheftauto_article():
    start, _ = current_week_dates()
    month = start.strftime("%B").lower()
    expected_url = f"https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-{month}-{start.day}-{start.year}"

    print("iGrandTheftAuto:")
    print(f"Versuche aktuellen Wochenartikel:\n{expected_url}")

    async with async_playwright() as playwright:
        browser, context = await build_browser_context(playwright)
        try:
            article_html = await fetch_page_html(context, expected_url)
            entries = parse_html_entries(article_html)
            title = extract_article_title(entries)

            if looks_like_igta_weekly_title(title):
                print(f"Artikel direkt gefunden: {title}")
                return article_html, expected_url

            print("Direkter Artikel nicht eindeutig erkannt.")
            print("Suche aktuellen Wochenartikel über die GTA-Online-Newsseite ...")

            page = await context.new_page()
            try:
                await page.goto(IGTA_NEWS_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4500)
                links = await page.locator("a[href*='/gtaonline/news/']").evaluate_all(
                    "els => els.map(e => ({href: e.href, text: (e.innerText || e.textContent || '').trim()}))"
                )
            finally:
                await page.close()

            candidates = []
            seen = set()
            for item in links:
                href = clean_text(item.get("href", ""))
                text = clean_line(item.get("text", ""))
                href = urljoin(IGTA_NEWS_URL + "/", href)
                if "/gtaonline/news/this-week-in-gta-online-" not in href.casefold():
                    continue
                if href in seen:
                    continue
                seen.add(href)
                candidates.append((href, text))

            expected_marker = f"{month}-{start.day}-{start.year}"
            candidates.sort(
                key=lambda x: (
                    expected_marker not in x[0].casefold(),
                    x[0],
                )
            )

            for href, text in candidates:
                print(f"Kandidat: {href} | {text}")
                candidate_html = await fetch_page_html(context, href)
                candidate_entries = parse_html_entries(candidate_html)
                candidate_title = extract_article_title(candidate_entries)
                if looks_like_igta_weekly_title(candidate_title):
                    print(f"Aktueller Artikel gefunden: {candidate_title}")
                    return candidate_html, href

            raise RuntimeError("Kein aktueller iGrandTheftAuto-Wochenartikel gefunden.")
        finally:
            await context.close()
            await browser.close()


async def fetch_rockstar_news():
    async with async_playwright() as playwright:
        browser, context = await build_browser_context(playwright)
        try:
            page = await context.new_page()
            try:
                await page.goto(ROCKSTAR_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                cards = await page.locator("a[href*='/newswire/article/']").evaluate_all(
                    "els => els.map(e => ({href: e.href, text: (e.innerText || e.textContent || '').trim()}))"
                )
            finally:
                await page.close()

            seen = set()
            candidates = []
            for card in cards:
                url = clean_text(card.get("href", ""))
                text = clean_line(card.get("text", ""))
                if not url or "/newswire/article/" not in url:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                title = text.split("\n")[0] if text else ""
                blob = f"{title} {text}".casefold()
                if "gta" not in blob and "grand theft auto" not in blob:
                    continue
                candidates.append({"title": title, "url": url, "text": text})

            for candidate in candidates:
                if is_relevant_rockstar_title(candidate["title"], candidate["text"]):
                    article_html = await fetch_page_html(context, candidate["url"], wait_ms=5000)
                    return candidate, article_html

            raise RuntimeError("Kein relevanter aktueller Rockstar-GTA-Artikel gefunden.")
        finally:
            await context.close()
            await browser.close()


def is_relevant_rockstar_title(title: str, text: str = "") -> bool:
    blob = f"{title} {text}".casefold()
    return any(token in blob for token in ("gta online", "gta+", "grand theft auto online"))


def extract_rockstar_title(article_html: str, fallback: str) -> str:
    entries = parse_html_entries(article_html)
    for entry in entries:
        if entry["tag"] == "h1" and entry["text"]:
            title = clean_line(entry["text"])
            if title.casefold() not in {"rockstar games", "newswire"}:
                return title
    return clean_line(fallback)


def plain_body_lines(article_html: str):
    entries = parse_html_entries(article_html)
    result = []
    for entry in entries:
        text = clean_line(entry["text"])
        if not text:
            continue
        result.append((entry["tag"], text))
    return result


def classify_rockstar_article(title: str, article_html: str):
    lines = plain_body_lines(article_html)
    body = "\n".join(text for _, text in lines)
    low_body = body.casefold()
    low_title = title.casefold()
    categories = []

    if any(token in low_title or token in low_body for token in (
        "new ", "newly", "early access", "debut", "introducing", "new vehicle"
    )):
        categories.append("🆕 **Neu**")

    if any(token in low_title or token in low_body for token in (
        "vehicle", "supercar", "sports car", "horus", "pegassi"
    )):
        categories.append("🚗 **neue Fahrzeugmeldung**")

    if any(token in low_body for token in (
        "gta$", "2x", "3x", "double", "triple", "bonus", "reward"
    )):
        categories.append("💰 **neuer Bonus**")

    if any(token in low_body for token in (
        "free", "reward", "unlock", "livery", "outfit", "clothing", "tee", "cap"
    )):
        categories.append("🎁 **neue Belohnung**")

    if any(token in low_body for token in (
        "updated", "changed", "returning", "removed", "replaced", "now available"
    )):
        categories.append("⚠️ **Änderung**")

    return unique_items(categories)


def translate_rockstar_text(text: str) -> str:
    text = clean_line(text)
    replacements = [
        (r"GTA\+ Members", "GTA+ Mitglieder"),
        (r"one week of early access", "eine Woche frühen Zugang"),
        (r"early access", "frühen Zugang"),
        (r"new Pegassi Horus Supercar", "neuen Pegassi Horus"),
        (r"new Pegassi Horus", "neuen Pegassi Horus"),
        (r"available", "verfügbar"),
        (r"members", "Mitglieder"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def summarize_rockstar_article(title: str, article_html: str):
    lines = plain_body_lines(article_html)
    body_texts = [text for tag, text in lines if tag in {"p", "li"}]
    low_title = title.casefold()

    # Aktueller Horus-Artikel: bewusst kompakt und klar.
    if "pegassi horus" in low_title and "early access" in low_title:
        return [
            "GTA+ Mitglieder erhalten eine Woche frühen Zugang zum neuen Pegassi Horus Supercar.",
            "Der Pegassi Horus ist damit zunächst im Rahmen des GTA+ Early Access verfügbar.",
        ]

    result = []
    for text in body_texts:
        low = text.casefold()
        if any(token in low for token in (
            "gta+", "early access", "new ", "available", "reward", "vehicle", "gta$"
        )) and len(text) >= 60:
            result.append(translate_rockstar_text(text))
        if len(result) >= 2:
            break

    if not result:
        for text in body_texts:
            if 60 <= len(text) <= 320:
                result.append(translate_rockstar_text(text))
            if len(result) >= 2:
                break

    return unique_items(result)[:2]


def make_thursday_post(article: dict, article_html: str):
    original_title = clean_line(article["title"])
    title = extract_rockstar_title(article_html, original_title)
    url = article["url"]
    categories = classify_rockstar_article(title, article_html)
    summary = summarize_rockstar_article(title, article_html)

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
    ]

    lines.extend(categories)
    lines.append("")

    lines.append("🕵️ **INFORMANTENBERICHT**")
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
        f"<{url}>",
    ]

    return "\n".join(lines), {
        "title": title,
        "url": url,
        "categories": categories,
        "summary": summary,
    }


async def run_wednesday():
    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    article_html, source_url = await fetch_igrandtheftauto_article()
    post, data = make_wednesday_post(article_html, source_url)

    start, end = current_week_dates()
    period_key = format_period(start, end)
    signature = make_signature("wednesday", period_key)
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
        state["wednesday_signature"] = signature
        save_state(state)


async def run_thursday():
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article, article_html = await fetch_rockstar_news()
    post, data = make_thursday_post(article, article_html)

    article_url = data["url"]
    signature = make_signature("thursday", article_url)
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
        state["thursday_signature"] = signature
        save_state(state)


async def main():
    now = datetime.now(VIENNA)
    weekday = now.weekday()

    print("========================================")
    print("LS-INSIDER")
    print(f"Wiener Zeit: {now:%d.%m.%Y %H:%M:%S}")
    print(f"TEST_MODE={TEST_MODE}")
    print("State wird NICHT verändert." if TEST_MODE else "State wird gespeichert.")
    print("========================================")

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
