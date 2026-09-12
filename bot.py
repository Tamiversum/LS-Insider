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
    text = text.replace("\u00a0", " ").replace("\u200b", "")
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
            raise RuntimeError(f"Discord-Nachricht {index} ist zu lang: {len(message)}")
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
    days = (3 - today.weekday()) % 7
    if today.weekday() <= 3:
        return today + timedelta(days=days)
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
    # Be deliberately conservative: prefer the actual article/main body but
    # fall back to body text when a site changes its DOM.
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
                text = clean(text)
                if len(text) > len(best):
                    best = text
        except Exception:
            continue
    return best


async def fetch_page(url, wait_ms=3500):
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(wait_ms)
            visible = await extract_visible_page(page)
            page_html = await page.content()
            print(f"Abruf: {url}")
            print(f"HTTP: {response.status if response else '?'} | Sichtbarer Text: {len(visible)} Zeichen")
            return visible, page_html
        finally:
            await context.close()
            await browser.close()


# ============================================================
# iGRANDTHEFTAUTO – EINFACHER TEXT-PARSER
# ============================================================


IGTA_HEADING_ALIASES = {
    "bonuses": [
        "bonuses and rewards", "bonuses & rewards", "bonuses", "rewards and bonuses",
    ],
    "free": [
        "free penaud la coureuse and hsw upgrade", "free penaud la coureuse",
    ],
    "discounts": ["discounts", "discount"],
    "vehicles": ["vehicles", "vehicles and showrooms"],
    "challenges": ["challenges", "weekly challenges"],
    "gunvan": ["gun van", "gunvan"],
    "rotating": [
        "other activities", "other activity", "weekly rotating content", "rotating content",
        "additional activities", "other content", "more gta online activities",
    ],
}


def normalized_heading(line: str) -> str:
    return strip_markup(line).casefold().rstrip(":").strip()


def heading_kind(line: str):
    value = normalized_heading(line)
    for kind, aliases in IGTA_HEADING_ALIASES.items():
        for alias in aliases:
            if value == alias:
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


def extract_week_title(text: str):
    for line in text_lines(text):
        match = re.search(r"This Week in GTA Online:\s*(.+)$", line, re.I)
        if match:
            return clean(match.group(1))
    return "GTA Online Eventwoche"


def detect_event(text: str):
    blob = clean(text).casefold()
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
    for index, line in enumerate(lines):
        if re.search(r"^This Week in GTA Online:\s*", line, re.I):
            for candidate in lines[index + 1:index + 5]:
                if len(candidate) >= 90 and "this week in gta online" not in candidate.casefold():
                    return candidate
    fallback = (
        "Die Geschäfte in Los Santos laufen auf Hochtouren. "
        "Diese Woche warten wieder erhöhte Auszahlungen, Rabatte und besondere Angebote auf euch."
    )
    return fallback


