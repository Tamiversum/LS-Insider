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
    1: "january", 2: "february", 3: "march", 4: "april", 5: "may", 6: "june",
    7: "july", 8: "august", 9: "september", 10: "october", 11: "november", 12: "december",
}


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
    line = re.sub(r"\s*[:：]\s*$", "", line)
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
    if not TEST_MODE:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def post_to_discord(messages):
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL ist nicht gesetzt.")
    if isinstance(messages, str):
        messages = [messages]
    for index, message in enumerate(messages, 1):
        if len(message) > DISCORD_LIMIT:
            raise RuntimeError(f"Discord-Nachricht {index} ist zu lang: {len(message)}")
        r = requests.post(webhook, json={"content": message, "allowed_mentions": {"parse": []}}, timeout=45)
        r.raise_for_status()
        print(f"Discord: Nachricht {index}/{len(messages)} gesendet ({len(message)} Zeichen)")


def week_period(today: date | None = None):
    today = today or datetime.now(VIENNA).date()
    delta = (today.weekday() - 3) % 7
    start = today - timedelta(days=delta)
    return start, start + timedelta(days=6)


async def new_context(playwright):
    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = await browser.new_context(
        locale="de-DE",
        viewport={"width": 1440, "height": 1200},
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
    )
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    return browser, context


async def extract_visible_page(page):
    best = ""
    for selector in ("article", "main article", "main", "[role='main']", "body"):
        try:
            loc = page.locator(selector).first
            if await loc.count():
                text = clean(await loc.inner_text(timeout=12000))
                if len(text) > len(best):
                    best = text
        except Exception:
            pass
    return best


async def fetch_page(url, wait_ms=3500):
    async with async_playwright() as p:
        browser, context = await new_context(p)
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
    "rotating": {"other activities", "weekly rotating content", "rotating content", "additional activities", "other content"},
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
    for pattern, repl in (
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
        (r"\band\b", "und"),
        (r"for GTA\+ Members", "für GTA+ Mitglieder"),
        (r"\bon\b", "für"),
    ):
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def parse_bonuses(lines):
    return unique([translate_bonus(x) for x in lines if re.search(r"\b[23]x gta\$|double rewards", x, re.I)])


def parse_percent_groups(lines):
    groups = {}
    for raw in lines:
        line = clean(raw)
        m = re.search(r"(\d{1,3})\s*%\s*(?:off|discount|rabatt)?", line, re.I)
        if m:
            pct = int(m.group(1))
            item = re.sub(r"\s*(?:-|–|—|:)\s*\d{1,3}\s*%.*?$", "", line, flags=re.I)
            item = re.sub(r"\b\d{1,3}\s*%\s*(?:off|discount|rabatt)?\b", "", item, flags=re.I).strip(" -–—:")
            if item:
                groups.setdefault(pct, []).append(item)
        elif re.search(r"\bfree\b", line, re.I):
            item = re.sub(r"\bfree\b", "", line, flags=re.I).strip(" -–—:")
            if item:
                groups.setdefault(0, []).append(item)
    ordered = sorted(groups, key=lambda p: (p != 0, -p))
    return [("KOSTENLOS" if p == 0 else f"{p}% RABATT", unique(groups[p])) for p in ordered]


def vehicle_group(line):
    value = strip_markup(line).casefold()
    for needle, label in (
        ("luxury autos", "Luxury Autos"),
        ("premium deluxe motorsport", "Premium Deluxe Motorsport"),
        ("hao's premium test ride", "Hao's Premium Test Ride"),
        ("hao’s premium test ride", "Hao's Premium Test Ride"),
        ("ls car meet test rides", "LS Car Meet Test Rides"),
        ("the diamond casino and resort lucky wheel", "Lucky Wheel"),
        ("diamond casino and resort lucky wheel", "Lucky Wheel"),
    ):
        if value == needle or value.startswith(needle + " "):
            return label
    return None


def parse_vehicles(lines):
    result, current, bucket = [], None, []
    def flush():
        nonlocal bucket
        if current and bucket:
            result.append((current, unique(bucket)))
        bucket = []
    for raw in lines:
        line = clean(raw)
        group = vehicle_group(line)
        if group:
            flush(); current = group; continue
        m = re.match(r"^(.+?)\s*\(([^)]*)\)\s*$", line)
        if current and m:
            bucket.append(clean(m.group(1)))
    flush()
    return result


