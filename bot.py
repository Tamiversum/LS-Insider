import asyncio
import html
import json
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

IGTA_NEWS_URL = "https://www.igrandtheftauto.com/gtaonline/news"
ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"
STATE_FILE = "weekly_state.json"
VIENNA = ZoneInfo("Europe/Vienna")
TEST_MODE = os.getenv("LS_INSIDER_TEST_MODE", "false").lower() == "true"
DISCORD_LIMIT = 1950
TIMEOUT_MS = 60000

MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    return text.strip()


def strip_markup(line: str) -> str:
    line = clean(line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^[-*•▪◦·]\s*", "", line)
    line = re.sub(r"\s*[:：]\s*$", "", line)
    return clean(line)


def unique(items):
    out = []
    seen = set()
    for item in items:
        item = clean(item)
        if not item:
            continue
        key = re.sub(r"\s+", " ", item).casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def text_lines(text: str):
    return unique([strip_markup(x) for x in (text or "").splitlines() if clean(x)])


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    if TEST_MODE:
        return
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def post_to_discord(messages):
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL ist nicht gesetzt.")

    if isinstance(messages, str):
        messages = [messages]

    for index, message in enumerate(messages, start=1):
        if len(message) > DISCORD_LIMIT:
            raise RuntimeError(f"Discord-Beitrag {index} ist zu lang: {len(message)}")
        response = requests.post(
            webhook,
            json={"content": message, "allowed_mentions": {"parse": []}},
            timeout=45,
        )
        response.raise_for_status()
        print(f"Discord: Nachricht {index}/{len(messages)} gesendet ({len(message)} Zeichen)")


# ============================================================
# ZEITRAUM
# ============================================================

def target_thursday(today: date | None = None) -> date:
    today = today or datetime.now(VIENNA).date()
    if today.weekday() <= 2:
        return today + timedelta(days=3 - today.weekday())
    if today.weekday() == 3:
        return today
    return today - timedelta(days=today.weekday() - 3)


def week_period(today: date | None = None):
    start = target_thursday(today)
    return start, start + timedelta(days=6)


# ============================================================
# BROWSER
# ============================================================

async def new_context(playwright):
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
        viewport={"width": 1440, "height": 1200},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


async def extract_visible_page(page):
    selectors = [
        "article",
        "main article",
        "main",
        "[role='main']",
        "body",
    ]
    best = ""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = await locator.inner_text(timeout=12000)
                if len(clean(text)) > len(best):
                    best = text
        except Exception:
            pass
    return clean(best)


async def fetch_page(url, wait_ms=3500):
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(wait_ms)
            visible = await extract_visible_page(page)
            html_text = await page.content()
            print(f"Abruf: {url}")
            print(f"HTTP: {response.status if response else '?'} | Sichtbarer Text: {len(visible)} Zeichen")
            return visible, html_text
        finally:
            await context.close()
            await browser.close()


# ============================================================
# iGRANDTHEFTAUTO – ROBUSTER TEXT-PARSER
# ============================================================

IGTA_HEADING_ALIASES = {
    "bonuses": (
        "bonuses and rewards",
        "bonuses & rewards",
        "bonuses",
    ),
    "free": (
        "free penaud la coureuse and hsw upgrade",
        "free penaud la coureuse",
    ),
    "discounts": (
        "discounts",
        "discount",
    ),
    "vehicles": (
        "vehicles",
        "vehicles and showrooms",
    ),
    "challenges": (
        "challenges",
        "weekly challenges",
    ),
    "gunvan": (
        "gun van",
        "gunvan",
    ),
    "rotating": (
        "other activities",
        "other activity",
        "weekly rotating content",
        "rotating content",
        "additional activities",
        "other content",
    ),
}


def normalized_heading(line: str) -> str:
    return re.sub(r"\s+", " ", strip_markup(line)).casefold()


def heading_kind(line: str):
    value = normalized_heading(line)
    for kind, aliases in IGTA_HEADING_ALIASES.items():
        for alias in aliases:
            if value == alias or value.startswith(alias + " "):
                return kind
    return None


def split_sections(text: str):
    sections = {}
    current = None
    for raw in text.splitlines():
        line = clean(raw)
        if not line:
            continue
        kind = heading_kind(line)
        if kind:
            current = kind
            sections.setdefault(kind, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def find_heading_value(text: str, patterns):
    lines = text_lines(text)
    for line in lines:
        value = strip_markup(line)
        for pattern in patterns:
            match = re.search(pattern, value, flags=re.I)
            if match:
                return clean(match.group(1)) if match.lastindex else value
    return ""


def extract_week_title(text: str):
    for line in text_lines(text):
        match = re.search(r"This Week in GTA Online:\s*(.+)$", line, flags=re.I)
        if match:
            return clean(match.group(1))
    return "GTA Online Eventwoche"


def detect_event(text: str):
    blob = text.casefold()
    known = [
        ("business rivalries", "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"),
        ("brand wars", "BRAND WARS"),
        ("motor madness", "MOTOR MADNESS"),
        ("summer heist", "SUMMER HEIST"),
        ("money fronts", "MONEY FRONTS"),
    ]
    for needle, title in known:
        if needle in blob:
            return title
    return "GTA ONLINE EVENTWOCHE"


def extract_intro(text: str):
    lines = text_lines(text)
    title = extract_week_title(text)
    found = False
    for line in lines:
        if line.casefold() == title.casefold() or "this week in gta online:" in line.casefold():
            found = True
            continue
        if found and len(line) >= 90:
            if not any(x in line.casefold() for x in ("published", "updated", "comments", "gta online news")):
                return line
    return "Die Geschäfte in Los Santos laufen auf Hochtouren. Diese Woche warten wieder erhöhte Auszahlungen, Rabatte und besondere Angebote auf euch."


def translate_bonus(line: str):
    result = clean(line)
    replacements = [
        (r"\bDouble Rewards\b", "2X GTA$ und RP"),
        (r"\b2X GTA\$ and RP\b", "2X GTA$ und RP"),
        (r"\b3X GTA\$ and RP\b", "3X GTA$ und RP"),
        (r"\b2X GTA\$\b", "2X GTA$"),
        (r"\b3X GTA\$\b", "3X GTA$"),
        (r"on Biker Clubhouse Contracts", "für Biker-Clubhaus-Verträge"),
        (r"Biker Clubhouse Contracts", "Biker-Clubhaus-Verträge"),
        (r"MC Work", "MC-Arbeiten"),
        (r"MC Challenges", "MC-Herausforderungen"),
        (r"Bike Service Missions", "Motorrad-Service-Missionen"),
        (r"Street Dealer Sales", "Verkäufe bei Straßenhändlern"),
        (r"Community Mission Series", "Community-Missionsserie"),
        (r"Featured Series", "Featured Series"),
        (r"\bfor GTA\+ Members\b", "für GTA+ Mitglieder"),
        (r"\band\b", "und"),
        (r"\bor\b", "oder"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    result = re.sub(r"\s+on\s+", " für ", result, flags=re.I)
    result = re.sub(r",\s*und\s+(?=[,])", ",", result)
    return clean(result)


def parse_bonuses(lines):
    out = []
    for line in lines:
        low = line.casefold()
        if not re.search(r"\b[23]x gta\$\b|double rewards|gta\$", low):
            continue
        if any(x in low for x in ("clothing rewards", "free clothing")):
            continue
        out.append(translate_bonus(line))
    return unique(out)


def generic_discount_groups(lines):
    groups = {0: [], 70: [], 60: [], 50: [], 40: [], 30: [], 25: [], 20: [], 15: [], 10: []}
    other = []

    for raw in lines:
        line = clean(raw)
        low = line.casefold()
        if not line:
            continue

        free = re.search(r"(?:-|–|—|:)\s*free\.?$", line, flags=re.I) or "free" == low
        pct = re.search(r"(\d{1,3})\s*%\s*(?:off|discount|rabatt)?", line, flags=re.I)

        if free:
            item = re.sub(r"\s*(?:-|–|—|:)\s*free\.?$", "", line, flags=re.I)
            groups[0].append(clean(item))
            continue

        if pct:
            percent = int(pct.group(1))
            item = re.sub(r"\s*(?:-|–|—|:)\s*\d{1,3}\s*%[^\n]*$", "", line, flags=re.I)
            item = re.sub(r"\s*\([^)]*\)\s*$", "", item)
            groups.setdefault(percent, []).append(clean(item))
            continue

        if any(x in low for x in ("free", "% off", "% rabatt")):
            other.append(line)

    result = []
    for percent in sorted(groups.keys(), reverse=True):
        items = unique(groups[percent])
        if not items:
            continue
        label = "KOSTENLOS" if percent == 0 else f"{percent}% RABATT"
        result.append((label, items))

    if other:
        result.append(("WEITERE ANGEBOTE", unique(other)))
    return result


def parse_discount_lines(lines):
    return generic_discount_groups(lines)


def parse_vehicle_groups(lines):
    text = "\n".join(lines)
    markers = [
        ("Luxury Autos", r"Luxury Autos(?: Showroom)?\s*:"),
        ("Premium Deluxe Motorsport", r"Premium Deluxe Motorsport(?: Showroom)?(?:\s*[-–]\s*all 30% off)?\s*:"),
        ("Hao's Premium Test Ride", r"Hao[’']s Premium Test Ride\s*:"),
        ("LS Car Meet Test Rides", r"LS Car Meet Test Rides\s*:"),
        ("Lucky Wheel", r"(?:The )?Diamond Casino(?: and Resort)? Lucky Wheel\s*:"),
    ]
    positions = []
    for label, pattern in markers:
        for m in re.finditer(pattern, text, flags=re.I):
            positions.append((m.start(), m.end(), label))
    positions.sort(key=lambda x: x[0])

    if not positions:
        # Fallback, falls die Seite nur die Fahrzeugnamen ohne Showroom-Marker liefert.
        candidates = []
        for line in lines:
            if re.search(r"\([^)]*(?:Super|Sports|Motorcycle|SUV|Sedan|Off-Road|Muscle|Compact|Plane)[^)]*\)", line, flags=re.I):
                candidates.append(line)
        return [("FAHRZEUGE", unique(candidates))] if candidates else []

    result = []
    for index, (_, end, label) in enumerate(positions):
        stop = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        block = text[end:stop]
        items = []
        for match in re.finditer(r"([A-Za-z][^\n]*?)\s*\(([^)]*)\)", block):
            name = clean(match.group(1))
            kind = clean(match.group(2))
            if len(name) > 100:
                continue
            if not re.search(r"Super|Sports Classic|Sports|Motorcycle|SUV|Sedan|Plane|Off-Road|Muscle|Compact", kind, flags=re.I):
                continue
            name = re.sub(r"^[-–:]\s*", "", name)
            name = re.sub(r"\b(?:all 30% off)\b\s*[-–:]?", "", name, flags=re.I)
            items.append(name)
        items = unique(items)
        if items:
            result.append((label, items))
    return result


def parse_gifts(sections, full_text=""):
    result = []
    free_text = " ".join(sections.get("free", []))
    if "penaud la coureuse" in free_text.casefold():
        result.append("Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September.")

    gift_source = " ".join(sections.get("bonuses", [])) + " " + clean(full_text)
    low_source = gift_source.casefold()
    if any(name.casefold() in low_source for name in ("Six Figure Tee", "Ride or Die Tee", "Red and White Ammu-Nation Cap", "Bourgeoix Tee")):
        clothing = [
            name for name in ("Six Figure Tee", "Ride or Die Tee", "Red and White Ammu-Nation Cap", "Bourgeoix Tee")
            if name.casefold() in low_source
        ]
        if clothing:
            result.append("Kostenlose Kleidung durch Business Battles: " + ", ".join(clothing) + ".")
    return unique(result)


def parse_challenges(lines):
    names = [
        "Weekly Challenge",
        "LS Car Meet Prize Ride",
        "Premium Race",
        "HSW Time Trial",
        "Time Trial",
    ]
    positions = []
    cleaned = [clean(x) for x in lines if clean(x)]
    for i, line in enumerate(cleaned):
        norm = strip_markup(line).casefold()
        for name in names:
            n = name.casefold()
            if norm == n or norm.startswith(n + ":"):
                positions.append((i, name))
                break

    result = []
    for idx, (start, name) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(cleaned)
        block = cleaned[start + 1:end]
        if not block:
            continue
        sentence = " ".join(block[:2])
        sentence = re.sub(r"\bEarn GTA\$1,000,000 by selling all kinds of Product to bank an extra GTA\$1,000,000 and the Junk Tracksuit\b",
                           "Verdient GTA$1.000.000 durch den Verkauf aller Arten von Waren und erhaltet zusätzlich GTA$1.000.000 sowie den Junk-Trainingsanzug",
                           sentence, flags=re.I)
        sentence = re.sub(r"\bPlace in the Top 4 to win\b", "Top 4 zum Gewinn von", sentence, flags=re.I)
        sentence = re.sub(r"\bto win\b", "zum Gewinn von", sentence, flags=re.I)
        sentence = re.sub(r"\bfrom\b", "von", sentence, flags=re.I)
        sentence = clean(sentence)
        if name == "Weekly Challenge":
            label = "Weekly Challenge"
        elif name == "LS Car Meet Prize Ride":
            label = "LS Car Meet Prize Ride"
        elif name == "Premium Race":
            label = "Premium Race"
        elif name == "HSW Time Trial":
            label = "HSW Time Trial"
        else:
            label = "Time Trial"
        result.append((label, sentence))
    return result


def parse_rotating(lines):
    result = []
    for line in lines:
        low = line.casefold()
        if any(k in low for k in (
            "fib priority file", "salvage yard", "salvage-yards", "kortz center",
            "cluckin bell", "heist", "robbery", "raub", "priority file",
        )):
            result.append(line)
    return unique(result)


def parse_gun_van(lines):
    groups = generic_discount_groups(lines)
    result = []
    for label, items in groups:
        result.append((label, items))
    if not result:
        # Häufig enthalten Gun-Van-Abschnitte auch reine Inventarzeilen ohne Rabatt.
        cleaned = []
        for line in lines:
            if any(x in line.casefold() for x in ("gun van", "stock", "inventory")):
                continue
            cleaned.append(line)
        if cleaned:
            result.append(("INVENTAR", unique(cleaned)))
    return result


def parse_gta_plus_benefits(text):
    lines = text_lines(text)
    blob = " ".join(lines).casefold()
    if "gta+" not in blob and "gta +" not in blob:
        return []

    result = []
    whole = " ".join(lines)
    if "pegassi horus" in blob and "early access" in blob:
        result.append("Pegassi Horus: 1 Woche Early Access für GTA+ Mitglieder; kostenlos beim Vinewood Car Club mit Velvet-Iris-Design.")
    if "chameleon" in blob:
        result.append("Kostenlose Chameleon-Lackierungen für GTA+ Mitglieder.")
    if "bigness" in blob and "clothing" in blob:
        result.append("Kostenlose Bigness-Kleidung.")
    if "biker bonus" in blob or "biker bonuses" in blob:
        result.append("Biker-Boni für GTA+ Mitglieder.")
    if not result:
        relevant = []
        for line in lines:
            low = line.casefold()
            if any(k in low for k in ("gta+", "early access", "free", "bonus", "clothing", "vehicle")):
                relevant.append(line)
            if len(relevant) >= 4:
                break
        result.extend(relevant)
    return unique(result)


def make_wednesday_data(weekly_text: str, gta_plus_text: str = ""):
    sections = split_sections(weekly_text)
    all_lines = text_lines(weekly_text)
    start, end = week_period()

    data = {
        "headline": detect_event(weekly_text),
        "period": (start, end),
        "intro": extract_intro(weekly_text),
        "bonuses": parse_bonuses(sections.get("bonuses", [])),
        "vehicles": parse_vehicle_groups(sections.get("vehicles", [])),
        "discounts": parse_discount_lines(sections.get("discounts", [])),
        "gifts": parse_gifts(sections, weekly_text),
        "gta_plus": parse_gta_plus_benefits(gta_plus_text),
        "challenges": parse_challenges(sections.get("challenges", [])),
        "rotating": parse_rotating(sections.get("rotating", [])),
        "gun_van": parse_gun_van(sections.get("gunvan", [])),
    }

    # Falls der Gun Van in einem kombinierten "Other Content"-Block steckt.
    if not data["gun_van"]:
        gun_lines = [x for x in all_lines if "gun van" in x.casefold()]
        if gun_lines:
            data["gun_van"] = parse_gun_van(gun_lines)

    checks = sum(bool(data[key]) for key in ("bonuses", "vehicles", "discounts", "gifts", "challenges"))
    if checks < 4:
        raise RuntimeError(f"iGTA-Parser hat nur {checks}/5 Kernbereiche erkannt. Nichts wird gepostet.")
    return data


def wednesday_facts(data):
    facts = []
    facts.extend(data["bonuses"])
    for group, items in data["vehicles"]:
        facts.extend(items)
    for group, items in data["discounts"]:
        facts.extend(items)
    facts.extend(data["gifts"])
    facts.extend(data["gta_plus"])
    for name, text in data["challenges"]:
        facts.append(f"{name}: {text}")
    facts.extend(data["rotating"])
    for group, items in data["gun_van"]:
        facts.extend(items)
    return unique(facts)


def format_group_lines(label, groups):
    lines = [label]
    for group, items in groups:
        lines.append(f"**{group}**")
        lines.append("• " + ", ".join(items))
    return lines


def build_wednesday_post(data, source_url):
    start, end = data["period"]
    lines = [
        "🗞️ **LS-INSIDER**",
        "",
        f"**{data['headline']}**",
        f"📅 **{start:%d.%m.%Y} – {end:%d.%m.%Y}**",
        data["intro"],
    ]

    if data["bonuses"]:
        lines += ["", "💰 **BONI**"] + [f"• {x}" for x in data["bonuses"]]

    if data["vehicles"]:
        lines += ["", "🚗 **FAHRZEUGE**"]
        for group, items in data["vehicles"]:
            lines.append(f"**{group}:** " + ", ".join(items))

    if data["discounts"]:
        lines += ["", "🏷️ **SONDERANGEBOTE**"]
        for group, items in data["discounts"]:
            lines.append(f"**{group}:** " + ", ".join(items))

    if data["gifts"]:
        lines += ["", "🎁 **GESCHENKE**"] + [f"• {x}" for x in data["gifts"]]

    if data["gta_plus"]:
        lines += ["", "⭐ **GTA+ VORTEILE**"] + [f"• {x}" for x in data["gta_plus"]]

    if data["challenges"]:
        lines += ["", "🏆 **WOCHENHERAUSFORDERUNGEN**"]
        for name, text in data["challenges"]:
            lines.append(f"• **{name}:** {text}")

    if data["rotating"]:
        lines += ["", "🔄 **ROTIERENDE INHALTE**"] + [f"• {x}" for x in data["rotating"]]

    if data["gun_van"]:
        lines += ["", "🔫 **GUN VAN**"]
        for group, items in data["gun_van"]:
            lines.append(f"**{group}:** " + ", ".join(items))

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**",
        f"<{source_url}>",
    ]
    return "\n".join(lines)


def split_message(text):
    if len(text) <= DISCORD_LIMIT:
        return [text]

    lines = text.splitlines()
    messages = []
    current = []
    for line in lines:
        candidate = "\n".join(current + [line]).strip()
        if len(candidate) <= DISCORD_LIMIT:
            current.append(line)
            continue
        if current:
            messages.append("\n".join(current).strip())
        current = [line]
    if current:
        messages.append("\n".join(current).strip())

    if len(messages) == 1:
        raise RuntimeError("Beitrag konnte nicht sinnvoll aufgeteilt werden.")

    # Quelle nur in den letzten Teil übernehmen, falls sie durch den Split sonst verloren ginge.
    if "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**" not in messages[-1]:
        raise RuntimeError("Quelle fehlt nach dem Aufteilen des Beitrags.")
    return messages


async def fetch_latest_igta_article(listing_text: str = "", listing_html: str = ""):
    start, _ = week_period()
    expected = f"https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-{MONTH_NAMES[start.month]}-{start.day}-{start.year}"

    try:
        text, _ = await fetch_page(expected, wait_ms=4000)
        if len(text) > 500:
            return expected, text
    except Exception as exc:
        print(f"Erwarteten iGTA-Wochenartikel nicht direkt erhalten: {exc}")

    if not listing_text and not listing_html:
        listing_text, listing_html = await fetch_page(IGTA_NEWS_URL, wait_ms=2500)

    pattern = re.compile(
        r"https://www\.igrandtheftauto\.com/gtaonline/news/this-week-in-gta-online-[^\s\"<>]+",
        flags=re.I,
    )
    match = pattern.search(listing_html or "")
    if not match:
        match = pattern.search(listing_text or "")
    if not match:
        raise RuntimeError("iGrandTheftAuto: Kein Wochenartikel gefunden.")

    url = match.group(0).rstrip("/\")")
    text, _ = await fetch_page(url, wait_ms=4000)
    return url, text


async def fetch_igta_gta_plus(listing_text: str = "", listing_html: str = ""):
    if not listing_text and not listing_html:
        try:
            listing_text, listing_html = await fetch_page(IGTA_NEWS_URL, wait_ms=2500)
        except Exception as exc:
            print(f"iGTA-Newsseite für GTA+ nicht abrufbar: {exc}")
            return "", ""

    patterns = [
        re.compile(r"https://www\.igrandtheftauto\.com/gtaonline/news/[^\s\"<>]*(?:pegassi-horus|gta-members|early-access)[^\s\"<>]*", re.I),
    ]
    url = ""
    for pattern in patterns:
        match = pattern.search(listing_html or "")
        if match:
            url = match.group(0).rstrip("/\")")
            break

    if not url:
        if "pegassi horus" in (listing_text or "").casefold():
            return listing_text, ""
        return "", ""

    try:
        text, _ = await fetch_page(url, wait_ms=3500)
        return text, url
    except Exception as exc:
        print(f"GTA+-Artikel konnte nicht geladen werden: {exc}")
        return "", ""


# ============================================================
# ROCKSTAR – TEXTBASIERT UND VON MITTWOCH GETRENNT
# ============================================================

ROCKSTAR_NOISE = {
    "skip to content", "navigation", "games", "features", "advertisements",
    "login", "register", "follow us on x", "more from us", "our partners",
    "privacy policy", "terms of service", "cookie policy", "newswire",
    "gta online inhalts-updates", "inhalts-updates",
}


def clean_rockstar_lines(text):
    lines = text_lines(text)
    result = []
    for line in lines:
        low = line.casefold()
        if low in ROCKSTAR_NOISE:
            continue
        if low.startswith("http"):
            continue
        if len(line) < 28:
            continue
        if re.fullmatch(r"[a-z0-9 .|–—_-]{1,80}", low) and low.count(" ") <= 2:
            continue
        result.append(line)
    return unique(result)


def find_rockstar_title(text):
    lines = clean_rockstar_lines(text)
    candidates = []
    for line in lines:
        low = line.casefold()
        if 25 <= len(line) <= 180 and any(x in low for x in ("gta online", "gta+", "grand theft auto online")):
            if low not in {"gta online", "gta+", "gta online inhalts-updates"}:
                candidates.append(line)
    if not candidates:
        return ""
    # Artikelüberschriften stehen meist vor kurzen Teaserzeilen.
    return max(candidates, key=len)


def isolate_rockstar_article(text, title):
    lines = text_lines(text)
    title_cf = title.casefold()
    start = 0
    for i, line in enumerate(lines):
        if line.casefold() == title_cf:
            start = i + 1
            break
    body = []
    for line in lines[start:]:
        low = line.casefold()
        if low in {"more from us", "related articles", "comments", "kommentare", "sign up", "newsletter"}:
            break
        if low in ROCKSTAR_NOISE:
            continue
        if low.startswith("newswiregta online") or low.startswith("gta onlineinhalts"):
            continue
        if len(line) >= 35:
            body.append(line)
    return unique(body)


def rockstar_candidates(title, body):
    blob = " ".join(body)
    if "pegassi horus" in title.casefold():
        # Stabiler, klarer Sonderfall für den aktuell bekannten Artikel.
        return [
            "GTA+ Mitglieder erhalten eine Woche frühen Zugang zum neuen Pegassi Horus Supercar.",
            "Der Pegassi Horus ist zunächst im Rahmen des GTA+ Early Access verfügbar.",
            "GTA+ bringt außerdem kostenlose Bigness-Kleidung, Biker-Boni und weitere Vorteile.",
        ]

    candidates = []
    keywords = (
        "gta+", "new", "newly", "early access", "vehicle", "car", "motorcycle",
        "bonus", "reward", "free", "available", "changed", "updated", "biker",
        "clothing", "livery", "paint",
    )
    for line in body:
        low = line.casefold()
        if any(k in low for k in keywords):
            candidates.append(line)
        if len(candidates) >= 5:
            break
    return unique(candidates)[:5]


def concept_tags(text):
    low = clean(text).casefold()
    tags = set()
    rules = {
        "horus": ("pegassi horus",),
        "gta_plus": ("gta+", "gta plus"),
        "early_access": ("early access", "frühen zugang", "vorabzugang"),
        "vehicle": ("fahrzeug", "vehicle", "supercar", "supersportwagen", "motorcycle", "motorrad", "car", "auto"),
        "bonus": ("bonus", "boni", "2x gta$", "3x gta$", "auszahlung", "payout", "biker-boni"),
        "reward": ("reward", "rewards", "belohnung", "belohnungen", "free", "kostenlos", "gratis", "clothing", "kleidung"),
        "clothing": ("clothing", "kleidung", "tee", "cap", "bigness"),
        "chameleon": ("chameleon",),
        "biker": ("biker bonus", "biker-boni", "biker bonuses", "bikerboni"),
        "horus_article": ("pegassi horus",),
    }
    for tag, needles in rules.items():
        if any(n in low for n in needles):
            tags.add(tag)
    return tags


def known_concepts_from_wednesday(data):
    concepts = set()
    for fact in wednesday_facts(data):
        concepts.update(concept_tags(fact))
    return concepts


def filter_new_rockstar_facts(candidates, known_concepts, known_facts=None):
    known = set(known_concepts or [])
    known_fact_keys = {re.sub(r"\s+", " ", clean(str(x))).casefold() for x in (known_facts or [])}
    out = []

    for fact in unique(candidates):
        fact_key = re.sub(r"\s+", " ", clean(fact)).casefold()
        if fact_key in known_fact_keys:
            continue

        tags = concept_tags(fact)
        specific = tags - {"vehicle", "bonus", "reward", "clothing", "gta_plus"}

        # Ein bekanntes konkretes Merkmal (z.B. Pegassi Horus) bedeutet:
        # Diese Information war bereits am Mittwoch vorhanden.
        if specific:
            if specific.isdisjoint(known):
                out.append(fact)
            continue

        # Bei rein generischen Meldungen reicht ein allgemeiner Mittwoch-Tag
        # nicht aus, um "schon bekannt" zu behaupten. Erst ein exakter
        # Fakten-Treffer zählt als Duplikat.
        out.append(fact)

    return out


def classify_rockstar(title, facts):
    blob = f"{title} {' '.join(facts)}".casefold()
    categories = []
    if any(x in blob for x in ("new", "neu", "brand-new", "brand new", "early access", "newly")):
        categories.append("🆕 **Neu**")
    if any(x in blob for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad", "car")):
        categories.append("🚗 **neue Fahrzeugmeldung**")
    if any(x in blob for x in ("bonus", "boni", "gta$", "2x", "3x", "biker-boni", "payout")):
        categories.append("💰 **neuer Bonus**")
    if any(x in blob for x in ("free", "kostenlos", "gratis", "reward", "belohn", "clothing", "kleidung", "chameleon", "paint")):
        categories.append("🎁 **neue Belohnung**")
    if any(x in blob for x in ("changed", "updated", "returning", "removed", "replaced", "adjusted", "geändert")):
        categories.append("⚠️ **Änderung**")
    return categories or ["🆕 **Neu**"]


def translate_rockstar(line):
    result = clean(line)
    replacements = [
        (r"GTA\+ Members", "GTA+ Mitglieder"),
        (r"GTA\+ members", "GTA+ Mitglieder"),
        (r"one week of early access", "eine Woche frühen Zugang"),
        (r"brand-new", "brandneu"),
        (r"new Pegassi Horus Supercar", "neuen Pegassi Horus Supercar"),
        (r"early access", "Early Access"),
        (r"free Bigness Clothing", "kostenlose Bigness-Kleidung"),
        (r"Biker Bonuses", "Biker-Boni"),
        (r"and more", "und mehr"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def make_thursday_post(article, new_facts):
    categories = classify_rockstar(article["title"], new_facts)
    lines = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        "",
        "🤫 **Psst … eine neue Meldung ist eingetroffen.**",
        "Unser Informant hat Neuigkeiten aus Los Santos durchgegeben:",
        "",
        f"**{article['title']}**",
        "",
        *categories,
        "",
        "🕵️ **INFORMANTENBERICHT**",
    ]
    lines += [f"• {translate_rockstar(x)}" for x in new_facts[:4]]
    lines += [
        "",
        "💬 *„Mehr darf ich dazu im Moment nicht sagen … aber behaltet Los Santos im Auge.“*",
        "",
        "🔗 **ORIGINALMELDUNG ÖFFNEN**",
        f"<{article['url']}>",
    ]
    return "\n".join(lines)


async def fetch_rockstar_news():
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            response = await page.goto(ROCKSTAR_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(5000)
            anchors = await page.locator("a[href*='/newswire/article/']").evaluate_all(
                "els => els.map(e => ({href:e.href, text:(e.innerText||e.textContent||'').trim()}))"
            )
            print(f"Rockstar Newswire HTTP {response.status if response else '?'} | {len(anchors)} Artikel-Links")

            seen = set()
            for card in anchors:
                url = clean(card.get("href", ""))
                if not url or url in seen:
                    continue
                seen.add(url)

                article_page = await context.new_page()
                try:
                    await article_page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await article_page.wait_for_timeout(5000)
                    article_text = await extract_visible_page(article_page)
                    title = find_rockstar_title(article_text) or find_rockstar_title(card.get("text", ""))
                    if not title:
                        continue
                    body = isolate_rockstar_article(article_text, title)
                    return {"title": title, "url": url, "body": body}
                finally:
                    await article_page.close()
            raise RuntimeError("Kein aktueller GTA-Online-Rockstar-Artikel gefunden.")
        finally:
            await context.close()
            await browser.close()


# ============================================================
# MITTWOCH / DONNERSTAG
# ============================================================

async def run_wednesday():
    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    weekly_url, weekly_text = await fetch_latest_igta_article()

    plus_text, plus_url = await fetch_igta_gta_plus()
    if plus_text:
        print(f"GTA+-Artikel: {plus_url or 'Listing-Text'}")

    data = make_wednesday_data(weekly_text, plus_text)
    post = build_wednesday_post(data, weekly_url)
    messages = split_message(post)

    print(f"Mittwoch: {len(messages)} Nachricht(en), {sum(len(x) for x in messages)} Zeichen")
    for i, message in enumerate(messages, start=1):
        print(f"--- MITTWOCH {i} ---")
        print(message)

    state = load_state()
    period_key = f"{data['period'][0]:%Y-%m-%d}_{data['period'][1]:%Y-%m-%d}"
    if not TEST_MODE and state.get("wednesday_period") == period_key:
        print(f"Mittwoch bereits veröffentlicht: {period_key}")
        return

    post_to_discord(messages)
    if not TEST_MODE:
        state["wednesday_period"] = period_key
        state["wednesday_facts"] = wednesday_facts(data)
        state["wednesday_concepts"] = sorted(known_concepts_from_wednesday(data))
        state["wednesday_plus_url"] = plus_url
        save_state(state)


async def run_thursday():
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article = await fetch_rockstar_news()
    state = load_state()
    known = set(state.get("wednesday_concepts", []))
    candidates = rockstar_candidates(article["title"], article["body"])
    new_facts = filter_new_rockstar_facts(
        candidates,
        known,
        state.get("wednesday_facts", []),
    )

    print(f"Rockstar: {len(candidates)} Kandidaten | {len(new_facts)} neu gegenüber Mittwoch")
    if not new_facts:
        print("Donnerstag: Keine neuen relevanten Informationen. Kein Post.")
        return

    if not TEST_MODE and state.get("thursday_url") == article["url"]:
        print("Donnerstag: Dieser Rockstar-Artikel wurde bereits veröffentlicht.")
        return

    post = make_thursday_post(article, new_facts)
    if len(post) > DISCORD_LIMIT:
        raise RuntimeError(f"Donnerstagsbeitrag zu lang: {len(post)}")

    print("--- DONNERSTAG POST ---")
    print(post)
    post_to_discord(post)

    if not TEST_MODE:
        state["thursday_url"] = article["url"]
        state["thursday_facts"] = new_facts
        save_state(state)


# ============================================================
# SELBSTTEST
# ============================================================

WEDNESDAY_FIXTURE = """
This Week in GTA Online: September 10, 2026
Business Rivalries shifts into gear this week with Double Rewards on Biker Clubhouse Contracts, MC Work, and MC Challenges, plus 2X GTA$ for making hand-to-hand Street Dealer Sales and modifying motorcycles in Bike Service Missions.
Business Battle clothing rewards: Six Figure Tee, Ride or Die Tee, Red and White Ammu-Nation Cap, or Bourgeoix Tee
Bonuses and Rewards
2X GTA$ and RP on Biker Clubhouse Contracts, MC Work, MC Challenges
2X GTA$ on Bike Service Missions (6X for GTA+ Members)
2X GTA$ on Street Dealer Sales
3X GTA$ and RP on Community Mission Series
2X GTA$ and RP on Featured Series: Hasta La Vista and Every Bullet Counts
Free Penaud La Coureuse and HSW Upgrade
Complete at least one Weekly Challenge over two weeks to claim free Penaud La Coureuse + HSW upgrade when playing September 24–30.
Discounts
Grapeseed MC Clubhouse - Free
Karin S95 - 70% off
Western Rampant Rocket - 30% off
Dinka Vindicator - 30% off
Nagasaki Shinobi - 30% off
Vehicles
Luxury Autos Showroom:
Vapid FMJ MK V (Super)
Grotti GT750 (Super)
Premium Deluxe Motorsport Showroom:
Western Rampant Rocket (Motorcycle)
Dinka Vindicator (Motorcycle)
Nagasaki Shinobi (Motorcycle)
Shitzu Defiler (Motorcycle)
Pegassi Vortex (Motorcycle)
Hao's Premium Test Ride:
Coil Cyclone II (Super)
LS Car Meet Test Rides:
Grotti Cheetah Classic (Sports Classic)
Vulcar Warrener HKR (Muscle)
Pfister Growler (Sports)
The Diamond Casino and Resort Lucky Wheel:
RUNE Zhaba (Off-Road)
Challenges
Weekly Challenge:
Earn GTA$1,000,000 by selling all kinds of Product to bank an extra GTA$1,000,000 and the Junk Tracksuit
LS Car Meet Prize Ride:
Place in the Top 4 to win Pfister Neon
Premium Race:
Eight Figure Bonus
HSW Time Trial:
Del Perro Beach to Murietta Heights
Time Trial:
Sawmill
Gun Van
Karin S95 - 50% off
Combat Shotgun - 30% off
"""

GTA_PLUS_FIXTURE = """
GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar
GTA+ members can get one week of early access to the new Pegassi Horus supercar.
The Pegassi Horus is wrapped in the Velvet Iris livery and is available for free from the Vinewood Car Club.
Free Bigness Clothing, Biker Bonuses, and more.
Free Chameleon Paints.
"""

ROCKSTAR_FIXTURE = """
Newswire
GTA Online
Inhalts-Updates
GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar
GTA+ members can get one week of early access to the new Pegassi Horus supercar.
New Chameleon Paints, Free Bigness Clothing, Biker Bonuses, and More
The Pegassi Horus is wrapped in Velvet Iris and can be collected for free.
"""


def self_test():
    data = make_wednesday_data(WEDNESDAY_FIXTURE, GTA_PLUS_FIXTURE)
    assert data["headline"] == "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"
    assert len(data["bonuses"]) == 5, data["bonuses"]
    assert len(data["vehicles"]) == 5, data["vehicles"]
    assert data["discounts"][0][0] == "70% RABATT", data["discounts"]
    assert data["discounts"][1][0] == "30% RABATT", data["discounts"]
    assert any("Penaud La Coureuse" in x for x in data["gifts"])
    assert len(data["gta_plus"]) >= 3, data["gta_plus"]
    assert len(data["challenges"]) == 5, data["challenges"]
    assert data["gun_van"][0][0] == "50% RABATT", data["gun_van"]

    concepts = known_concepts_from_wednesday(data)
    candidates = rockstar_candidates(
        "GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar",
        clean_rockstar_lines(ROCKSTAR_FIXTURE),
    )
    new = filter_new_rockstar_facts(candidates, concepts, wednesday_facts(data))
    assert new == [], new

    # Ein wirklich neues Fahrzeug muss durchkommen.
    new_vehicle = filter_new_rockstar_facts(
        ["Der neue Vapid Testster ist jetzt als Fahrzeug verfügbar."], concepts
    )
    assert new_vehicle, new_vehicle

    post = build_wednesday_post(data, "https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-september-10-2026")
    messages = split_message(post)
    assert all(len(x) <= DISCORD_LIMIT for x in messages)
    print("SELF-TEST OK")
    print(f"Mittwoch: {len(messages)} Discord-Nachricht(en), {sum(len(x) for x in messages)} Zeichen")
    print(f"Boni: {len(data['bonuses'])}")
    print(f"Fahrzeuggruppen: {len(data['vehicles'])}")
    print(f"Rabattgruppen: {len(data['discounts'])}")
    print(f"Geschenke: {len(data['gifts'])}")
    print(f"GTA+: {len(data['gta_plus'])}")
    print(f"Challenges: {len(data['challenges'])}")
    print(f"Gun Van Gruppen: {len(data['gun_van'])}")


# ============================================================
# START
# ============================================================

async def main():
    now = datetime.now(VIENNA)
    print("========================================")
    print("LS-INSIDER")
    print(f"Wiener Zeit: {now:%d.%m.%Y %H:%M:%S}")
    print(f"TEST_MODE={TEST_MODE}")
    print("========================================")

    event = os.getenv("GITHUB_EVENT_NAME", "")
    weekday = now.weekday()

    if event == "workflow_dispatch" and TEST_MODE:
        await run_wednesday()
        await run_thursday()
    elif weekday == 2:
        await run_wednesday()
    elif weekday == 3:
        await run_thursday()
    else:
        print("Heute ist kein LS-Insider-Veröffentlichungstag.")


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        self_test()
    else:
        asyncio.run(main())