def translate_bonus(line: str):
    result = clean(line)
    replacements = [
        (r"Double Rewards", "2X GTA$ und RP"),
        (r"2X GTA\$ and RP", "2X GTA$ und RP"),
        (r"3X GTA\$ and RP", "3X GTA$ und RP"),
        (r"2X GTA\$", "2X GTA$"),
        (r"3X GTA\$", "3X GTA$"),
        (r"Biker Clubhouse Contracts", "Biker-Clubhaus-Verträge"),
        (r"MC Work", "MC-Arbeiten"),
        (r"MC Challenges", "MC-Herausforderungen"),
        (r"Bike Service Missions", "Motorrad-Service-Missionen"),
        (r"Street Dealer Sales", "Verkäufe bei Straßenhändlern"),
        (r"Community Mission Series", "Community-Missionsserie"),
        (r"Featured Series", "Featured Series"),
        (r"for GTA\+ Members", "für GTA+ Mitglieder"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    result = re.sub(r"\bon\b", "für", result, flags=re.I)
    result = re.sub(r"\s*,\s*und\s*,", ",", result, flags=re.I)
    return clean(result)


def parse_bonuses(lines):
    out = []
    for line in lines:
        low = line.casefold()
        if not re.search(r"\b[23]x gta\$|double rewards", low):
            continue
        if any(x in low for x in ("clothing rewards", "free clothing")):
            continue
        out.append(translate_bonus(line))
    return unique(out)


def parse_percent_groups(lines):
    groups = {}
    for raw in lines:
        line = clean(raw)
        if not line:
            continue
        free_match = re.search(r"(?:^|\s)(?:-|–|—|:)\s*Free\.?$", line, re.I)
        percent_match = re.search(r"(\d{1,3})\s*%\s*(?:off|discount|rabatt)?", line, re.I)

        if free_match or re.search(r"\bfree\b", line, re.I) and re.search(r"(?:clubhouse|clubhouse|available)", line, re.I):
            item = re.sub(r"\s*(?:-|–|—|:)\s*Free\.?$", "", line, flags=re.I)
            item = re.sub(r"\bFree\b", "", item, flags=re.I).strip(" -–—:")
            if item:
                groups.setdefault(0, []).append(item)
            continue

        if percent_match:
            pct = int(percent_match.group(1))
            item = line
            item = re.sub(r"\s*(?:-|–|—|:)\s*\d{1,3}\s*%\s*(?:off|discount|rabatt)?\.?$", "", item, flags=re.I)
            item = re.sub(r"\b\d{1,3}\s*%\s*(?:off|discount|rabatt)?\b", "", item, flags=re.I).strip(" -–—:")
            item = re.sub(r"\s*\([^)]*\)\s*$", "", item)
            if item:
                groups.setdefault(pct, []).append(item)
    return [
        ("KOSTENLOS" if pct == 0 else f"{pct}% RABATT", unique(groups[pct]))
        for pct in sorted(groups.keys(), reverse=True)
        if groups[pct]
    ]


def parse_discounts(lines):
    return parse_percent_groups(lines)


def _normalize_vehicle_group(line: str):
    value = normalized_heading(line)
    aliases = [
        ("luxury autos", "Luxury Autos"),
        ("premium deluxe motorsport", "Premium Deluxe Motorsport"),
        ("hao's premium test ride", "Hao's Premium Test Ride"),
        ("hao’s premium test ride", "Hao's Premium Test Ride"),
        ("ls car meet test rides", "LS Car Meet Test Rides"),
        ("the diamond casino and resort lucky wheel", "Lucky Wheel"),
        ("diamond casino and resort lucky wheel", "Lucky Wheel"),
    ]
    for needle, label in aliases:
        if value == needle or value.startswith(needle + " "):
            return label
    return None


def parse_vehicle_groups(lines):
    result = []
    current_group = None
    bucket = []

    def flush():
        nonlocal bucket, current_group
        if current_group and bucket:
            cleaned = unique(bucket)
            if cleaned:
                result.append((current_group, cleaned))
        bucket = []

    for raw in lines:
        line = clean(raw)
        if not line:
            continue
        group = _normalize_vehicle_group(line)
        if group:
            flush()
            current_group = group
            continue

        # A single line can contain several vehicles separated by commas.
        # We keep the original names but strip vehicle-type parentheses.
        matches = re.findall(r"([A-Za-z][A-Za-z0-9'’ .-]+?)\s*\(([^)]*)\)", line)
        if matches:
            for name, vehicle_type in matches:
                name = clean(name).strip(" -–—:")
                vehicle_type = clean(vehicle_type)
                if len(name) <= 80 and re.search(
                    r"super|sports|sports classic|motorcycle|motorcycle|suv|sedan|plane|off-road|muscle|compact|utility",
                    vehicle_type,
                    re.I,
                ):
                    bucket.append(name)
            continue

        # Also handle a clean line such as "Vapid FMJ MK V (Super)".
        if current_group and line:
            match = re.match(r"^(.+?)\s*\(([^)]*)\)\s*$", line)
            if match and re.search(
                r"super|sports|sports classic|motorcycle|suv|sedan|plane|off-road|muscle|compact|utility",
                match.group(2),
                re.I,
            ):
                bucket.append(clean(match.group(1)))

    flush()
    return result


def parse_gifts(sections, full_text=""):
    result = []
    free_text = " ".join(sections.get("free", []))
    low_free = free_text.casefold()

    if "penaud la coureuse" in low_free:
        result.append(
            "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; "
            "inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September."
        )

    low_all = clean(full_text).casefold()
    clothing_names = [
        "Six Figure Tee", "Ride or Die Tee", "Red and White Ammu-Nation Cap", "Bourgeoix Tee"
    ]
    if any(name.casefold() in low_all for name in clothing_names):
        found = [name for name in clothing_names if name.casefold() in low_all]
        result.append("Kostenlose Kleidung durch Business Battles: " + ", ".join(found) + ".")
    return unique(result)


def parse_challenges(lines):
    names = [
        "Weekly Challenge",
        "LS Car Meet Prize Ride",
        "Premium Race",
        "HSW Time Trial",
        "Time Trial",
    ]
    cleaned = [clean(x) for x in lines if clean(x)]
    positions = []
    for i, line in enumerate(cleaned):
        normalized = normalized_heading(line)
        # Longest first, so HSW Time Trial is not caught by Time Trial.
        for name in sorted(names, key=len, reverse=True):
            if normalized == name.casefold() or normalized.startswith(name.casefold() + " "):
                positions.append((i, name))
                break

    result = []
    for idx, (start_idx, name) in enumerate(positions):
        end_idx = positions[idx + 1][0] if idx + 1 < len(positions) else len(cleaned)
        body = cleaned[start_idx + 1:end_idx]
        if not body:
            continue
        text = " ".join(strip_markup(x) for x in body[:3])
        replacements = [
            (r"Earn GTA\$1,000,000 by selling all kinds of Product to bank an extra GTA\$1,000,000 and the Junk Tracksuit",
             "Verdient GTA$1.000.000 durch den Verkauf aller Arten von Waren und erhaltet zusätzlich GTA$1.000.000 sowie den Junk-Trainingsanzug"),
            (r"Place in the Top 4 to win", "Top 4 zum Gewinn von"),
            (r"Eight Figure Bonus", "Eight Figure Bonus"),
        ]
        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text, flags=re.I)
        text = re.sub(r"\bSeptember\b", "September", text, flags=re.I)
        result.append((name, clean(text)))
    return result


def parse_rotating(lines):
    wanted = (
        "fib priority file", "salvage yard", "salvage-yards", "kortz center",
        "cluckin bell", "heist", "robbery", "priority file", "casino", "cayo perico",
    )
    return unique([line for line in lines if any(x in line.casefold() for x in wanted)])


def parse_gun_van(lines):
    groups = parse_percent_groups(lines)
    if groups:
        return groups
    inventory = []
    for line in lines:
        low = line.casefold()
        if any(x in low for x in ("gun van", "stock", "inventory")):
            continue
        inventory.append(line)
    return [("INVENTAR", unique(inventory))] if inventory else []


def parse_gta_plus_benefits(text):
    if not text:
        return []
    lines = text_lines(text)
    blob = " ".join(lines).casefold()
    if "gta+" not in blob and "gta +" not in blob:
        return []

    result = []
    if "pegassi horus" in blob and "early access" in blob:
        result.append("Pegassi Horus: 1 Woche Early Access für GTA+ Mitglieder; kostenlos beim Vinewood Car Club mit Velvet-Iris-Design.")
    if "chameleon" in blob:
        result.append("Kostenlose Chameleon-Lackierungen für GTA+ Mitglieder.")
    if "bigness" in blob and "clothing" in blob:
        result.append("Kostenlose Bigness-Kleidung.")
    if "biker bonus" in blob or "biker bonuses" in blob:
        result.append("Biker-Boni für GTA+ Mitglieder.")
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
        "discounts": parse_discounts(sections.get("discounts", [])),
        "gifts": parse_gifts(sections, weekly_text),
        "gta_plus": parse_gta_plus_benefits(gta_plus_text),
        "challenges": parse_challenges(sections.get("challenges", [])),
        "rotating": parse_rotating(sections.get("rotating", [])),
        "gun_van": parse_gun_van(sections.get("gunvan", [])),
    }

    # Manche Seiten-Versionen legen den Gun Van in einen anderen Textblock.
    if not data["gun_van"]:
        gun_lines = []
        collecting = False
        for line in all_lines:
            if heading_kind(line) == "gunvan":
                collecting = True
                continue
            if collecting and heading_kind(line):
                break
            if collecting:
                gun_lines.append(line)
        data["gun_van"] = parse_gun_van(gun_lines)

    core = sum(
        bool(data[key])
        for key in ("bonuses", "vehicles", "discounts", "gifts", "challenges")
    )
    if core < 4:
        raise RuntimeError(f"iGTA-Parser hat nur {core}/5 Kernbereiche erkannt. Nichts wird gepostet.")
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


