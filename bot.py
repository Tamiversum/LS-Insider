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


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    return text.strip()


def strip_markup(line: str) -> str:
    line = clean(line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^[-*•▪◦·]\s*", "", line)
    return clean(line)


def unique(items):
    out, seen = [], set()
    for item in items:
        item = clean(item)
        if not item:
            continue
        key = re.sub(r"\s+", " ", item).casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    if not TEST_MODE:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def post_to_discord(messages):
    if isinstance(messages, str):
        messages = [messages]

    for index, message in enumerate(messages, 1):
        if len(message) > DISCORD_LIMIT:
            raise RuntimeError(f"Discord-Nachricht {index} ist zu lang: {len(message)}")

    if TEST_MODE:
        for index, message in enumerate(messages, 1):
            print(f"TEST_MODE: Discord-Nachricht {index}/{len(messages)} NICHT gesendet ({len(message)} Zeichen)")
            print("--- TEST-NACHRICHT ---")
            print(message)
        return

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL ist nicht gesetzt.")

    for index, message in enumerate(messages, 1):
        response = requests.post(
            webhook,
            json={"content": message, "allowed_mentions": {"parse": []}},
            timeout=45,
        )
        response.raise_for_status()
        print(f"Discord: Nachricht {index}/{len(messages)} gesendet ({len(message)} Zeichen)")


def week_period(today: date | None = None):
    today = today or datetime.now(VIENNA).date()
    delta = (today.weekday() - 3) % 7
    start = today - timedelta(days=delta)
    return start, start + timedelta(days=6)


async def new_context(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    context = await browser.new_context(
        locale="de-DE",
        viewport={"width": 1440, "height": 1200},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
        ),
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


async def extract_visible_page(page):
    best = ""
    for selector in ("article", "main article", "main", "[role='main']", "body"):
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = clean(await locator.inner_text(timeout=12000))
                if len(text) > len(best):
                    best = text
        except Exception:
            pass
    return best


async def fetch_page(url, wait_ms=3500):
    async with async_playwright() as playwright:
        browser, context = await new_context(playwright)
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(wait_ms)
            text = await extract_visible_page(page)
            print(f"Abruf: {url}")
            print(f"HTTP: {response.status if response else '?'} | Sichtbarer Text: {len(text)} Zeichen")
            return text
        finally:
            await context.close()
            await browser.close()


# ------------------------ Mittwoch ------------------------

IGTA_HEADINGS = {
    "bonuses": {"bonuses and rewards", "bonuses", "rewards and bonuses"},
    "free": {"free penaud la coureuse", "free penaud la coureuse and hsw upgrade"},
    "discounts": {"discounts", "discount"},
    "vehicles": {"vehicles", "vehicles and showrooms"},
    "challenges": {"challenges", "weekly challenges"},
    "rotating": {
        "other activities",
        "weekly rotating content",
        "rotating content",
        "additional activities",
        "other content",
    },
}


def heading_kind(line):
    value = strip_markup(line).casefold().rstrip(":").strip()
    for kind, aliases in IGTA_HEADINGS.items():
        if value in aliases:
            return kind
    return None


def split_sections(text):
    result, current = {}, None
    for raw in (text or "").splitlines():
        line = clean(raw)
        if not line:
            continue
        kind = heading_kind(line)
        if kind:
            current = kind
            result.setdefault(kind, [])
        elif current:
            result[current].append(line)
    return result


def detect_event(text):
    low = clean(text).casefold()
    for needle, title in (
        ("business rivalries", "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"),
        ("brand wars", "BRAND WARS"),
        ("motor madness", "MOTOR MADNESS"),
        ("summer heist", "SUMMER HEIST"),
        ("money fronts", "MONEY FRONTS"),
    ):
        if needle in low:
            return title
    return "GTA ONLINE EVENTWOCHE"


def translate_bonus(line):
    result = clean(line)
    replacements = (
        (r"2X GTA\$ and RP", "2X GTA$ und RP"),
        (r"3X GTA\$ and RP", "3X GTA$ und RP"),
        (r"2X GTA\$", "2X GTA$"),
        (r"3X GTA\$", "3X GTA$"),
        (r"Biker Clubhouse Contracts", "Biker-Clubhaus-Verträge"),
        (r"MC Work", "MC-Arbeiten"),
        (r"MC Challenges", "MC-Herausforderungen"),
        (r"Bike Service Missions", "Motorrad-Service-Missionen"),
        (r"Bike Service", "Bike-Service"),
        (r"Street Dealer Sales", "Verkäufe bei Straßenhändlern"),
        (r"Community Mission Series", "Community-Missionsserie"),
        (r"Featured Series", "Featured Series"),
        (r"for GTA\+ Members", "für GTA+ Mitglieder"),
        (r"\bon\b", "für"),
        (r"\band\b", "und"),
    )
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def parse_bonuses(lines):
    return unique(
        [translate_bonus(x) for x in lines if re.search(r"\b[23]x gta\$|double rewards", x, re.I)]
    )


def parse_percent_groups(lines):
    groups = {}
    for raw in lines:
        line = clean(raw)
        low = line.casefold()
        if re.search(r"\bfree\b|\bk無料\b", low):
            item = re.sub(r"\bfree\b", "", line, flags=re.I).strip(" -–—:")
            if item:
                groups.setdefault(0, []).append(item)
            continue
        match = re.search(r"(\d{1,3})\s*%\s*(?:off|discount|rabatt)?", line, re.I)
        if not match:
            continue
        pct = int(match.group(1))
        item = re.sub(r"\b\d{1,3}\s*%\s*(?:off|discount|rabatt)?\b", "", line, flags=re.I)
        item = item.strip(" -–—:")
        if item:
            groups.setdefault(pct, []).append(item)
    ordered = sorted(groups, key=lambda p: (p != 0, -p))
    return [
        ("KOSTENLOS" if pct == 0 else f"{pct}% RABATT", unique(groups[pct]))
        for pct in ordered
    ]


def vehicle_group(line):
    value = strip_markup(line).casefold()
    aliases = (
        ("luxury autos", "Luxury Autos"),
        ("luxury autos showroom", "Luxury Autos"),
        ("premium deluxe motorsport", "Premium Deluxe Motorsport"),
        ("premium deluxe motorsport showroom", "Premium Deluxe Motorsport"),
        ("hao's premium test ride", "Hao's Premium Test Ride"),
        ("hao’s premium test ride", "Hao's Premium Test Ride"),
        ("ls car meet test rides", "LS Car Meet Test Rides"),
        ("the diamond casino and resort lucky wheel", "Lucky Wheel"),
        ("diamond casino and resort lucky wheel", "Lucky Wheel"),
        ("lucky wheel", "Lucky Wheel"),
    )
    for needle, label in aliases:
        if value == needle or value.startswith(needle + ":") or value.startswith(needle + " "):
            return label
    return None


def parse_vehicles(lines):
    result, current, bucket = [], None, []

    footer_markers = {
        "games", "gta vi", "gta online", "gta v", "ballad of gay tony",
        "chinatown wars", "lost and damned", "gta iv", "vice city stories",
        "liberty city stories", "gta san andreas", "gta advance", "gta vice city",
        "gta iii", "gta 2", "gta london 1961", "gta london 1969",
        "features", "news", "info", "guides", "maps", "images", "videos",
        "downloads", "forum", "sitemap", "contact us", "about us", "staff",
        "members", "copyright notice", "privacy policy", "more from us",
        "bluesky", "facebook", "x / twitter", "our partners",
    }

    hard_stop_phrases = (
        "0 comments",
        "add your comment",
        "there are currently no comments",
        "more gta online news",
        "all gta online news",
        "advertisements",
        "discover more",
        "action & platform games",
        "thanks for your support",
        "this article has been viewed",
        "unlock game guides",
        "© 2009",
        "all trademarks",
    )

    def flush():
        nonlocal bucket
        if current and bucket:
            result.append((current, unique(bucket)))
        bucket = []

    for raw in lines:
        line = clean(raw)
        low = strip_markup(line).casefold().rstrip(":").strip()

        cut_positions = [low.find(marker) for marker in hard_stop_phrases if low.find(marker) >= 0]
        stop_after_prefix = False
        if cut_positions:
            cut_at = min(cut_positions)
            prefix = clean(line[:cut_at].rstrip(" ,;:-"))
            if prefix:
                line = prefix
                low = strip_markup(line).casefold().rstrip(":").strip()
                stop_after_prefix = True
            else:
                flush()
                current = None
                break

        if low in footer_markers:
            flush()
            current = None
            break

        group = vehicle_group(line)
        if group:
            flush()
            current = group
            remainder = re.sub(r"^.*?:\s*", "", line).strip()
            if remainder and remainder.casefold() != group.casefold():
                bucket.extend([clean(x) for x in remainder.split(",") if clean(x)])
            if stop_after_prefix:
                flush()
                current = None
                break
            continue

        match = re.match(r"^(.+?)\s*\(([^)]*)\)\s*$", line)
        if current and match:
            bucket.append(clean(match.group(1)))
        elif current and len(line) >= 2:
            parts = [clean(x) for x in line.split(",") if clean(x)]
            bucket.extend(parts)

        if stop_after_prefix:
            flush()
            current = None
            break

    flush()
    return result


def parse_gifts(sections, full_text):
    out = []
    free_text = " ".join(sections.get("free", []))
    if "penaud la coureuse" in free_text.casefold():
        out.append(
            "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; "
            "inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September."
        )
    clothing = [
        "Six Figure Tee",
        "Ride or Die Tee",
        "Red and White Ammu-Nation Cap",
        "Bourgeoix Tee",
    ]
    low = full_text.casefold()
    found = [x for x in clothing if x.casefold() in low]
    if found:
        out.append("Kostenlose Kleidung durch Business Battles: " + ", ".join(found) + ".")
    return unique(out)


def parse_challenges(lines):
    names = [
        "Weekly Challenge",
        "LS Car Meet Prize Ride",
        "Premium Race",
        "HSW Time Trial",
        "Time Trial",
    ]

    inventory_markers = (
        "gun van inventory",
        "tactical smg",
        "railgun – 40% off",
        "railgun - 40% off",
        "compact emp launcher",
        "double barrel shotgun",
        "assault smg",
        "pipe wrench",
        "pipe bombs",
        "proximity mines",
        "sticky bombs",
    )

    cleaned_lines = []
    for raw in lines:
        line = clean(raw)
        if not line:
            continue
        low = line.casefold()
        cut_positions = [
            low.find(marker) for marker in inventory_markers
            if low.find(marker) >= 0
        ]
        if cut_positions:
            cut_at = min(cut_positions)
            line = clean(line[:cut_at].rstrip(" ,;:-"))
        if line:
            cleaned_lines.append(line)

    hits = []
    for i, line in enumerate(cleaned_lines):
        normalized = strip_markup(line).rstrip(":：").strip().casefold()
        for name in sorted(names, key=len, reverse=True):
            if normalized == name.casefold() or normalized.startswith(name.casefold() + " "):
                hits.append((i, name))
                break

    out = []
    for index, (start_i, name) in enumerate(hits):
        end_i = hits[index + 1][0] if index + 1 < len(hits) else len(cleaned_lines)
        start_line = strip_markup(cleaned_lines[start_i]).rstrip(":：").strip()
        remainder = ""
        if start_line.casefold().startswith(name.casefold() + " "):
            remainder = start_line[len(name):].strip(" :–—-\t")

        body = []
        if remainder:
            body.append(remainder)
        body.extend(cleaned_lines[start_i + 1:end_i])
        text = " ".join(x for x in body if clean(x))

        # The iGTA page can append the Gun Van inventory directly after the Time Trial.
        # Keep only the actual Time Trial location.
        if name == "Time Trial":
            for marker in inventory_markers[5:]:
                pos = text.casefold().find(marker)
                if pos >= 0:
                    text = clean(text[:pos].rstrip(" ,;:-"))
                    break

        text = re.sub(
            r"^Place in the Top 4.*?Pfister Neon$",
            "Platz unter den Top 4 in der LS Car Meet Series für den Pfister Neon",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"^Place Top 4.*?Pfister Neon$",
            "Platz unter den Top 4 in der LS Car Meet Series für den Pfister Neon",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"^Earn GTA\$1,000,000.*?Junk (?:Tracksuit|Trainingsanzug)$",
            "Verdient GTA$1.000.000 durch den Verkauf aller Arten von Waren und erhaltet zusätzlich GTA$1.000.000 sowie den Junk-Trainingsanzug",
            text,
            flags=re.I,
        )
        text = re.sub(r"Del Perro Beach to Murietta Heights", "Del Perro Beach nach Murietta Heights", text, flags=re.I)
        text = clean(text)
        if text:
            out.append((name, text))
    return out


def parse_rotating(lines):
    heading_aliases = {
        "the kortz center heist primary targets": "Kortz Center Heist – Hauptziele",
        "fib priority file": "FIB Priority File",
        "salvage yard robbery targets": "Salvage Yard – Raubziele",
    }
    groups, current, bucket = [], None, []

    def flush():
        nonlocal current, bucket
        if current and bucket:
            groups.append(f"**{current}:** " + ", ".join(unique(bucket)))
        current, bucket = None, []

    for raw in lines:
        line = clean(raw)
        low = strip_markup(line).casefold()
        if any(marker in low for marker in (
            "this article has been viewed",
            "share",
            "discover more",
            "unlock game guides",
            "thanks for your support",
        )):
            flush()
            break
        normalized_heading = low.rstrip(":：").strip()
        heading = next((label for needle, label in heading_aliases.items() if normalized_heading == needle or normalized_heading.startswith(needle + " ")), None)
        if heading:
            flush()
            current = heading
            remainder = re.sub(r"^.*?:\s*", "", line).strip()
            if remainder and remainder.casefold() != heading.casefold():
                bucket.extend([clean(x) for x in remainder.split(",") if clean(x)])
            continue
        if current and line:
            bucket.extend([clean(x) for x in re.split(r"\n|(?<=\))\s+(?=[A-Z])", line) if clean(x)])
    flush()
    return unique(groups)


# GTA+ is intentionally handled as its own article, not inferred from the normal weekly article.
GTA_PLUS_URL = f"{IGTA_NEWS_URL}/gta-members-enjoy-one-week-of-early-access-to-the-new-pegassi-horus-supercar"


def parse_gta_plus_article(full_text):
    """Extract the entire useful GTA+ article in stable, topic-sized facts.

    This deliberately uses broad topic markers rather than brittle exact full-sentence matches.
    """
    text = clean(full_text)
    low = text.casefold()
    out = []

    def add(condition, value):
        if condition:
            out.append(value)

    add(
        "pegassi horus" in low and "early access" in low,
        "Pegassi Horus: 1 Woche Early Access für GTA+ Mitglieder; kostenlos beim Vinewood Car Club mit exklusiver Velvet-Iris-Lackierung.",
    )
    add(
        "chameleon" in low and ("cyan/red flip" in low or "cyan/red" in low or "chamäleon" in low),
        "Kostenlose Chameleon-Lackierung und Chameleon-Felgenfarbe für GTA+ Mitglieder.",
    )
    add(
        "bigness patchwork" in low and ("revere collar" in low or "patchwork jeans" in low),
        "Kostenlose Bigness-Patchwork-Kleidung: Bigness Patchwork Revere Collar Shirt und Bigness Patchwork Jeans.",
    )
    add(
        "cluckin' bell" in low and "first completion" in low or "cluckin' bell" in low and "first weekly" in low,
        "2X GTA$ für den ersten wöchentlichen Abschluss von „Scene of the Crime“ im Cluckin' Bell Farm Raid.",
    )
    add(
        "60% off" in low and "biker" in low,
        "60% Rabatt auf Biker-Unternehmen sowie deren Upgrades und Anpassungen für GTA+ Mitglieder.",
    )
    add(
        "cocaine production" in low and ("doubled" in low or "double" in low),
        "Doppelte Produktionsgeschwindigkeit im Kokain-Labor für Biker-Unternehmen von GTA+ Mitgliedern.",
    )
    add(
        "3x gta$" in low and "bike service" in low,
        "3X GTA$ und RP für Bike-Service-Arbeiten für GTA+ Mitglieder.",
    )
    add(
        "50% off" in low and "nagasaki" in low,
        "50% Rabatt auf alle Nagasaki-Motorräder: Shinobi, Shotaro, Stryder, Chimera und BF400.",
    )
    add(
        "gta$500,000" in low or "500,000" in low and "maze bank" in low,
        "GTA$500.000 werden GTA+ Mitgliedern monatlich zum Abrechnungszeitpunkt gutgeschrieben.",
    )
    add(
        "15%" in low and "shark card" in low,
        "Spezielle GTA+ Shark Cards bieten 15% Bonus-GTA$.",
    )
    add(
        "vinewood club app" in low,
        "Zugang zur Vinewood Club App mit Funktionen zur Verwaltung von Unternehmen, Einnahmen und angeforderten Fahrzeugen.",
    )
    add(
        "games library" in low,
        "Zugang zur wechselnden GTA+ Games Library mit Rockstar-Spielen, solange sie über den Service verfügbar sind.",
    )
    return unique(out)


def make_wednesday_data(weekly_text, gta_plus_text):
    sec = split_sections(weekly_text)
    data = {
        "headline": detect_event(weekly_text),
        "period": week_period(),
        "intro": "Die Geschäfte in Los Santos laufen auf Hochtouren. Diese Woche warten wieder erhöhte Auszahlungen, Rabatte und besondere Angebote auf euch.",
        "bonuses": parse_bonuses(sec.get("bonuses", [])),
        "vehicles": parse_vehicles(sec.get("vehicles", [])),
        "discounts": parse_percent_groups(sec.get("discounts", [])),
        "gifts": parse_gifts(sec, weekly_text),
        "gta_plus": parse_gta_plus_article(gta_plus_text),
        "challenges": parse_challenges(sec.get("challenges", [])),
        "rotating": parse_rotating(sec.get("rotating", [])),
    }
    core = sum(bool(data[key]) for key in ("bonuses", "vehicles", "discounts", "gifts", "challenges"))
    if core < 4:
        raise RuntimeError(f"iGTA-Parser hat nur {core}/5 Kernbereiche erkannt. Nichts wird gepostet.")
    return data


def wednesday_facts(data):
    facts = list(data.get("bonuses", []))
    for _, items in data.get("vehicles", []):
        facts.extend(items)
    for _, items in data.get("discounts", []):
        facts.extend(items)
    facts.extend(data.get("gifts", []))
    facts.extend(data.get("gta_plus", []))
    facts.extend(f"{n}: {t}" for n, t in data.get("challenges", []))
    facts.extend(data.get("rotating", []))
    return unique(facts)


def known_concepts(data):
    concepts = set()
    for fact in wednesday_facts(data):
        low = fact.casefold()
        for concept, markers in {
            "horus": ("pegassi horus",),
            "early_access": ("early access", "vorabzugang", "frühen zugang"),
            "chameleon": ("chameleon", "chamäleon"),
            "bigness": ("bigness",),
            "cluckin_bell": ("cluckin' bell", "cluckin bell"),
            "scene_of_crime": ("scene of the crime", "tatort"),
            "biker": ("biker",),
            "bike_service": ("bike-service", "bike service", "motorrad-service"),
            "nagasaki": ("nagasaki",),
            "monthly_500k": ("500.000", "500,000", "500k"),
            "cashcard": ("shark cards", "shark card", "cashcard"),
            "vinewood_app": ("vinewood club app",),
            "games_library": ("games library",),
        }.items():
            if any(marker in low for marker in markers):
                concepts.add(concept)
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
        lines += ["", "💰 **DIE GROSSEN GESCHÄFTE**"] + [f"• {x}" for x in data["bonuses"]]
    if data["vehicles"]:
        lines += ["", "🚗 **NEUES AUF DEN STRASSEN**"]
        lines += [f"**{group}:** {', '.join(items)}" for group, items in data["vehicles"]]
    if data["discounts"]:
        lines += ["", "🏷️ **LAUFENDE DEALS**"]
        lines += [f"**{group}:** {', '.join(items)}" for group, items in data["discounts"]]
    if data["gifts"]:
        lines += ["", "🎁 **GESCHENKE & BEUTE**"] + [f"• {x}" for x in data["gifts"]]
    if data["gta_plus"]:
        lines += ["", "⭐ **DAS PLUS FÜR EUCH**"] + [f"• {x}" for x in data["gta_plus"]]
    if data["challenges"]:
        lines += ["", "🏆 **DIE WOCHENAUFTRÄGE**"] + [f"• **{name}:** {text}" for name, text in data["challenges"]]
    if data["rotating"]:
        lines += ["", "📍 **DIESE WOCHE AKTUELL IN LOS SANTOS**"] + [f"• {x}" for x in data["rotating"]]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**",
        f"<{source_url}>",
    ]
    return "\n".join(lines)


def split_message(text):
    """Split safely at Discord's limit, including oversized single lines."""
    if len(text) <= DISCORD_LIMIT:
        return [text]

    def hard_split(line):
        parts = []
        remaining = clean(line)
        while len(remaining) > DISCORD_LIMIT:
            cut = remaining.rfind(" ", 0, DISCORD_LIMIT + 1)
            if cut < 1:
                cut = DISCORD_LIMIT
            parts.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            parts.append(remaining)
        return parts

    out, current = [], []
    current_len = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        for line in hard_split(raw):
            if not current:
                current = [line]
                current_len = len(line)
                continue
            candidate_len = current_len + 1 + len(line)
            if candidate_len <= DISCORD_LIMIT:
                current.append(line)
                current_len = candidate_len
            else:
                out.append("\n".join(current).strip())
                current = [line]
                current_len = len(line)
    if current:
        out.append("\n".join(current).strip())
    return out


async def fetch_latest_igta_article():
    start, _ = week_period()
    direct = f"{IGTA_NEWS_URL}/this-week-in-gta-online-{start.strftime('%B').lower()}-{start.day}-{start.year}"
    text = await fetch_page(direct, 4000)
    if "this week in gta online" in text.casefold() and len(text) > 800:
        return direct, text
    listing = await fetch_page(IGTA_NEWS_URL, 3000)
    match = re.search(
        r"https://www\.igrandtheftauto\.com/gtaonline/news/this-week-in-gta-online-[\w-]+",
        listing,
        re.I,
    )
    if not match:
        raise RuntimeError("iGTA: Kein Wochenartikel gefunden.")
    url = match.group(0)
    return url, await fetch_page(url, 4000)


async def fetch_igta_gta_plus():
    text = await fetch_page(GTA_PLUS_URL, 4000)
    if "gta+" in text.casefold() and "pegassi horus" in text.casefold():
        return text, GTA_PLUS_URL
    return "", ""


# ------------------------ Donnerstag ------------------------

ROCKSTAR_NOISE = (
    "this article has been viewed",
    "thanks for your support",
    "share",
    "discover more",
    "unlock game guides",
)


def clean_rockstar_lines(text):
    out = []
    for line in text.splitlines():
        line = clean(line)
        if not line:
            continue
        low = line.casefold()
        if low in {"newswire", "gta online", "inhalts-updates", "skip to content", "navigation"}:
            continue
        if any(noise in low for noise in ROCKSTAR_NOISE):
            continue
        out.append(line)
    return unique(out)


def find_rockstar_title(lines):
    for line in lines:
        low = line.casefold()
        if 30 <= len(line) <= 180 and "gta+" in low and low not in {"gta online", "gta online inhalts-updates"}:
            return line
    return ""


def isolate_rockstar_article(lines, title):
    start = 0
    for i, line in enumerate(lines):
        if line.casefold() == title.casefold():
            start = i + 1
            break
    body = []
    for line in lines[start:]:
        low = line.casefold()
        if any(marker in low for marker in ("more from us", "related articles", "comments", "newsletter", "sign up")):
            break
        if any(noise in low for noise in ROCKSTAR_NOISE):
            continue
        if len(line) >= 30:
            body.append(line)
    return unique(body)


def rockstar_candidates(title, body):
    # Artikelüberschriften und reine Navigations-/Teaserzeilen nicht als eigene Fakten behandeln.
    out = []
    title_low = clean(title).casefold()
    for x in body:
        x = clean(x)
        low = x.casefold()
        if not x or low == title_low:
            continue
        if len(x) < 35:
            continue
        if low in {
            'new chameleon paints, free bigness clothing, biker bonuses, and more',
            'new chameleon paints, free bigness clothing, biker bonuses, and more.',
        }:
            continue
        out.append(x)
    return unique(out)


def fact_concepts(fact):
    low = clean(fact).casefold()
    concepts = set()
    markers = {
        "horus": ("pegassi horus", "horus supercar"),
        "early_access": ("early access", "vorabzugang", "frühen zugang", "früher zugang"),
        "chameleon": ("chameleon", "chamäleon"),
        "bigness": ("bigness",),
        "cluckin_bell": ("cluckin' bell", "cluckin bell"),
        "scene_of_crime": ("scene of the crime", "tatort"),
        "biker": ("biker", "biker business", "biker-unternehmen"),
        "bike_service": ("bike service", "bike-service", "motorrad-service"),
        "nagasaki": ("nagasaki",),
        "monthly_500k": ("500.000", "500,000", "500k"),
        "cashcard": ("shark card", "cash card", "cashcard"),
        "vinewood_app": ("vinewood club app",),
        "games_library": ("games library",),
    }
    for concept, concept_markers in markers.items():
        if any(marker in low for marker in concept_markers):
            concepts.add(concept)
    return concepts


def filter_new_rockstar_facts(candidates, known_facts, known_concepts_set):
    """Return only genuinely NEW Rockstar information compared with Wednesday.

    Anything that belongs to a topic already reported Wednesday is blocked unless the
    Rockstar sentence explicitly describes a change, replacement, increase, reduction,
    or similar update. This is deliberately conservative: a false negative is preferable
    to posting the same weekly information twice.
    """
    known = [clean(x).casefold() for x in (known_facts or [])]
    known_concepts_set = set(known_concepts_set or set())
    out = []

    signal_words = (
        "gta+", "gta$", "2x", "3x", "4x", "5x", "6x", "free", "kostenlos",
        "reward", "belohn", "bonus", "discount", "rabatt", "vehicle", "supercar",
        "supersportwagen", "motorcycle", "motorrad", "early access", "vorabzugang",
        "paint", "livery", "clothing", "kleidung", "nagasaki", "biker",
        "bike service", "cluckin' bell", "scene of the crime", "shark card", "cash card",
    )

    change_words = (
        "changed", "change", "updated", "update", "adjusted", "adjustment",
        "increased", "increases", "decreased", "decreases", "reduced", "removed",
        "replaced", "replacement", "instead", "now pays", "now offers", "now available",
        "changed to", "from now on",
        "geändert", "änderung", "aktualisiert", "angepasst", "erhöht", "gesenkt",
        "reduziert", "entfernt", "ersetzt", "stattdessen", "nun", "ab jetzt",
    )

    def normalized_tokens(text):
        return set(re.findall(r"[a-zäöüß0-9+%-]{4,}", clean(text).casefold()))

    for fact in candidates:
        fact = clean(fact)
        key = fact.casefold()
        if not key or key in known:
            continue
        if not any(word in key for word in signal_words):
            continue

        concepts = fact_concepts(fact)
        explicit_change = any(word in key for word in change_words)

        # A Wednesday concept is considered known for the rest of the week.
        # A rewording is NOT a new Thursday fact.
        if concepts & known_concepts_set and not explicit_change:
            continue

        ft = normalized_tokens(fact)
        duplicate = False
        for old in known:
            ot = normalized_tokens(old)
            overlap = len(ft & ot) / max(1, len(ft))
            if len(ft) >= 5 and overlap >= 0.72:
                duplicate = True
                break
        if duplicate and not explicit_change:
            continue

        out.append(fact)

    return unique(out)


def classify_rockstar(facts):
    """Return only categories that are actually represented by the new facts."""
    categories = []
    blob = " ".join(facts).casefold()

    if any(x in blob for x in ("new", "brand-new", "brand new", "neu", "early access", "vorabzugang")):
        categories.append("🆕 **Neu**")
    if any(x in blob for x in ("changed", "change", "updated", "update", "adjusted", "geändert", "änderung", "angepasst")):
        categories.append("⚠️ **Änderung**")
    if any(x in blob for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad")):
        categories.append("🚗 **neue Fahrzeugmeldung**")
    if any(x in blob for x in ("bonus", "boni", "gta$", "2x", "3x", "payout", "rabatt", "discount")):
        categories.append("💰 **neuer Bonus**")
    if any(x in blob for x in ("free", "kostenlos", "reward", "belohn", "clothing", "kleidung", "paint", "livery")):
        categories.append("🎁 **neue Belohnung**")

    return categories


def month_key(today=None):
    return (today or datetime.now(VIENNA).date()).strftime("%Y-%m")


def is_first_friday(today=None):
    today = today or datetime.now(VIENNA).date()
    return today.weekday() == 4 and today.day <= 7


def make_monthly_agent_report():
    return "\n".join(
        [
            "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
            "",
            "🤫 **Psst … Lagebericht aus Los Santos.**",
            "",
            "**INFORMANTENBERICHT**",
            "",
            "*„Keine neuen Vorkommnisse zu melden. Die Lage in Los Santos bleibt unter Beobachtung. Ich halte weiterhin Augen und Ohren offen und melde mich, sobald sich etwas verändert.“*",
            "",
            "💬 *„Bis dahin heißt es: unauffällig bleiben und aufmerksam sein.“*",
        ]
    )


def translate_rockstar(text):
    result = clean(text)
    for pattern, repl in (
        (r"GTA\+ Members", "GTA+ Mitglieder"),
        (r"GTA\+ members", "GTA+ Mitglieder"),
        (r"one week of early access", "eine Woche frühen Zugang"),
        (r"brand-new", "brandneu"),
        (r"New Chameleon Paints", "Neue Chameleon-Lackierungen"),
        (r"Free Bigness Clothing", "kostenlose Bigness-Kleidung"),
        (r"Biker Bonuses", "Biker-Boni"),
        (r"and more", "und mehr"),
    ):
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def make_thursday_post(article, new_facts):
    cats = classify_rockstar(new_facts)
    lines = [
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**",
        "",
        "🤫 **Psst … eine neue Meldung ist eingetroffen.**",
        "Unser Informant hat Neuigkeiten aus Los Santos durchgegeben:",
        "",
        f"**{translate_rockstar(article['title'])}**",
    ]

    if cats:
        lines += ["", *cats]

    lines += [
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
    async with async_playwright() as playwright:
        browser, context = await new_context(playwright)
        try:
            page = await context.new_page()
            await page.goto(ROCKSTAR_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(5000)
            anchors = await page.locator("a[href*='/newswire/article/']").evaluate_all(
                "els => els.map(e => ({href:e.href,text:(e.innerText||e.textContent||'').trim()}))"
            )
            for card in anchors:
                url = clean(card.get("href", ""))
                card_text = clean(card.get("text", ""))
                blob = (card_text + " " + url).casefold()
                if "gta online" not in blob and "gta+" not in blob:
                    continue
                article_page = await context.new_page()
                try:
                    await article_page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await article_page.wait_for_timeout(5000)
                    article_text = await extract_visible_page(article_page)
                    lines = clean_rockstar_lines(article_text)
                    title = find_rockstar_title(lines) or card_text
                    if not title:
                        continue
                    return {"title": title, "url": url, "body": isolate_rockstar_article(lines, title)}
                finally:
                    await article_page.close()
            raise RuntimeError("Kein aktueller GTA-Online-Rockstar-Artikel gefunden.")
        finally:
            await context.close()
            await browser.close()


async def run_wednesday():
    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    weekly_url, weekly_text = await fetch_latest_igta_article()
    gta_plus_text, gta_plus_url = await fetch_igta_gta_plus()
    if not gta_plus_text:
        raise RuntimeError("GTA+: Der aktuelle GTA+-Artikel konnte nicht gelesen werden. Nichts wird gepostet.")
    data = make_wednesday_data(weekly_text, gta_plus_text)
    message_text = format_wednesday(data, weekly_url)
    messages = split_message(message_text)
    print(f"Mittwoch: {len(messages)} Nachricht(en), {len(message_text)} Zeichen")
    state = load_state()
    period_key = f"{data['period'][0]:%Y-%m-%d}_{data['period'][1]:%Y-%m-%d}"
    if not TEST_MODE and state.get("wednesday_period") == period_key:
        print("Mittwoch bereits veröffentlicht.")
        return data
    for message in messages:
        print("--- MITTWOCH ---\n" + message)
    post_to_discord(messages)
    if not TEST_MODE:
        state.update(
            {
                "wednesday_period": period_key,
                "wednesday_facts": wednesday_facts(data),
                "wednesday_concepts": sorted(known_concepts(data)),
                "wednesday_plus_url": gta_plus_url,
            }
        )
        save_state(state)
    return data


async def run_thursday(known_data=None):
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article = await fetch_rockstar_news()
    state = load_state()
    known_facts = wednesday_facts(known_data) if known_data is not None else state.get("wednesday_facts", [])
    known_set = known_concepts(known_data) if known_data is not None else set(state.get("wednesday_concepts", []))
    candidates = rockstar_candidates(article["title"], article["body"])
    new_facts = filter_new_rockstar_facts(candidates, known_facts, known_set)
    print(f"Rockstar: {len(candidates)} Kandidaten | {len(new_facts)} neu gegenüber Mittwoch")

    # Donnerstag veröffentlicht ausschließlich echte Änderungen gegenüber Mittwoch.
    # Kein Monatsbericht, kein Füllpost, keine Wiederholung bereits bekannter Informationen.
    if not new_facts:
        print("Donnerstag: Keine neuen relevanten Informationen. Kein Post.")
        return

    if not TEST_MODE and state.get("thursday_url") == article["url"]:
        print("Donnerstag: Artikel bereits veröffentlicht.")
        return

    post_to_discord(make_thursday_post(article, new_facts))
    if not TEST_MODE:
        state.update(
            {
                "thursday_url": article["url"],
                "thursday_facts": new_facts,
            }
        )
        save_state(state)


async def run_friday_monthly():
    print("\nLS-INSIDER – FREITAG / MONATSBERICHT")
    today = datetime.now(VIENNA).date()
    if not is_first_friday(today):
        print("Freitag: Kein Monatsbericht, da heute nicht der erste Freitag des Monats ist.")
        return

    state = load_state()
    month = month_key(today)
    if not TEST_MODE and state.get("monthly_agent_report_month") == month:
        print("Monatsbericht für diesen Monat bereits veröffentlicht.")
        return

    post_to_discord(make_monthly_agent_report())

    if not TEST_MODE:
        state["monthly_agent_report_month"] = month
        save_state(state)


# ------------------------ Selbsttest ------------------------

WED_FIXTURE = '''
This Week in GTA Online: September 10, 2026
Business Rivalries shifts into gear this week
Bonuses and Rewards
2X GTA$ and RP on Biker Clubhouse Contracts, MC Work, and MC Challenges
2X GTA$ on Bike Service Missions (6X for GTA+ Members)
2X GTA$ on Street Dealer Sales
3X GTA$ and RP on Community Mission Series
2X GTA$ and RP on Featured Series: Hasta La Vista and Every Bullet Counts
Free Penaud La Coureuse and HSW Upgrade
Complete at least one Weekly Challenge over the two weeks to claim a free Penaud La Coureuse.
Business Battle clothing rewards: Six Figure Tee, Ride or Die Tee, Red and White Ammu-Nation Cap, or Bourgeoix Tee
Discounts
Grapeseed MC Clubhouse - Free
Karin S95 - 70% off
Western Rampant Rocket - 30% off
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
Earn GTA$1,000,000 selling all kinds of Product to bank an extra GTA$1,000,000 and the Junk Tracksuit
LS Car Meet Prize Ride:
Place Top 4 in the LS Car Meet Series to win the Pfister Neon
Premium Race:
Eight Figure Bonus
HSW Time Trial:
Del Perro Beach to Murietta Heights
Time Trial:
Sawmill
Other Activities
# Rotating Content
## The Kortz Center Heist Primary Targets:
I Hear Voices
I, Fruit
Until Death
## FIB Priority File:
The Black Box File
## Salvage Yard Robbery Targets:
The Gangbanger Robbery: Dinka Blista Kanjo (Compact)'''

GTA_FIXTURE = '''
GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar
GTA+ members can get one week of early access to the new Pegassi Horus supercar.
The Pegassi Horus is wrapped in the Velvet Iris livery and can be claimed free at the Vinewood Car Club.
Free Cyan/Red Flip Chameleon Paints and matching Wheel Paint.
Both Cyan/Red Flip Chameleon options are free through Vehicle Workshops.
Free Bigness Patchwork clothing: Bigness Patchwork Revere Collar shirt and Bigness Patchwork Jeans.
The Cluckin' Bell Farm Raid is included in this month's bonuses, with the first completion of its finale each week paying 2X GTA$.
From September 10 through October 7, GTA+ members receive 2X GTA$ for their first weekly completion of Scene of the Crime.
GTA+ members get 60% off Biker businesses and upgrades.
Cocaine production is doubled for GTA+ members.
3X GTA$ and RP on Bike Service.
50% off all Nagasaki motorcycles: Shinobi, Shotaro, Stryder, Chimera and BF400.
GTA$500,000 deposited monthly into Maze Bank.
Special GTA+ Shark Cards include a 15% GTA$ bonus.
The Vinewood Club App is included with GTA+.
The GTA+ Games Library is included.
'''

ROCK_FIXTURE = '''
Newswire
GTA Online
Inhalts-Updates
GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar
Members at GTA+ receive one week of early access to the new Pegassi Horus through the Vinewood Car Club.
Every week, get 2X GTA$ on the first completion of Scene of the Crime in the Cluckin' Bell Farm Raid.
Join former LSPD officer Vincent Effenburger as he takes on corrupt cops and cartel gangs.
If you complete Scene of the Crime for the first time in the week, you receive 2X GTA$.
New Chameleon Paints, Free Bigness Clothing, Biker Bonuses, and More
The Pegassi Horus is wrapped in Velvet Iris and can be collected for free.
'''


def self_test():
    data = make_wednesday_data(WED_FIXTURE, GTA_FIXTURE)
    assert data["headline"] == "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"
    assert len(data["bonuses"]) == 5
    assert len(data["vehicles"]) == 5
    assert [x[0] for x in data["discounts"]] == ["KOSTENLOS", "70% RABATT", "30% RABATT"]
    assert len(data["gta_plus"]) == 12, data["gta_plus"]
    assert any("500.000" in x for x in data["gta_plus"])
    assert any("Nagasaki" in x for x in data["gta_plus"])
    assert len(data["challenges"]) == 5
    assert len(data["rotating"]) == 3
    post = format_wednesday(data, "https://example.invalid/article")
    messages = split_message(post)
    assert all(len(message) <= DISCORD_LIMIT for message in messages)
    assert "⭐ **DAS PLUS FÜR EUCH**" in post
    assert "DIESE WOCHE AKTUELL IN LOS SANTOS" in post
    assert "0 Comments" not in post
    assert "More GTA Online News" not in post
    assert "Games, GTA VI" not in post
    assert "Gun Van Inventory" not in post
    assert "Tactical SMG" not in post
    assert "Double Barrel Shotgun" not in post
    assert any(name == "Time Trial" and text == "Sawmill" for name, text in data["challenges"])

    known = known_concepts(data)
    candidates = rockstar_candidates(
        "GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar",
        clean_rockstar_lines(ROCK_FIXTURE),
    )
    assert filter_new_rockstar_facts(candidates, wednesday_facts(data), known) == []

    exact_live_like = [
        "GTA+ members can get one week of early access to the new Pegassi Horus supercar.",
        "From September 10 through October 7, GTA+ members receive 2X GTA$ for their first weekly completion of Scene of the Crime.",
        "GTA+ members get 60% off Biker businesses and upgrades.",
        "GTA$500,000 deposited monthly into Maze Bank.",
        "Special GTA+ Shark Cards include a 15% GTA$ bonus.",
    ]
    assert filter_new_rockstar_facts(exact_live_like, wednesday_facts(data), known) == []

    live_like = [
        "Members at GTA+ receive one week of early access to the new Pegassi Horus through the Vinewood Car Club.",
        "Every week, get 2X GTA$ on the first completion of Scene of the Crime in the Cluckin' Bell Farm Raid.",
        "Join former LSPD officer Vincent Effenburger as he takes on corrupt cops and cartel gangs.",
    ]
    assert filter_new_rockstar_facts(live_like, wednesday_facts(data), known) == []

    new = filter_new_rockstar_facts(
        ["A brand-new Vapid Testster vehicle is now available."],
        wednesday_facts(data),
        known,
    )
    assert new

    monthly = make_monthly_agent_report()
    assert "LS-INSIDER – GEHEIMBERICHT" in monthly
    assert "Psst … Lagebericht aus Los Santos." in monthly
    assert "Keine neuen Vorkommnisse zu melden." in monthly
    assert is_first_friday(date(2026, 9, 4))
    assert not is_first_friday(date(2026, 9, 11))

    print("SELF-TEST OK")
    print(f"Mittwoch: {len(messages)} Nachricht(en), {len(post)} Zeichen")
    print(f"Boni: {len(data['bonuses'])}")
    print(f"Fahrzeuggruppen: {len(data['vehicles'])}")
    print(f"Rabattgruppen: {len(data['discounts'])}")
    print(f"Geschenke: {len(data['gifts'])}")
    print(f"GTA+: {len(data['gta_plus'])}")
    print(f"Challenges: {len(data['challenges'])}")
    print(f"Rotierende Inhalte: {len(data['rotating'])}")


async def main():
    self_test()
    now = datetime.now(VIENNA)
    print("========================================")
    print("LS-INSIDER")
    print(f"Wiener Zeit: {now:%d.%m.%Y %H:%M:%S}")
    print(f"TEST_MODE={TEST_MODE}")
    print("========================================")
    event = os.getenv("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch" and TEST_MODE:
        data = await run_wednesday()
        await run_thursday(data)
    elif now.weekday() == 2:
        await run_wednesday()
    elif now.weekday() == 3:
        await run_thursday()
    elif now.weekday() == 4:
        await run_friday_monthly()
    else:
        print("Heute ist kein LS-Insider-Veröffentlichungstag.")


if __name__ == "__main__":
    asyncio.run(main())