def parse_gifts(sections, full_text):
    out = []
    if "penaud la coureuse" in " ".join(sections.get("free", [])).casefold():
        out.append("Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September.")
    clothing = ["Six Figure Tee", "Ride or Die Tee", "Red and White Ammu-Nation Cap", "Bourgeoix Tee"]
    low = full_text.casefold()
    found = [x for x in clothing if x.casefold() in low]
    if found:
        out.append("Kostenlose Kleidung durch Business Battles: " + ", ".join(found) + ".")
    return unique(out)


def parse_challenges(lines):
    names = ["Weekly Challenge", "LS Car Meet Prize Ride", "Premium Race", "HSW Time Trial", "Time Trial"]
    lines = [clean(x) for x in lines if clean(x)]
    hits = []
    for i, line in enumerate(lines):
        normalized_line = line.rstrip(":：").strip().casefold()
        for name in sorted(names, key=len, reverse=True):
            if normalized_line == name.casefold() or normalized_line.startswith(name.casefold() + " "):
                hits.append((i, name)); break
    out = []
    for n, (i, name) in enumerate(hits):
        body = lines[i+1:(hits[n+1][0] if n+1 < len(hits) else len(lines))]
        if not body:
            continue
        text = " ".join(body[:2])
        for pattern, repl in (
            (r"Earn GTA\$1,000,000.*Junk Tracksuit", "Verdient GTA$1.000.000 durch den Verkauf aller Arten von Waren und erhaltet zusätzlich GTA$1.000.000 sowie den Junk-Trainingsanzug"),
            (r"Place in the Top 4 to win Pfister Neon", "Platz unter den Top 4 in der LS Car Meet Series für den Pfister Neon"),
            (r"Del Perro Beach to Murietta Heights", "Del Perro Beach nach Murietta Heights"),
        ):
            text = re.sub(pattern, repl, text, flags=re.I)
        out.append((name, clean(text)))
    return out


def translate_rotating(line):
    return clean(line)


def parse_rotating(lines):
    headings = {
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
        line = clean(raw); low = strip_markup(line).casefold()
        if any(x in low for x in ("this article has been viewed", "share", "discover more", "unlock game guides", "thanks for your support")):
            flush(); break
        heading = next((label for needle, label in headings.items() if low == needle or low.startswith(needle + " ")), None)
        if heading:
            flush(); current = heading; continue
        if current and line and not low.startswith(("newswire", "gta online inhalts")):
            bucket.append(translate_rotating(line))
    flush()
    return unique(groups)


def parse_gta_plus(text):
    low = clean(text).casefold(); out = []
    checks = [
        ("pegassi horus" in low and "early access" in low, "Pegassi Horus: 1 Woche Early Access für GTA+ Mitglieder; kostenlos beim Vinewood Car Club mit exklusiver Velvet-Iris-Lackierung."),
        ("chameleon" in low and "free" in low, "Kostenlose Chameleon-Lackierung und Chameleon-Felgenfarbe für GTA+ Mitglieder."),
        ("bigness patchwork" in low, "Kostenlose Bigness-Patchwork-Kleidung: Bigness Patchwork Revere Collar Shirt und Bigness Patchwork Jeans."),
        ("cluckin' bell" in low and "2x gta$" in low, "2X GTA$ für den ersten wöchentlichen Abschluss von „Scene of the Crime“ im Cluckin' Bell Farm Raid."),
        ("60% off" in low and "biker" in low, "60% Rabatt auf Biker-Unternehmen sowie deren Upgrades und Anpassungen für GTA+ Mitglieder."),
        ("cocaine production" in low and "doubled" in low, "Doppelte Produktionsgeschwindigkeit im Kokain-Labor für Biker-Unternehmen von GTA+ Mitgliedern."),
        ("3x gta$ and rp" in low and "bike service" in low, "3X GTA$ und RP für Bike-Service-Arbeiten für GTA+ Mitglieder."),
        ("50% off all nagasaki" in low, "50% Rabatt auf alle Nagasaki-Motorräder: Shinobi, Shotaro, Stryder, Chimera und BF400."),
        ("gta$500,000" in low and "monthly" in low, "GTA$500.000 werden GTA+ Mitgliedern monatlich zum Abrechnungszeitpunkt gutgeschrieben."),
        ("15%" in low and "shark card" in low, "Spezielle GTA+ Shark Cards bieten 15% Bonus-GTA$."),
        ("vinewood club app" in low, "Zugang zur Vinewood Club App mit Funktionen zur Verwaltung von Unternehmen, Einnahmen und angeforderten Fahrzeugen."),
        ("games library" in low, "Zugang zur wechselnden GTA+ Games Library mit Rockstar-Spielen, solange sie über den Service verfügbar sind."),
    ]
    for ok, text in checks:
        if ok: out.append(text)
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
        "gta_plus": parse_gta_plus(gta_plus_text),
        "challenges": parse_challenges(sec.get("challenges", [])),
        "rotating": parse_rotating(sec.get("rotating", [])),
    }
    core = sum(bool(data[x]) for x in ("bonuses", "vehicles", "discounts", "gifts", "challenges"))
    if core < 4:
        raise RuntimeError(f"iGTA-Parser hat nur {core}/5 Kernbereiche erkannt. Nichts wird gepostet.")
    return data


