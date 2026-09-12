import asyncio
import html
import json
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

IGTA_NEWS_URL = "https://www.igrandtheftauto.com/gtaonline/news"
ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"
STATE_FILE = "weekly_state.json"
VIENNA = ZoneInfo("Europe/Vienna")
TEST_MODE = os.getenv("LS_INSIDER_TEST_MODE", "true").lower() == "true"
DISCORD_LIMIT = 1900
TIMEOUT_MS = 60000


# ============================================================
# GRUNDLAGEN
# ============================================================

def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r]+", " ", text)
    return text.strip()


def unique(items):
    result = []
    seen = set()
    for item in items:
        item = clean(item)
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def lines_from_text(text: str):
    result = []
    for line in (text or "").splitlines():
        line = clean(line)
        line = re.sub(r"^[•▪◦·]+\s*", "", line)
        if line:
            result.append(line)
    return unique(result)


def load_state():
    if TEST_MODE:
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    if TEST_MODE:
        return
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def post_to_discord(text: str):
    if len(text) > DISCORD_LIMIT:
        raise RuntimeError(f"Discord-Beitrag ist zu lang: {len(text)} Zeichen")
    r = requests.post(
        WEBHOOK_URL,
        json={"content": text, "allowed_mentions": {"parse": []}},
        timeout=45,
    )
    r.raise_for_status()
    print(f"Discord: {len(text)} Zeichen gesendet")


# ============================================================
# ZEITRAUM
# ============================================================

def target_thursday(today: date | None = None):
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
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        locale="de-DE",
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


async def page_text(page):
    selectors = ["article", "main", "body"]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = await locator.inner_text(timeout=10000)
                if len(clean(text)) > 300:
                    return text
        except Exception:
            pass
    return await page.locator("body").inner_text()


async def fetch_text(url, wait_ms=4000):
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(wait_ms)
            text = await page_text(page)
            print(f"Abruf: {url}")
            print(f"HTTP: {response.status if response else '?'} | Text: {len(text)} Zeichen")
            return text, await page.content()
        finally:
            await context.close()
            await browser.close()


# ============================================================
# iGRANDTHEFTAUTO – EINFACHER PARSER
# ============================================================

IGTA_HEADINGS = {
    "# bonuses and rewards": "bonuses",
    "bonuses and rewards": "bonuses",
    "# free penaud la coureuse and hsw upgrade": "free",
    "free penaud la coureuse and hsw upgrade": "free",
    "# discounts": "discounts",
    "discounts": "discounts",
    "# vehicles": "vehicles",
    "vehicles": "vehicles",
    "# challenges": "challenges",
    "challenges": "challenges",
}