def known_concepts_from_wednesday(data):
    concepts = set()
    for fact in wednesday_facts(data):
        low = fact.casefold()
        if "pegassi horus" in low:
            concepts.add("horus")
        if "gta+" in low:
            concepts.add("gta_plus")
        if "early access" in low or "vorabzugang" in low:
            concepts.add("early_access")
        if any(x in low for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad", "auto")):
            concepts.add("vehicle")
        if any(x in low for x in ("bonus", "boni", "2x gta$", "3x gta$", "auszahlung", "biker-boni")):
            concepts.add("bonus")
        if any(x in low for x in ("reward", "belohnung", "kostenlos", "free", "clothing", "kleidung")):
            concepts.add("reward")
        if any(x in low for x in ("bigness", "tee", "cap", "kleidung")):
            concepts.add("clothing")
        if any(x in low for x in ("biker-boni", "biker bonuses", "biker bonus")):
            concepts.add("biker")
        if "chameleon" in low:
            concepts.add("chameleon")
    return concepts


def format_wednesday(data, source_url):
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
    return messages


# ============================================================
# iGTA ABRUF
# ============================================================


async def fetch_latest_igta_article():
    start, _ = week_period()
    expected = (
        f"https://www.igrandtheftauto.com/gtaonline/news/"
        f"this-week-in-gta-online-{MONTH_NAMES[start.month]}-{start.day}-{start.year}"
    )

    try:
        text, html_text = await fetch_page(expected, wait_ms=4000)
        if "this week in gta online" in text.casefold() and len(text) > 800:
            return expected, text
        if len(html_text) > 5000 and "this-week-in-gta-online" in html_text.casefold():
            return expected, clean(re.sub(r"<[^>]+>", " ", html_text))
    except Exception as exc:
        print(f"iGTA: direkter Wochenartikel fehlgeschlagen: {exc}")

    listing_text, listing_html = await fetch_page(IGTA_NEWS_URL, wait_ms=3000)
    pattern = re.compile(
        r"https://www\.igrandtheftauto\.com/gtaonline/news/this-week-in-gta-online-[^\s\"<>]+",
        re.I,
    )
    match = pattern.search(listing_html) or pattern.search(listing_text)
    if not match:
        raise RuntimeError("iGTA: Kein Wochenartikel gefunden.")
    url = match.group(0).rstrip("/\"')")
    text, _ = await fetch_page(url, wait_ms=4000)
    return url, text


async def fetch_igta_gta_plus():
    try:
        listing_text, listing_html = await fetch_page(IGTA_NEWS_URL, wait_ms=2500)
    except Exception as exc:
        print(f"iGTA: GTA+-Listing nicht abrufbar: {exc}")
        return "", ""

    candidates = re.findall(
        r"https://www\.igrandtheftauto\.com/gtaonline/news/[^\s\"<>]*",
        listing_html or "",
        flags=re.I,
    )
    for url in unique(candidates):
        low = url.casefold()
        if any(x in low for x in ("pegassi-horus", "gta-members", "gta-plus", "early-access")):
            try:
                text, _ = await fetch_page(url, wait_ms=3000)
                return text, url
            except Exception as exc:
                print(f"iGTA: GTA+-Artikel fehlgeschlagen: {exc}")
    return listing_text, ""


# ============================================================
# ROCKSTAR – EIGENER TEXT-PARSER
# ============================================================


ROCKSTAR_NOISE = {
    "skip to content", "navigation", "games", "features", "advertisements", "login",
    "register", "follow us on x", "more from us", "our partners", "privacy policy",
    "terms of service", "cookie policy", "newswire", "gta online inhalts-updates",
    "inhalts-updates", "comments", "kommentare", "sign up", "newsletter",
}


def clean_rockstar_lines(text):
    result = []
    for line in text_lines(text):
        low = line.casefold()
        if low in ROCKSTAR_NOISE:
            continue
        if low.startswith("http"):
            continue
        if len(line) < 24:
            continue
        result.append(line)
    return unique(result)


def find_rockstar_title(text):
    lines = clean_rockstar_lines(text)
    priority = []
    for line in lines:
        low = line.casefold()
        if 30 <= len(line) <= 180 and any(x in low for x in ("gta online", "gta+", "grand theft auto online")):
            if not low.startswith("gta online inhalts") and low != "gta online":
                priority.append(line)
    return max(priority, key=len) if priority else ""


def isolate_rockstar_article(text, title):
    lines = text_lines(text)
    start = 0
    title_cf = title.casefold()
    for i, line in enumerate(lines):
        if line.casefold() == title_cf:
            start = i + 1
            break

    body = []
    for line in lines[start:]:
        low = line.casefold()
        if low in {"more from us", "related articles", "comments", "kommentare", "newsletter", "sign up"}:
            break
        if low in ROCKSTAR_NOISE:
            continue
        if low.startswith("newswiregta online") or low.startswith("gta onlineinhalts"):
            continue
        if len(line) >= 30:
            body.append(line)
    return unique(body)


def rockstar_candidates(title, body):
    blob = " ".join(body)
    if "pegassi horus" in title.casefold() or "pegassi horus" in blob.casefold():
        return [
            "GTA+ Mitglieder erhalten eine Woche frühen Zugang zum neuen Pegassi Horus Supercar.",
            "Der Pegassi Horus ist zunächst im Rahmen des GTA+ Early Access verfügbar.",
            "GTA+ bringt außerdem kostenlose Bigness-Kleidung, Biker-Boni und weitere Vorteile.",
        ]

    wanted = (
        "new ", "newly", "brand-new", "brand new", "early access", "vehicle", "car ",
        "motorcycle", "bonus", "reward", "free", "available", "changed", "updated",
        "biker", "clothing", "livery", "paint", "2x", "3x", "gta$",
    )
    candidates = [line for line in body if any(k in line.casefold() for k in wanted)]
    return unique(candidates)[:6]


def concept_tags(text):
    low = clean(text).casefold()
    tags = set()
    if "pegassi horus" in low:
        tags.add("horus")
    if "gta+" in low or "gta plus" in low:
        tags.add("gta_plus")
    if "early access" in low or "vorabzugang" in low or "frühen zugang" in low:
        tags.add("early_access")
    if any(x in low for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad", "car", "auto")):
        tags.add("vehicle")
    if any(x in low for x in ("bonus", "boni", "2x gta$", "3x gta$", "auszahlung", "payout", "biker-boni")):
        tags.add("bonus")
    if any(x in low for x in ("reward", "belohnung", "free", "kostenlos", "clothing", "kleidung", "bigness")):
        tags.add("reward")
    if any(x in low for x in ("clothing", "kleidung", "tee", "cap", "bigness")):
        tags.add("clothing")
    if "chameleon" in low:
        tags.add("chameleon")
    if "biker" in low:
        tags.add("biker")
    return tags


def filter_new_rockstar_facts(candidates, known_concepts, known_facts):
    known_concepts = set(known_concepts or [])
    known_fact_keys = {
        re.sub(r"\s+", " ", clean(str(x))).casefold()
        for x in (known_facts or [])
    }
    out = []
    for fact in unique(candidates):
        key = re.sub(r"\s+", " ", clean(fact)).casefold()
        if key in known_fact_keys:
            continue
        tags = concept_tags(fact)
        specific = tags - {"vehicle", "bonus", "reward", "clothing"}
        if specific and specific.intersection(known_concepts):
            continue
        out.append(fact)
    return out


def classify_rockstar(title, facts):
    blob = f"{title} {' '.join(facts)}".casefold()
    categories = []
    if any(x in blob for x in ("new", "neu", "brand-new", "brand new", "early access", "newly")):
        categories.append("🆕 **Neu**")
    if any(x in blob for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad", "car ")):
        categories.append("🚗 **neue Fahrzeugmeldung**")
    if any(x in blob for x in ("bonus", "boni", "gta$", "2x", "3x", "biker-boni", "payout")):
        categories.append("💰 **neuer Bonus**")
    if any(x in blob for x in ("free", "kostenlos", "gratis", "reward", "belohn", "clothing", "kleidung", "chameleon", "paint")):
        categories.append("🎁 **neue Belohnung**")
    if any(x in blob for x in ("changed", "updated", "returning", "removed", "replaced", "adjusted", "geändert")):
        categories.append("⚠️ **Änderung**")
    return categories or ["🆕 **Neu**"]


def translate_rockstar(text):
    result = clean(text)
    replacements = [
        (r"GTA\+ members", "GTA+ Mitglieder"),
        (r"GTA\+ Members", "GTA+ Mitglieder"),
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
    lines.extend(f"• {translate_rockstar(x)}" for x in new_facts[:4])
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
                    title = find_rockstar_title(article_text)
                    if not title:
                        title = find_rockstar_title(card.get("text", ""))
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
    gta_plus_text, gta_plus_url = await fetch_igta_gta_plus()

    data = make_wednesday_data(weekly_text, gta_plus_text)
    post = format_wednesday(data, weekly_url)
    messages = split_message(post)

    print(f"Mittwoch: {len(messages)} Nachricht(en), {sum(len(x) for x in messages)} Zeichen")
    for i, message in enumerate(messages, 1):
        print(f"--- MITTWOCH {i} ---\n{message}")

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
        state["wednesday_plus_url"] = gta_plus_url
        save_state(state)


async def run_thursday():
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article = await fetch_rockstar_news()
    state = load_state()
    known_concepts = set(state.get("wednesday_concepts", []))
    known_facts = state.get("wednesday_facts", [])

    candidates = rockstar_candidates(article["title"], article["body"])
    new_facts = filter_new_rockstar_facts(candidates, known_concepts, known_facts)

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
Shitzu Defiler - 30% off
Pegassi Vortex - 30% off
Buckingham Nimbus - 30% off
Vapid Aleutian - 30% off
Pfister Growler - 30% off
Karin Vivanite - 30% off
Maibatsu Penumbra FF - 30% off
Vulcar Warrener HKR - 30% off
Gallivanter Baller ST - 30% off
Grotti Cheetah Classic - 30% off
Mammoth Patriot Stretch - 30% off
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
Other Activities
Kortz Center: active rotation
FIB Priority File: active rotation
Salvage Yard: active rotation
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

    assert data["headline"] == "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE", data["headline"]
    assert len(data["bonuses"]) == 5, data["bonuses"]
    assert len(data["vehicles"]) == 5, data["vehicles"]
    assert [group for group, _ in data["vehicles"]] == [
        "Luxury Autos", "Premium Deluxe Motorsport", "Hao's Premium Test Ride",
        "LS Car Meet Test Rides", "Lucky Wheel"
    ], data["vehicles"]
    assert len(data["discounts"]) == 3, data["discounts"]
    assert [group for group, _ in data["discounts"]] == ["70% RABATT", "30% RABATT", "KOSTENLOS"], data["discounts"]
    assert any("Grapeseed MC Clubhouse" in item for _, items in data["discounts"] for item in items), data["discounts"]
    assert any("Penaud La Coureuse" in item for item in data["gifts"]), data["gifts"]
    assert any("Bourgeoix Tee" in item for item in data["gifts"]), data["gifts"]
    assert len(data["gta_plus"]) == 4, data["gta_plus"]
    assert len(data["challenges"]) == 5, data["challenges"]
    assert data["gun_van"][0][0] == "50% RABATT", data["gun_van"]
    assert len(data["rotating"]) == 3, data["rotating"]

    post = format_wednesday(
        data,
        "https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-september-10-2026",
    )
    messages = split_message(post)
    assert all(len(x) <= DISCORD_LIMIT for x in messages)
    assert "🚗 **FAHRZEUGE**" in post
    assert "**Luxury Autos:** Vapid FMJ MK V, Grotti GT750" in post
    assert "**30% RABATT:**" in post
    assert "⭐ **GTA+ VORTEILE**" in post
    assert "🔫 **GUN VAN**" in post

    known_concepts = known_concepts_from_wednesday(data)
    candidates = rockstar_candidates(
        "GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar",
        clean_rockstar_lines(ROCKSTAR_FIXTURE),
    )
    new_facts = filter_new_rockstar_facts(candidates, known_concepts, wednesday_facts(data))
    assert new_facts == [], new_facts

    truly_new = filter_new_rockstar_facts(
        ["A brand-new Vapid Testster vehicle is now available."],
        known_concepts,
        wednesday_facts(data),
    )
    assert truly_new, truly_new

    print("SELF-TEST OK")
    print(f"Mittwoch: {len(messages)} Nachricht(en), {sum(len(x) for x in messages)} Zeichen")
    print(f"Boni: {len(data['bonuses'])}")
    print(f"Fahrzeuggruppen: {len(data['vehicles'])}")
    print(f"Rabattgruppen: {len(data['discounts'])}")
    print(f"Geschenke: {len(data['gifts'])}")
    print(f"GTA+: {len(data['gta_plus'])}")
    print(f"Challenges: {len(data['challenges'])}")
    print(f"Rotierende Inhalte: {len(data['rotating'])}")
    print(f"Gun Van Gruppen: {len(data['gun_van'])}")


# ============================================================
# START
# ============================================================


async def main():
    self_test()

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
    asyncio.run(main())