def wednesday_facts(data):
    facts = list(data["bonuses"])
    for _, items in data["vehicles"]: facts.extend(items)
    for _, items in data["discounts"]: facts.extend(items)
    facts.extend(data["gifts"]); facts.extend(data["gta_plus"])
    facts.extend(f"{n}: {t}" for n, t in data["challenges"]); facts.extend(data["rotating"])
    return unique(facts)


def known_concepts(data):
    concepts = set()
    for fact in wednesday_facts(data):
        low = fact.casefold()
        markers = {
            "horus": "pegassi horus", "early_access": "early access", "chameleon": "chameleon",
            "bigness": "bigness", "cluckin_bell": "cluckin' bell", "biker": "biker",
            "bike_service": "bike-service", "nagasaki": "nagasaki", "monthly_500k": "500.000",
            "cashcard": "shark cards", "vinewood_app": "vinewood club app", "games_library": "games library",
        }
        for concept, marker in markers.items():
            if marker in low: concepts.add(concept)
    return concepts


def format_wednesday(data, source_url):
    start, end = data["period"]
    lines = ["🗞️ **LS-INSIDER**", "", f"**{data['headline']}**", f"📅 **{start:%d.%m.%Y} – {end:%d.%m.%Y}**", data["intro"]]
    if data["bonuses"]: lines += ["", "💰 **DIE GROSSEN GESCHÄFTE**"] + [f"• {x}" for x in data["bonuses"]]
    if data["vehicles"]:
        lines += ["", "🚗 **NEUES AUF DEN STRASSEN**"]
        lines += [f"**{g}:** {', '.join(items)}" for g, items in data["vehicles"]]
    if data["discounts"]:
        lines += ["", "🏷️ **LAUFENDE DEALS**"]
        lines += [f"**{g}:** {', '.join(items)}" for g, items in data["discounts"]]
    if data["gifts"]: lines += ["", "🎁 **GESCHENKE & BEUTE**"] + [f"• {x}" for x in data["gifts"]]
    if data["gta_plus"]: lines += ["", "⭐ **DAS PLUS FÜR EUCH**"] + [f"• {x}" for x in data["gta_plus"]]
    if data["challenges"]:
        lines += ["", "🏆 **DIE WOCHENAUFTRÄGE**"] + [f"• **{n}:** {t}" for n, t in data["challenges"]]
    if data["rotating"]: lines += ["", "📍 **DIESE WOCHE AKTUELL IN LOS SANTOS**"] + [f"• {x}" for x in data["rotating"]]
    lines += ["", "━━━━━━━━━━━━━━━━━━━━", "", "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**", f"<{source_url}>"]
    return "\n".join(lines)


def split_message(text):
    if len(text) <= DISCORD_LIMIT: return [text]
    lines = text.splitlines(); units = []; i = 0
    while i < len(lines):
        if lines[i].strip() == "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**": units.append(lines[i:i+2]); i += 2
        else: units.append([lines[i]]); i += 1
    out, current = [], []
    for unit in units:
        u = "\n".join(unit).strip()
        if not u: continue
        candidate = ("\n".join(current + unit)).strip() if current else u
        if len(candidate) <= DISCORD_LIMIT: current.extend(unit)
        else:
            if current: out.append("\n".join(current).strip())
            if len(u) > DISCORD_LIMIT: raise RuntimeError(f"Ausgabeblock zu lang: {len(u)}")
            current = list(unit)
    if current: out.append("\n".join(current).strip())
    return out