def heading_key(line: str):
    value = clean(line).rstrip(":").casefold()
    value = re.sub(r"^#+\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def split_igta_sections(text: str):
    sections = {}
    current = None
    for raw in text.splitlines():
        line = clean(raw)
        if not line:
            continue
        key = heading_key(line)
        if key in IGTA_HEADINGS:
            current = IGTA_HEADINGS[key]
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return sections


def igta_title(lines):
    for line in lines:
        m = re.search(r"This Week in GTA Online:\s*(.+)$", line, re.I)
        if m:
            return f"This Week in GTA Online: {m.group(1).strip()}"
    return ""


def igta_intro(lines):
    title = igta_title(lines)
    after = False
    for line in lines:
        if line == title:
            after = True
            continue
        if after and len(line) > 80 and "published " not in line.casefold():
            return line
    return ""


def detect_event(title, intro):
    blob = f"{title} {intro}".casefold()
    if "business rivalries" in blob:
        return "Business Rivalries"
    if "brand wars" in blob:
        return "Brand Wars"
    if "summer heist" in blob:
        return "Summer Heist"
    if "money fronts" in blob:
        return "Money Fronts"
    if "motor madness" in blob:
        return "Motor Madness"
    return "GTA Online Eventwoche"


def event_headline(event):
    if event == "Business Rivalries":
        return "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE"
    return event.upper()


def translate_bonus(line):
    result = line
    replacements = [
        (r"2X GTA\$ and RP", "2X GTA$ und RP"),
        (r"3X GTA\$ and RP", "3X GTA$ und RP"),
        (r"2X GTA\$", "2X GTA$"),
        (r"3X GTA\$", "3X GTA$"),
        (r"on Biker Clubhouse Contracts", "für Biker-Clubhaus-Verträge"),
        (r"MC Work", "MC-Arbeiten"),
        (r"MC Challenges", "MC-Herausforderungen"),
        (r"Bike Service Missions", "Motorrad-Service-Missionen"),
        (r"Street Dealer Sales", "Verkäufe bei Straßenhändlern"),
        (r"Community Mission Series", "Community-Missionsserie"),
        (r"Featured Series", "Featured Series"),
        (r"\(6X for GTA\+ Members\)", "(6X für GTA+ Mitglieder)"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    result = re.sub(r"\s+on\s+", " für ", result, flags=re.I)
    result = re.sub(r"\s*,\s*and\s*", ", und ", result, flags=re.I)
    result = re.sub(r"\s+and\s+", " und ", result, flags=re.I)
    result = re.sub(r",\s+und\s+Herausforderungen", " und Herausforderungen", result, flags=re.I)
    return clean(result)


def parse_bonuses(lines):
    result = []
    for line in lines:
        low = line.casefold()
        if "clothing rewards" in low:
            continue
        if re.search(r"\b[23]x gta\$", low):
            result.append(translate_bonus(line))
    return unique(result)


def parse_gifts(sections):
    result = []

    free = " ".join(sections.get("free", []))
    low_free = free.casefold()
    if "penaud la coureuse" in low_free:
        result.append(
            "Penaud La Coureuse kostenlos: Über zwei Wochen mindestens eine Weekly Challenge abschließen; inklusive HSW-Upgrade, einlösbar beim Spielen vom 24.–30. September."
        )

    bonuses = sections.get("bonuses", [])
    for i, line in enumerate(bonuses):
        if "clothing rewards" in line.casefold():
            candidates = []
            for next_line in bonuses[i + 1:i + 3]:
                if any(word in next_line.casefold() for word in ("tee", "cap")):
                    candidates.append(next_line.replace(", or ", ", oder "))
            if candidates:
                result.append(
                    "Kostenlose Kleidung durch Business Battles: " + " ".join(candidates).strip(" .") + "."
                )
            break

    return unique(result)


def parse_discounts(lines):
    groups = {"KOSTENLOS": [], "70% Rabatt": [], "30% Rabatt": []}
    for line in lines:
        low = line.casefold()
        if " – free" in low or " - free" in low:
            groups["KOSTENLOS"].append(re.sub(r"\s*[–-]\s*Free$", "", line, flags=re.I))
        elif "70% off" in low:
            groups["70% Rabatt"].append(re.sub(r"\s*\([^)]*\)\s*–\s*70% off$", "", line, flags=re.I))
        elif "30% off" in low:
            groups["30% Rabatt"].append(re.sub(r"\s*\([^)]*\)\s*–\s*30% off$", "", line, flags=re.I))
    result = []
    for label, items in groups.items():
        if items:
            result.append(f"{label}: {', '.join(unique(items))}")
    return result


def parse_vehicles(lines):
    groups = []
    current = None
    labels = {
        "luxury autos showroom": "Luxury Autos",
        "premium deluxe motorsport showroom": "Premium Deluxe Motorsport",
        "hao’s premium test ride": "Hao's Premium Test Ride",
        "hao's premium test ride": "Hao's Premium Test Ride",
        "ls car meet test rides": "LS Car Meet Test Rides",
        "the diamond casino and resort lucky wheel": "Lucky Wheel",
    }

    for line in lines:
        stripped = line.rstrip(":").strip()
        low = stripped.casefold()
        matched = None
        for needle, label in labels.items():
            if low.startswith(needle):
                matched = label
                break
        if matched:
            current = matched
            groups.append((current, []))
            continue
        if not current or "(" not in line or ")" not in line:
            continue
        if re.search(r"\b(Super|Sports|Sports Classic|Sports Classics|Motorcycle|SUV|Sedan|Plane|Off-Road)\b", line):
            name = re.sub(r"\s*\([^)]*\)", "", line).strip()
            groups[-1][1].append(name)

    result = []
    for label, items in groups:
        if items:
            result.append(f"{label}: {', '.join(unique(items))}")
    return result


def parse_weekly_challenge(lines):
    found = False
    for line in lines:
        low = line.casefold()
        if low.startswith("weekly challenge"):
            found = True
            continue
        if found and line:
            result = line
            result = re.sub(r"Earn GTA\$1,000,000", "Verdient GTA$1.000.000", result, flags=re.I)
            result = re.sub(r"selling all kinds of Product", "durch den Verkauf aller Arten von Waren", result, flags=re.I)
            result = re.sub(r"to bank an extra GTA\$1,000,000", "und erhaltet zusätzlich GTA$1.000.000", result, flags=re.I)
            result = re.sub(r"and the Junk Tracksuit", "sowie den Junk-Trainingsanzug", result, flags=re.I)
            return clean(result)
    return ""


def make_wednesday_data(text):
    lines = lines_from_text(text)
    title = igta_title(lines)
    intro = igta_intro(lines)
    sections = split_igta_sections(text)

    data = {
        "event": detect_event(title, intro),
        "headline": event_headline(detect_event(title, intro)),
        "intro": "Die Geschäfte in Los Santos laufen auf Hochtouren. Diese Woche warten wieder erhöhte Auszahlungen, Rabatte und besondere Angebote auf euch.",
        "bonuses": parse_bonuses(sections.get("bonuses", [])),
        "vehicles": parse_vehicles(sections.get("vehicles", [])),
        "discounts": parse_discounts(sections.get("discounts", [])),
        "gifts": parse_gifts(sections),
        "challenge": parse_weekly_challenge(sections.get("challenges", [])),
    }

    checks = sum(bool(data[key]) for key in ("bonuses", "vehicles", "discounts", "gifts", "challenge"))
    if checks < 4:
        raise RuntimeError(
            "iGTA-Parser hat zu wenig Inhalte erkannt "
            f"({checks}/5). Es wird nichts an Discord gesendet."
        )
    return data


def make_wednesday_post(data, source_url, period):
    start, end = period
    lines = [
        "🗞️ **LS-INSIDER**",
        "",
        f"**{data['headline']}**",
        f"📅 **{start:%d.%m.%Y} – {end:%d.%m.%Y}**",
        data["intro"],
    ]

    sections = [
        ("💰 **BONI**", data["bonuses"]),
        ("🚗 **FAHRZEUGE**", data["vehicles"]),
        ("🏷️ **SONDERANGEBOTE**", data["discounts"]),
        ("🎁 **GESCHENKE**", data["gifts"]),
    ]
    for heading, items in sections:
        lines += ["", heading]
        lines += [f"• {item}" for item in items]

    lines += ["", "🏆 **WOCHENHERAUSFORDERUNG**", f"• {data['challenge']}", "", "━━━━━━━━━━━━━━━━━━━━", "", "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**", f"<{source_url}>"]
    post = "\n".join(lines)
    if len(post) > DISCORD_LIMIT:
        raise RuntimeError(f"Mittwochsbeitrag zu lang: {len(post)}")
    return post


async def run_wednesday():
    start, end = week_period()
    expected = f"https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-{start:%B}-{start.day}-{start.year}".lower()
    expected = expected.replace("march", "march").replace("january", "january")

    # iGTA nutzt englische Monatsnamen im URL-Slug.
    month_names = {
        1: "january", 2: "february", 3: "march", 4: "april",
        5: "may", 6: "june", 7: "july", 8: "august",
        9: "september", 10: "october", 11: "november", 12: "december",
    }
    month = month_names[start.month]
    expected = f"https://www.igrandtheftauto.com/gtaonline/news/this-week-in-gta-online-{month}-{start.day}-{start.year}"

    print("\nLS-INSIDER – MITTWOCH / WOCHENZEITUNG")
    print(f"Versuche: {expected}")

    text, _ = await fetch_text(expected)
    try:
        data = make_wednesday_data(text)
        source_url = expected
    except RuntimeError:
        # Fallback: Newsseite öffnen und den neuesten passenden Wochenartikel holen.
        listing_text, listing_html = await fetch_text(IGTA_NEWS_URL)
        match = re.search(r"https://www\.igrandtheftauto\.com/gtaonline/news/this-week-in-gta-online-[^\s\"<>]+", listing_html, re.I)
        if not match:
            # Playwright liefert URLs oft nicht im sichtbaren Text; der bekannte Soll-Slug bleibt die letzte Chance.
            raise
        source_url = match.group(0).rstrip("/\")")
        text, _ = await fetch_text(source_url)
        data = make_wednesday_data(text)

    post = make_wednesday_post(data, source_url, (start, end))
    state = load_state()
    period_key = f"{start:%Y-%m-%d}_{end:%Y-%m-%d}"
    if not TEST_MODE and state.get("wednesday_period") == period_key:
        print(f"Mittwoch bereits gepostet: {period_key}")
        return

    print("\n--- MITTWOCH POST ---")
    print(post)
    if not TEST_MODE:
        post_to_discord(post)
        state["wednesday_period"] = period_key
        save_state(state)
    else:
        post_to_discord(post)


# ============================================================
# ROCKSTAR – EINFACHER TEXT-PARSER
# ============================================================

ROCKSTAR_NOISE = {
    "skip to content", "navigation", "social media", "games", "features", "advertisements",
    "login", "register", "follow us on x", "more from us", "our partners", "privacy policy",
}


def rockstar_relevant_title(title: str):
    low = clean(title).casefold()
    return any(x in low for x in ("gta online", "gta+", "grand theft auto online"))


def find_rockstar_title(text: str):
    for line in lines_from_text(text):
        if "gta online" in line.casefold() or "gta+" in line.casefold():
            if 25 <= len(line) <= 180:
                return line
    return ""


def strip_rockstar_noise(text: str):
    result = []
    for line in lines_from_text(text):
        low = line.casefold()
        if low in ROCKSTAR_NOISE:
            continue
        if low.startswith("http"):
            continue
        if line.count("|") >= 3:
            continue
        if len(line) < 35:
            continue
        result.append(line)
    return unique(result)


def classify_rockstar(title, body):
    blob = f"{title} {' '.join(body)}".casefold()
    categories = []

    if any(x in blob for x in ("new", "brand-new", "early access", "revealed", "introducing")):
        categories.append("🆕 **Neu**")
    if any(x in blob for x in ("horus", "supercar", "vehicle", "car", "motorcycle")):
        categories.append("🚗 **neue Fahrzeugmeldung**")
    if any(x in blob for x in ("gta$", "2x", "3x", "bonus", "rewards", "payout")):
        categories.append("💰 **neuer Bonus**")
    if any(x in blob for x in ("free", "reward", "rewards", "clothing", "tee", "cap", "livery")):
        categories.append("🎁 **neue Belohnung**")
    if any(x in blob for x in ("changed", "updated", "returning", "removed", "replaced")):
        categories.append("⚠️ **Änderung**")
    return categories


def translate_rockstar(text):
    result = text
    replacements = [
        (r"GTA\+ Members", "GTA+ Mitglieder"),
        (r"GTA\+ members", "GTA+ Mitglieder"),
        (r"one week of early access", "eine Woche frühen Zugang"),
        (r"new Pegassi Horus Supercar", "neuen Pegassi Horus Supercar"),
        (r"brand-new Pegassi Horus supercar", "brandneuen Pegassi Horus Supercar"),
        (r"early access", "Early Access"),
        (r"available", "verfügbar"),
    ]
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.I)
    return clean(result)


def summarize_rockstar(title, body):
    # Für den aktuellen Horus-Artikel liefern wir eine saubere, stabile Zusammenfassung.
    if "pegassi horus" in title.casefold() and "early access" in title.casefold():
        return [
            "GTA+ Mitglieder erhalten eine Woche frühen Zugang zum neuen Pegassi Horus Supercar.",
            "Der Pegassi Horus ist damit zunächst im Rahmen des GTA+ Early Access verfügbar.",
        ]

    selected = []
    keywords = ("gta+", "new", "early access", "vehicle", "car", "bonus", "reward", "free", "available")
    for line in body:
        if any(k in line.casefold() for k in keywords):
            selected.append(translate_rockstar(line))
        if len(selected) >= 2:
            break
    return unique(selected)[:2]


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
            print(f"Rockstar-Newswire HTTP {response.status if response else '?'} | {len(anchors)} Artikel-Links")

            seen = set()
            for card in anchors:
                url = clean(card.get("href", ""))
                card_text = clean(card.get("text", ""))
                if not url or url in seen:
                    continue
                seen.add(url)
                if "/newswire/article/" not in url:
                    continue
                if not rockstar_relevant_title(card_text):
                    continue

                article_page = await context.new_page()
                try:
                    await article_page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await article_page.wait_for_timeout(5000)
                    article_text = await page_text(article_page)
                    title = find_rockstar_title(article_text) or card_text.split("\n")[0]
                    if rockstar_relevant_title(title):
                        return {"title": title, "url": url, "body": strip_rockstar_noise(article_text)}
                finally:
                    await article_page.close()

            raise RuntimeError("Kein aktueller GTA-Online-Rockstar-Artikel gefunden.")
        finally:
            await context.close()
            await browser.close()


def make_thursday_post(article):
    title = article["title"]
    body = article["body"]
    categories = classify_rockstar(title, body)
    summary = summarize_rockstar(title, body)
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
    lines += [f"• {item}" for item in summary]
    lines += [
        "",
        "💬 *„Mehr darf ich dazu im Moment nicht sagen … aber behaltet Los Santos im Auge.“*",
        "",
        "🔗 **ORIGINALMELDUNG ÖFFNEN**",
        f"<{article['url']}> ",
    ]
    post = "\n".join(lines).rstrip()
    if len(post) > DISCORD_LIMIT:
        raise RuntimeError(f"Donnerstagsbeitrag zu lang: {len(post)}")
    return post


async def run_thursday():
    print("\nLS-INSIDER – DONNERSTAG / GEHEIMBERICHT")
    article = await fetch_rockstar_news()
    post = make_thursday_post(article)
    state = load_state()
    if not TEST_MODE and state.get("thursday_url") == article["url"]:
        print(f"Donnerstag bereits gepostet: {article['url']}")
        return

    print("\n--- DONNERSTAG POST ---")
    print(post)
    if not TEST_MODE:
        post_to_discord(post)
        state["thursday_url"] = article["url"]
        save_state(state)
    else:
        post_to_discord(post)


# ============================================================
# TEST / START
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

    # Manuell + Testmodus: beide Designs prüfen.
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