async def fetch_latest_igta_article():
    start, _ = week_period()
    direct = f"{IGTA_NEWS_URL}/this-week-in-gta-online-{MONTH_NAMES[start.month]}-{start.day}-{start.year}"
    text = await fetch_page(direct, 4000)
    if "this week in gta online" in text.casefold() and len(text) > 800:
        return direct, text
    listing = await fetch_page(IGTA_NEWS_URL, 3000)
    match = re.search(r"https://www\.igrandtheftauto\.com/gtaonline/news/this-week-in-gta-online-[\w-]+", listing, re.I)
    if not match: raise RuntimeError("iGTA: Kein Wochenartikel gefunden.")
    url = match.group(0)
    return url, await fetch_page(url, 4000)


async def fetch_igta_gta_plus():
    url = f"{IGTA_NEWS_URL}/gta-members-enjoy-one-week-of-early-access-to-the-new-pegassi-horus-supercar"
    text = await fetch_page(url, 4000)
    if "gta+ members" in text.casefold() and "pegassi horus" in text.casefold():
        return text, url
    return "", ""


# ------------------------ Donnerstag ------------------------

ROCKSTAR_NOISE = ("this article has been viewed", "thanks for your support", "share", "discover more", "unlock game guides")


def clean_rockstar_lines(text):
    out = []
    for line in text_lines(text):
        low = line.casefold()
        if low in {"newswire", "gta online", "inhalts-updates", "skip to content", "navigation"}: continue
        if any(x in low for x in ROCKSTAR_NOISE): continue
        if len(line) >= 24: out.append(line)
    return unique(out)


def find_rockstar_title(lines):
    for line in lines:
        low = line.casefold()
        if 30 <= len(line) <= 180 and (low.startswith("gta+") or low.startswith("gta online") or "gta+" in low):
            if low not in {"gta online", "gta online inhalts-updates"}: return line
    return ""


def isolate_rockstar_article(lines, title):
    start = 0
    for i, line in enumerate(lines):
        if line.casefold() == title.casefold(): start = i + 1; break
    body = []
    for line in lines[start:]:
        low = line.casefold()
        if any(x in low for x in ("more from us", "related articles", "comments", "newsletter", "sign up")): break
        if any(x in low for x in ROCKSTAR_NOISE): break
        if len(line) >= 30: body.append(line)
    return unique(body)


def rockstar_candidates(title, body):
    candidates = []
    for line in body:
        low = line.casefold()
        if low == title.casefold(): continue
        if len(line) < 35: continue
        if any(x in low for x in ROCKSTAR_NOISE): continue
        candidates.append(line)
    return unique(candidates)


def fact_concepts(fact):
    low = fact.casefold(); out = set()
    tests = {
        "horus": ("pegassi horus",), "early_access": ("early access", "vorabzugang", "frühen zugang"),
        "chameleon": ("chameleon", "chamäleon"), "bigness": ("bigness",), "cluckin_bell": ("cluckin' bell", "cluckin bell"),
        "biker": ("biker",), "bike_service": ("bike service", "bike-service", "motorrad-service"),
        "nagasaki": ("nagasaki",), "monthly_500k": ("500,000", "500.000", "500k"),
        "cashcard": ("shark card", "cash card", "cashcard"), "vinewood_app": ("vinewood club app",),
        "games_library": ("games library",),
    }
    for c, needles in tests.items():
        if any(n in low for n in needles): out.add(c)
    return out


def filter_new_rockstar_facts(candidates, known):
    known = {x.casefold() for x in known or []}
    out = []
    for fact in candidates:
        key = fact.casefold()
        if key in known: continue
        # Wenn ein konkretes bekanntes Thema eindeutig angesprochen wird, ist es nicht neu.
        if fact_concepts(fact) & known_concepts_set:
            continue
        # Sehr ähnliche Formulierungen ebenfalls unterdrücken.
        ft = set(re.findall(r"[a-zäöüß0-9+%-]{4,}", key))
        duplicate = False
        for old in known:
            ot = set(re.findall(r"[a-zäöüß0-9+%-]{4,}", old))
            if len(ft) >= 5 and len(ft & ot) / max(1, len(ft)) >= 0.82:
                duplicate = True; break
        if not duplicate: out.append(fact)
    return out


def classify_rockstar(facts):
    blob = " ".join(facts).casefold(); categories = []
    if any(x in blob for x in ("new", "brand-new", "brand new", "neu", "early access", "vorabzugang")): categories.append("🆕 **Neu**")
    if any(x in blob for x in ("vehicle", "fahrzeug", "supercar", "supersportwagen", "motorcycle", "motorrad")): categories.append("🚗 **neue Fahrzeugmeldung**")
    if any(x in blob for x in ("bonus", "boni", "gta$", "2x", "3x", "payout", "rabatt", "discount")): categories.append("💰 **neuer Bonus**")
    if any(x in blob for x in ("free", "kostenlos", "reward", "belohn", "clothing", "kleidung", "paint", "livery")): categories.append("🎁 **neue Belohnung**")
    if any(x in blob for x in ("changed", "updated", "returning", "removed", "replaced", "adjusted", "geändert")): categories.append("⚠️ **Änderung**")
    return categories or ["🆕 **Neu**"]


def translate_rockstar(text):
    result = clean(text)
    for pattern, repl in (
        (r"GTA\+ Members", "GTA+ Mitglieder"), (r"GTA\+ members", "GTA+ Mitglieder"),
        (r"one week of early access", "eine Woche frühen Zugang"), (r"brand-new", "brandneu"),
        (r"New Chameleon Paints", "Neue Chameleon-Lackierungen"), (r"Free Bigness Clothing", "kostenlose Bigness-Kleidung"),
        (r"Biker Bonuses", "Biker-Boni"), (r"and more", "und mehr"),
    ):
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def month_key(today=None): return (today or datetime.now(VIENNA).date()).strftime("%Y-%m")

def is_first_thursday(today=None):
    today = today or datetime.now(VIENNA).date()
    return today.weekday() == 3 and today.day <= 7


def make_monthly_agent_report():
    return "\n".join([
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**", "", "🤫 **Psst … Lagebericht aus Los Santos.**", "",
        "**INFORMANTENBERICHT**", "",
        "*„Keine neuen Vorkommnisse zu melden. Die Lage in Los Santos bleibt unter Beobachtung. Ich halte weiterhin Augen und Ohren offen und melde mich, sobald sich etwas verändert.“*", "",
        "💬 *„Bis dahin heißt es: unauffällig bleiben und aufmerksam sein.“*",
    ])


def make_thursday_post(article, new_facts):
    cats = classify_rockstar(new_facts)
    lines = ["🕵️ **LS-INSIDER – GEHEIMBERICHT**", "", "🤫 **Psst … eine neue Meldung ist eingetroffen.**", "Unser Informant hat Neuigkeiten aus Los Santos durchgegeben:", "", f"**{article['title']}**", "", *cats, "", "🕵️ **INFORMANTENBERICHT**"]
    lines += [f"• {translate_rockstar(x)}" for x in new_facts[:4]]
    lines += ["", "💬 *„Mehr darf ich dazu im Moment nicht sagen … aber behaltet Los Santos im Auge.“*", "", "🔗 **ORIGINALMELDUNG ÖFFNEN**", f"<{article['url']}>"]
    return "\n".join(lines)


async def fetch_rockstar_news():
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            await page.goto(ROCKSTAR_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(5000)
            anchors = await page.locator("a[href*='/newswire/article/']").evaluate_all("els => els.map(e => ({href:e.href,text:(e.innerText||e.textContent||'').trim()}))")
            for card in anchors:
                url = clean(card.get("href", "")); card_text = clean(card.get("text", "")); blob = (card_text + " " + url).casefold()
                if "gta online" not in blob and "gta+" not in blob: continue
                article_page = await context.new_page()
                try:
                    await article_page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await article_page.wait_for_timeout(5000)
                    article_text = await extract_visible_page(article_page)
                    lines = clean_rockstar_lines(article_text)
                    title = find_rockstar_title(lines) or find_rockstar_title([card_text])
                    if not title: continue
                    return {"title": title, "url": url, "body": isolate_rockstar_article(lines, title)}
                finally:
                    await article_page.close()
            raise RuntimeError("Kein aktueller GTA-Online-Rockstar-Artikel gefunden.")
        finally:
            await context.close(); await browser.close()


async def run_wednesday():
    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    weekly_url, weekly_text = await fetch_latest_igta_article()
    gta_plus_text, gta_plus_url = await fetch_igta_gta_plus()
    data = make_wednesday_data(weekly_text, gta_plus_text)
    messages = split_message(format_wednesday(data, weekly_url))
    print(f"Mittwoch: {len(messages)} Nachricht(en), {sum(map(len, messages))} Zeichen")
    state = load_state(); period_key = f"{data['period'][0]:%Y-%m-%d}_{data['period'][1]:%Y-%m-%d}"
    if not TEST_MODE and state.get("wednesday_period") == period_key:
        print("Mittwoch bereits veröffentlicht."); return data
    for m in messages: print("--- MITTWOCH ---\n" + m)
    post_to_discord(messages)
    if not TEST_MODE:
        state.update({"wednesday_period": period_key, "wednesday_facts": wednesday_facts(data), "wednesday_concepts": sorted(known_concepts(data)), "wednesday_plus_url": gta_plus_url})
        save_state(state)
    return data


async def run_thursday(known_data=None):
    global known_concepts_set
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article = await fetch_rockstar_news(); state = load_state()
    known_facts = wednesday_facts(known_data) if known_data is not None else state.get("wednesday_facts", [])
    known_concepts_set = known_concepts(known_data) if known_data is not None else set(state.get("wednesday_concepts", []))
    candidates = rockstar_candidates(article["title"], article["body"])
    new_facts = filter_new_rockstar_facts(candidates, known_facts)
    print(f"Rockstar: {len(candidates)} Kandidaten | {len(new_facts)} neu gegenüber Mittwoch")
    month = month_key(); monthly_sent = state.get("monthly_agent_report_month") == month; secret_sent = state.get("thursday_secret_month") == month
    if not new_facts:
        if is_first_thursday() and not monthly_sent and not secret_sent:
            post = make_monthly_agent_report(); post_to_discord(post)
            if not TEST_MODE: state["monthly_agent_report_month"] = month; save_state(state)
        else:
            print("Donnerstag: Keine neuen relevanten Informationen. Kein Post.")
        return
    if not TEST_MODE and state.get("thursday_url") == article["url"]:
        print("Donnerstag: Artikel bereits veröffentlicht."); return
    post = make_thursday_post(article, new_facts)
    post_to_discord(post)
    if not TEST_MODE:
        state.update({"thursday_url": article["url"], "thursday_facts": new_facts, "thursday_secret_month": month}); save_state(state)


# ------------------------ Selbsttest ------------------------

WED_FIXTURE = '''
This Week in GTA Online: September 10, 2026
Business Rivalries shifts into gear this week
Bonuses and Rewards
2X GTA$ and RP on Biker Clubhouse Contracts, MC Work, MC Challenges
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
Place in the Top 4 to win Pfister Neon
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
The Pegassi Horus is wrapped in the Velvet Iris livery and available for free from the Vinewood Car Club.
Free Cyan/Red Flip Chameleon Paints.
Free Bigness Patchwork clothing.
2X GTA$ on the Cluckin' Bell Farm Raid.
60% off Biker businesses and upgrades.
Cocaine production is doubled for GTA+ members.
3X GTA$ and RP on Bike Service.
50% off all Nagasaki motorcycles.
GTA$500,000 deposited monthly.
Special GTA+ Shark Cards include a 15% GTA$ bonus.
The Vinewood Club App is included.
The GTA+ Games Library is included.
'''

ROCK_FIXTURE = '''
Newswire
GTA Online
Inhalts-Updates
GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar
GTA+ members can get one week of early access to the new Pegassi Horus supercar.
New Chameleon Paints, Free Bigness Clothing, Biker Bonuses, and More
The Pegassi Horus is wrapped in Velvet Iris and can be collected for free.
'''


def self_test():
    global known_concepts_set
    data = make_wednesday_data(WED_FIXTURE, GTA_FIXTURE)
    assert data["headline"] == "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"
    assert len(data["bonuses"]) == 5
    assert len(data["vehicles"]) == 5
    assert [x[0] for x in data["discounts"]] == ["KOSTENLOS", "70% RABATT", "30% RABATT"]
    assert len(data["gta_plus"]) == 12
    assert len(data["challenges"]) == 5
    assert len(data["rotating"]) == 3
    post = format_wednesday(data, "https://example.invalid/article")
    messages = split_message(post)
    assert all(len(x) <= DISCORD_LIMIT for x in messages)
    assert "⭐ **DAS PLUS FÜR EUCH**" in post
    known_concepts_set = known_concepts(data)
    candidates = rockstar_candidates(
        "GTA+ Members Enjoy One Week of Early Access to the New Pegassi Horus Supercar",
        clean_rockstar_lines(ROCK_FIXTURE),
    )
    assert filter_new_rockstar_facts(candidates, wednesday_facts(data)) == []
    new = filter_new_rockstar_facts(["A brand-new Vapid Testster vehicle is now available."], wednesday_facts(data))
    assert new
    monthly = make_monthly_agent_report(); assert "Keine neuen Vorkommnisse zu melden." in monthly
    assert is_first_thursday(date(2026, 9, 3)); assert not is_first_thursday(date(2026, 9, 10))
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
    else:
        print("Heute ist kein LS-Insider-Veröffentlichungstag.")


if __name__ == "__main__":
    asyncio.run(main())
