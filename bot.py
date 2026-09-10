import asyncio
import hashlib
import html
import json
import os
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# ============================================================
# LS-INSIDER
# ============================================================

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

IGTA_NEWS_URL = (
    "https://www.igrandtheftauto.com/gtaonline/news"
)

ROCKSTAR_URL = (
    "https://www.rockstargames.com/de/newswire?tag_id=735"
)

STATE_FILE = "weekly_state.json"

VIENNA = ZoneInfo("Europe/Vienna")

# ------------------------------------------------------------
# TEST-MODUS
# ------------------------------------------------------------
# True = manueller Testlauf
# False = normaler Mittwoch/Donnerstag-Betrieb
TEST_MODE = True

DISCORD_LIMIT = 1900


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)

    return text.strip()


def clean_line(text):
    return clean_text(
        re.sub(r"\s+", " ", text or "")
    ).strip()


def unique_items(items):
    result = []
    seen = set()

    for item in items:
        item = clean_line(item)

        if not item:
            continue

        key = item.lower()

        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def make_signature(*parts):
    raw = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)
    except Exception:
        return {}


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not message:
        return

    chunks = []

    while len(message) > DISCORD_LIMIT:
        split_at = message.rfind(
            "\n",
            0,
            DISCORD_LIMIT,
        )

        if split_at < 500:
            split_at = DISCORD_LIMIT

        chunks.append(
            message[:split_at]
        )

        message = message[split_at:].lstrip()

    if message:
        chunks.append(message)

    for chunk in chunks:
        response = requests.post(
            WEBHOOK_URL,
            json={
                "content": chunk,
                "allowed_mentions": {
                    "parse": []
                },
            },
            timeout=30,
        )

        response.raise_for_status()

        print(
            "Discord-Nachricht erfolgreich gesendet."
        )


# ============================================================
# DATUM
# ============================================================

def current_week_dates():
    now = datetime.now(
        VIENNA
    ).date()

    days_since_thursday = (
        now.weekday() - 3
    ) % 7

    start = now - timedelta(
        days=days_since_thursday
    )

    end = start + timedelta(
        days=6
    )

    return start, end


def current_week_period():
    start, end = current_week_dates()

    return (
        f"{start.day:02d}."
        f"{start.month:02d}."
        f"{start.year}"
        f" – "
        f"{end.day:02d}."
        f"{end.month:02d}."
        f"{end.year}"
    )


def format_period(start_date, end_date):
    return (
        f"{start_date.day:02d}."
        f"{start_date.month:02d}."
        f"{start_date.year}"
        f" – "
        f"{end_date.day:02d}."
        f"{end_date.month:02d}."
        f"{end_date.year}"
    )


# ============================================================
# IGRANDTHEFTAUTO HTML PARSER
# ============================================================

class ArticleParser(HTMLParser):

    TARGET_TAGS = {
        "h1",
        "h2",
        "h3",
        "p",
        "li",
    }

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.entries = []
        self.current_tag = None
        self.current_parts = []
        self.article_started = False
        self.article_finished = False

    def handle_starttag(self, tag, attrs):
        if self.article_finished:
            return

        tag = tag.lower()

        if tag == "h1":
            self.article_started = True
            self.current_tag = tag
            self.current_parts = []
            return

        if not self.article_started:
            return

        if tag in self.TARGET_TAGS:
            self.current_tag = tag
            self.current_parts = []

    def handle_data(self, data):
        if self.article_finished:
            return

        if self.current_tag:
            self.current_parts.append(data)

    def handle_endtag(self, tag):
        if self.article_finished:
            return

        tag = tag.lower()

        if (
            self.current_tag == tag
            and tag in self.TARGET_TAGS
        ):
            text = clean_line(
                " ".join(
                    self.current_parts
                )
            )

            if text:
                self.entries.append(
                    {
                        "tag": tag,
                        "text": text,
                    }
                )

            if (
                "This article has been viewed"
                in text
            ):
                self.article_finished = True

            self.current_tag = None
            self.current_parts = []


def parse_article_html(source):
    parser = ArticleParser()
    parser.feed(source)
    return parser.entries


# ============================================================
# IGRANDTHEFTAUTO ABRUF
# ============================================================

def make_expected_igta_url():
    start, _ = current_week_dates()

    month_name = start.strftime(
        "%B"
    ).lower()

    return (
        "https://www.igrandtheftauto.com/"
        "gtaonline/news/"
        "this-week-in-gta-online-"
        f"{month_name}-"
        f"{start.day}-"
        f"{start.year}"
    )


def requests_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "*/*;q=0.8"
        ),
        "Accept-Language": (
            "en-US,en;q=0.9,de;q=0.8"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.google.com/",
    }


def fetch_url_requests(url):
    response = requests.get(
        url,
        headers=requests_headers(),
        timeout=30,
    )

    response.raise_for_status()

    return response.text


async def fetch_url_playwright(url):
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features="
                "AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 2400,
            },
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        page = await context.new_page()

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                5000
            )

            # Cookie-Banner
            try:
                buttons = page.locator(
                    "button"
                )

                for i in range(
                    await buttons.count()
                ):
                    button = buttons.nth(i)

                    try:
                        text = clean_line(
                            await button.inner_text()
                        )

                        if any(
                            word in text.lower()
                            for word in [
                                "accept",
                                "accept all",
                                "agree",
                                "akzeptieren",
                                "alle akzeptieren",
                            ]
                        ):
                            await button.click(
                                timeout=2000
                            )
                            break

                    except Exception:
                        continue

            except Exception:
                pass

            await page.wait_for_timeout(
                2500
            )

            content = await page.content()

            return content

        finally:
            await context.close()
            await browser.close()


async def fetch_igta_article():
    expected_url = make_expected_igta_url()

    print()
    print(
        "iGrandTheftAuto:"
    )
    print(
        "Versuche aktuellen Wochenartikel:"
    )
    print(
        expected_url
    )

    # --------------------------------------------------------
    # 1. Playwright – Hauptweg gegen 403
    # --------------------------------------------------------

    try:
        article_html = await fetch_url_playwright(
            expected_url
        )

        entries = parse_article_html(
            article_html
        )

        if any(
            entry["tag"] == "h1"
            and "This Week in GTA Online:"
            in entry["text"]
            for entry in entries
        ):
            print(
                "Aktueller Wochenartikel "
                "über Browser erfolgreich gefunden."
            )

            return (
                expected_url,
                article_html,
            )

    except Exception as error:
        print(
            "Browser-Abruf des direkten "
            "Artikels nicht möglich:"
        )
        print(error)

    # --------------------------------------------------------
    # 2. Requests – Fallback
    # --------------------------------------------------------

    try:
        article_html = fetch_url_requests(
            expected_url
        )

        entries = parse_article_html(
            article_html
        )

        if entries:
            print(
                "Aktueller Wochenartikel "
                "über direkten Abruf gefunden."
            )

            return (
                expected_url,
                article_html,
            )

    except Exception as error:
        print(
            "Direkter Requests-Abruf nicht möglich:"
        )
        print(error)

    # --------------------------------------------------------
    # 3. Newsseite über Playwright
    # --------------------------------------------------------

    print()
    print(
        "Suche aktuellen Wochenartikel "
        "über die GTA-Online-Newsseite ..."
    )

    try:
        listing_html = await fetch_url_playwright(
            IGTA_NEWS_URL
        )

        article_url = (
            find_latest_weekly_article_from_listing(
                listing_html
            )
        )

        if article_url:
            print(
                "Gefundener Artikel:"
            )
            print(
                article_url
            )

            article_html = (
                await fetch_url_playwright(
                    article_url
                )
            )

            entries = parse_article_html(
                article_html
            )

            if entries:
                return (
                    article_url,
                    article_html,
                )

    except Exception as error:
        print(
            "Browser-Newsseite konnte "
            "nicht gelesen werden:"
        )
        print(error)

    # --------------------------------------------------------
    # 4. Newsseite über Requests
    # --------------------------------------------------------

    try:
        listing_html = fetch_url_requests(
            IGTA_NEWS_URL
        )

        article_url = (
            find_latest_weekly_article_from_listing(
                listing_html
            )
        )

        if article_url:
            print(
                "Gefundener Artikel:"
            )
            print(
                article_url
            )

            article_html = fetch_url_requests(
                article_url
            )

            return (
                article_url,
                article_html,
            )

    except Exception as error:
        print(
            "Requests-Newsseite konnte "
            "nicht gelesen werden:"
        )
        print(error)

    raise RuntimeError(
        "iGrandTheftAuto konnte den aktuellen "
        "Wochenartikel nicht abrufen."
    )


def find_latest_weekly_article_from_listing(
    listing_html
):
    patterns = [
        re.compile(
            r'href=["\']'
            r'([^"\']*this-week-in-gta-online-[^"\']+)'
            r'["\']',
            re.IGNORECASE,
        ),
        re.compile(
            r'href=["\']'
            r'([^"\']*/gtaonline/news/[^"\']+)'
            r'["\']',
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        matches = pattern.findall(
            listing_html
        )

        for href in matches:
            absolute = urljoin(
                IGTA_NEWS_URL + "/",
                href,
            )

            if (
                "this-week-in-gta-online-"
                in absolute.lower()
            ):
                return absolute

    return None


# ============================================================
# IGRANDTHEFTAUTO STRUKTUR
# ============================================================

def entries_to_sections(entries):
    sections = {}

    current_heading = "__INTRO__"

    for entry in entries:
        tag = entry["tag"]
        text = entry["text"]

        if tag in {
            "h1",
            "h2",
            "h3",
        }:
            current_heading = text

            sections.setdefault(
                current_heading,
                [],
            )

        else:
            sections.setdefault(
                current_heading,
                [],
            ).append(text)

    return sections


def get_section(
    sections,
    *possible_names,
):
    wanted = {
        clean_line(name).lower()
        for name in possible_names
    }

    for key, values in sections.items():
        key_clean = clean_line(
            key
        ).lower()

        if key_clean in wanted:
            return values

    return []


# ============================================================
# ARTIKEL / ZEITRAUM
# ============================================================

def extract_article_title(entries):
    for entry in entries:
        if entry["tag"] == "h1":
            return entry["text"]

    return ""


def extract_intro(entries):
    title_seen = False

    for entry in entries:
        if entry["tag"] == "h1":
            title_seen = True
            continue

        if not title_seen:
            continue

        if entry["tag"] == "p":
            text = entry["text"]

            if (
                text
                and not text.startswith("Published ")
                and not text.startswith("Last Updated ")
            ):
                return text

    return ""


def extract_period_from_title(title):
    match = re.search(
        r"This Week in GTA Online:\s*"
        r"([A-Za-z]+)\s+"
        r"(\d{1,2}),\s+"
        r"(\d{4})",
        title,
        re.IGNORECASE,
    )

    if not match:
        return current_week_period()

    month_name = match.group(1)
    day = int(match.group(2))
    year = int(match.group(3))

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

    month_number = months.get(
        month_name.lower()
    )

    if not month_number:
        return current_week_period()

    start = datetime(
        year,
        month_number,
        day,
    ).date()

    end = start + timedelta(
        days=6
    )

    return format_period(
        start,
        end,
    )


# ============================================================
# WOCHENSCHWERPUNKT
# ============================================================

def detect_event_name(
    title,
    intro,
    sections,
):
    combined = (
        f"{title}\n"
        f"{intro}\n"
        f"{' '.join(sections.keys())}"
    )

    known_events = [
        (
            "Business Rivalries",
            "BUSINESS RIVALRIES GEHEN IN DIE NÄCHSTE RUNDE",
        ),
        (
            "Brand Wars",
            "BRAND WARS GEHEN IN DIE NÄCHSTE RUNDE",
        ),
        (
            "Summer Heist",
            "DER SUMMER HEIST GEHT IN DIE NÄCHSTE RUNDE",
        ),
    ]

    for search, headline in known_events:
        if search.lower() in combined.lower():
            return (
                headline,
                build_german_intro(
                    search
                ),
            )

    fallback = re.sub(
        r"^This Week in GTA Online:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    fallback = re.sub(
        r"\s*\([^)]*\)",
        "",
        fallback,
    )

    fallback = clean_line(
        fallback
    )

    if not fallback:
        fallback = "GTA ONLINE EVENTWOCHE"

    return (
        fallback.upper(),
        build_german_intro(
            None
        ),
    )


def build_german_intro(
    event_name,
):
    if event_name == "Business Rivalries":
        return (
            "Die Geschäfte in Los Santos "
            "laufen auf Hochtouren. Diese "
            "Woche warten wieder erhöhte "
            "Auszahlungen, Rabatte und "
            "besondere Angebote auf euch."
        )

    if event_name == "Brand Wars":
        return (
            "In Los Santos geht der aktuelle "
            "Event weiter. Diese Woche warten "
            "neue Belohnungen, erhöhte "
            "Auszahlungen, Rabatte und "
            "besondere Angebote auf euch."
        )

    if event_name == "Summer Heist":
        return (
            "In Los Santos laufen die "
            "Geschäfte auf Hochtouren. "
            "Diese Woche warten erhöhte "
            "Auszahlungen, neue Belohnungen "
            "und besondere Angebote auf euch."
        )

    return (
        "In Los Santos wartet diese Woche "
        "wieder eine neue Auswahl an Boni, "
        "Belohnungen, Rabatten und "
        "besonderen Angeboten auf euch."
    )


# ============================================================
# BONI
# ============================================================

def translate_bonus_line(line):
    line = clean_line(line)

    match = re.match(
        r"^(\d+)X\s+GTA\$\s+and\s+RP\s+on\s+(.+)$",
        line,
        re.IGNORECASE,
    )

    if match:
        return (
            f"{match.group(1)}× GTA$ & RP – "
            f"{clean_line(match.group(2))}"
        )

    match = re.match(
        r"^(\d+)X\s+GTA\$\s+on\s+(.+)$",
        line,
        re.IGNORECASE,
    )

    if match:
        activity = clean_line(
            match.group(2)
        )

        # GTA+ Zusatz erkennen
        gta_plus_match = re.search(
            r"\((\d+)X\s+GTA\+"
            r"(?:\s+Members?)?\)",
            activity,
            re.IGNORECASE,
        )

        if gta_plus_match:
            gta_plus = gta_plus_match.group(1)

            activity = re.sub(
                r"\s*\(\d+X\s+GTA\+"
                r"(?:\s+Members?)?\)",
                "",
                activity,
                flags=re.IGNORECASE,
            )

            return (
                f"{match.group(1)}× GTA$ – "
                f"{activity} "
                f"({gta_plus}× für GTA+)"
            )

        return (
            f"{match.group(1)}× GTA$ – "
            f"{activity}"
        )

    return ""


def extract_bonuses(sections):
    raw = get_section(
        sections,
        "Bonuses and Rewards",
    )

    results = []

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        if (
            "Business Battle" in line
            or "Six Figure Tee" in line
            or "Ride or Die Tee" in line
            or "Ammu-Nation Cap" in line
            or "Bourgeoix Tee" in line
        ):
            continue

        translated = translate_bonus_line(
            line
        )

        if translated:
            results.append(
                translated
            )

    return unique_items(
        results
    )


# ============================================================
# GESCHENKE
# ============================================================

def extract_gifts(sections):
    results = []

    bonuses = get_section(
        sections,
        "Bonuses and Rewards",
    )

    bonus_text = " ".join(
        bonuses
    ).lower()

    clothing = [
        "Six Figure Tee",
        "Ride or Die Tee",
        "Red and White Ammu-Nation Cap",
        "Bourgeoix Tee",
    ]

    for item in clothing:
        if item.lower() in bonus_text:
            results.append(
                f"{item} – kostenlose Belohnung"
            )

    free_section = get_section(
        sections,
        "Free Penaud La Coureuse and HSW upgrade",
        "Free Penaud La Coureuse and HSW Upgrade",
    )

    free_text = " ".join(
        free_section
    ).lower()

    if "penaude la coureuse" in free_text:
        results.append(
            "Penaud La Coureuse – KOSTENLOS "
            "+ HSW-Upgrade bei erfüllter "
            "Wochenherausforderung"
        )

    return unique_items(
        results
    )


# ============================================================
# RABATTE
# ============================================================

def extract_discounts(sections):
    raw = get_section(
        sections,
        "Discounts",
    )

    results = []
    seen = set()

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        free_match = re.search(
            r"\s+[–-]\s*Free$",
            line,
            re.IGNORECASE,
        )

        if free_match:
            name = re.sub(
                r"\s+[–-]\s*Free$",
                "",
                line,
                flags=re.IGNORECASE,
            )

            item = {
                "name": clean_line(name),
                "percentage": 100,
                "free": True,
            }

        else:
            match = re.search(
                r"\s+[–-]\s*(\d+)%\s*off$",
                line,
                re.IGNORECASE,
            )

            if not match:
                continue

            percentage = int(
                match.group(1)
            )

            name = re.sub(
                r"\s+[–-]\s*\d+%\s*off$",
                "",
                line,
                flags=re.IGNORECASE,
            )

            item = {
                "name": clean_line(name),
                "percentage": percentage,
                "free": False,
            }

        key = (
            item["name"].lower(),
            item["percentage"],
            item["free"],
        )

        if key in seen:
            continue

        seen.add(key)
        results.append(item)

    results.sort(
        key=lambda item: (
            -item["percentage"],
            item["name"].lower(),
        )
    )

    return results


# ============================================================
# FAHRZEUGE
# ============================================================

def extract_vehicles(sections):
    raw = get_section(
        sections,
        "Vehicles",
    )

    results = []

    category = ""

    category_names = [
        "Luxury Autos",
        "Premium Deluxe Motorsport",
        "Hao's Premium Test Ride",
        "Hao’s Premium Test Ride",
        "LS Car Meet Test Rides",
        "Lucky Wheel",
    ]

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        matched_category = None

        for possible in category_names:
            if possible.lower() in line.lower():
                matched_category = possible
                break

        if matched_category:
            category = matched_category
            continue

        # Einzelne Fahrzeugnamen.
        # Text mit langen Beschreibungen wird ignoriert.
        if len(line) > 100:
            continue

        # Offensichtliche Nicht-Fahrzeugzeilen ignorieren.
        if any(
            word in line.lower()
            for word in [
                "available at",
                "visit",
                "purchase",
                "this week",
                "discount",
                "test ride",
            ]
        ):
            continue

        # Fahrzeugnamen aus HTML-Liste übernehmen.
        if category:
            results.append(
                {
                    "name": line,
                    "category": category,
                }
            )

    cleaned = []
    seen = set()

    for item in results:
        key = item["name"].lower()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(item)

    return cleaned


def format_vehicle(vehicle):
    name = vehicle["name"]
    category = vehicle.get(
        "category",
        "",
    )

    if category == "Luxury Autos":
        return (
            f"🚗 **{name}** – Luxury Autos"
        )

    if category == "Premium Deluxe Motorsport":
        return (
            f"🚗 **{name}** – Premium Deluxe Motorsport"
        )

    if "Hao" in category:
        return (
            f"🏎️ **{name}** – HSW Test Ride"
        )

    if "LS Car Meet" in category:
        return (
            f"🚗 **{name}** – LS Car Meet Test Ride"
        )

    if "Lucky Wheel" in category:
        return (
            f"🎰 **{name}** – Glücksrad"
        )

    return f"🚗 **{name}**"


# ============================================================
# WOCHENHERAUSFORDERUNG
# ============================================================

def extract_weekly_challenge(sections):
    challenges = get_section(
        sections,
        "Challenges",
    )

    if not challenges:
        return None

    for line in challenges:
        line = clean_line(line)

        if not line:
            continue

        if (
            "GTA$1,000,000" in line
            and "Product" in line
        ):
            return (
                "GTA$1.000.000 durch den "
                "Verkauf aller Arten von "
                "Produkten verdienen – dazu "
                "gibt es weitere GTA$1.000.000 "
                "und den Junk Tracksuit."
            )

    return None


# ============================================================
# MITTWOCH – DESIGN
# ============================================================

def build_wednesday_message(
    period,
    headline,
    intro,
    bonuses,
    vehicles,
    discounts,
    gifts,
    challenge,
    source_url,
):
    message = []

    message.append(
        "🗞️ **LS-INSIDER**"
    )
    message.append("")

    message.append(
        "### 📰 **WOCHENSCHWERPUNKT**"
    )
    message.append("")

    message.append(
        f"**{headline}**"
    )
    message.append("")

    message.append(
        f"📅 **{period}**"
    )
    message.append("")

    message.append(
        intro
    )
    message.append("")

    message.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )
    message.append("")

    # Boni
    if bonuses:
        message.append(
            "### 💰 **BONI**"
        )
        message.append("")

        for bonus in bonuses:
            message.append(
                f"• {bonus}"
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # Fahrzeuge
    if vehicles:
        message.append(
            "### 🚗 **FAHRZEUGE**"
        )
        message.append("")

        for vehicle in vehicles:
            message.append(
                format_vehicle(vehicle)
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # Sonderangebote
    if discounts:
        message.append(
            "### 🏷️ **SONDERANGEBOTE**"
        )
        message.append("")

        paid = [
            item
            for item in discounts
            if not item["free"]
        ]

        free = [
            item
            for item in discounts
            if item["free"]
        ]

        grouped = {}

        for item in paid:
            grouped.setdefault(
                item["percentage"],
                [],
            ).append(
                item["name"]
            )

        for percentage in sorted(
            grouped,
            reverse=True,
        ):
            for name in unique_items(
                grouped[percentage]
            ):
                message.append(
                    f"• **{name}** "
                    f"– {percentage} % Rabatt"
                )

        for item in free:
            message.append(
                f"• **{item['name']}** "
                "– KOSTENLOS"
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # Geschenke
    if gifts:
        message.append(
            "### 🎁 **GESCHENKE**"
        )
        message.append("")

        for gift in gifts:
            message.append(
                f"• {gift}"
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # Herausforderung
    if challenge:
        message.append(
            "### 🏆 **WOCHENHERAUSFORDERUNG**"
        )
        message.append("")

        message.append(
            f"• {challenge}"
        )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # Quelle
    message.append(
        "🔗 **QUELLE / VOLLSTÄNDIGER ARTIKEL**"
    )
    message.append("")
    message.append(
        source_url
    )

    return "\n".join(
        message
    )


# ============================================================
# MITTWOCH
# ============================================================

async def run_wednesday():
    print()
    print("=" * 70)
    print(
        "LS-INSIDER – MITTWOCH / WOCHENZEITUNG"
    )
    print("=" * 70)

    source_url, article_html = (
        await fetch_igta_article()
    )

    entries = parse_article_html(
        article_html
    )

    if not entries:
        raise RuntimeError(
            "Der iGrandTheftAuto-Artikel "
            "konnte nicht ausgelesen werden."
        )

    sections = entries_to_sections(
        entries
    )

    title = extract_article_title(
        entries
    )

    if not title:
        raise RuntimeError(
            "Kein Artikel-Titel erkannt."
        )

    intro = extract_intro(
        entries
    )

    period = extract_period_from_title(
        title
    )

    headline, german_intro = (
        detect_event_name(
            title,
            intro,
            sections,
        )
    )

    bonuses = extract_bonuses(
        sections
    )

    discounts = extract_discounts(
        sections
    )

    gifts = extract_gifts(
        sections
    )

    vehicles = extract_vehicles(
        sections
    )

    challenge = (
        extract_weekly_challenge(
            sections
        )
    )

    # --------------------------------------------------------
    # DOPPLUNGEN VERMEIDEN
    # --------------------------------------------------------

    discount_names = {
        item["name"].lower()
        for item in discounts
    }

    vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle["name"].lower()
        not in discount_names
    ]

    print()
    print("=" * 70)
    print(
        "MITTWOCH-DATEN ERFOLGREICH AUSGELESEN"
    )
    print("=" * 70)

    print(
        "Quelle:",
        source_url,
    )

    print(
        "Artikel:",
        title,
    )

    print(
        "Zeitraum:",
        period,
    )

    print(
        "Wochenschwerpunkt:",
        headline,
    )

    print(
        "Boni:",
        len(bonuses),
    )

    print(
        "Fahrzeuge:",
        len(vehicles),
    )

    print(
        "Sonderangebote:",
        len(discounts),
    )

    print(
        "Geschenke:",
        len(gifts),
    )

    print(
        "Wochenherausforderung:",
        "JA" if challenge else "NEIN",
    )

    message = build_wednesday_message(
        period=period,
        headline=headline,
        intro=german_intro,
        bonuses=bonuses,
        vehicles=vehicles,
        discounts=discounts,
        gifts=gifts,
        challenge=challenge,
        source_url=source_url,
    )

    print()
    print("=" * 70)
    print(
        "FERTIGER MITTWOCH-POST"
    )
    print("=" * 70)
    print(message)
    print("=" * 70)

    signature = make_signature(
        source_url,
        period,
        headline,
        german_intro,
        bonuses,
        vehicles,
        discounts,
        gifts,
        challenge,
    )

    # --------------------------------------------------------
    # TEST
    # --------------------------------------------------------

    if TEST_MODE:
        print()
        print(
            "TEST_MODE=True"
        )
        print(
            "Mittwoch wird zu Testzwecken "
            "gesendet."
        )
        print(
            "State wird NICHT verändert."
        )

        send_discord(
            message
        )

        return

    # --------------------------------------------------------
    # NORMALBETRIEB
    # --------------------------------------------------------

    state = load_state()

    if (
        state.get(
            "wednesday_signature"
        )
        == signature
    ):
        print(
            "Diese Wochenmeldung wurde "
            "bereits gepostet."
        )
        return

    send_discord(
        message
    )

    state[
        "wednesday_signature"
    ] = signature

    state[
        "wednesday_period"
    ] = period

    state[
        "wednesday_posted_at"
    ] = datetime.now(
        VIENNA
    ).isoformat()

    save_state(
        state
    )

    print(
        "Mittwoch-State gespeichert."
    )


# ============================================================
# DONNERSTAG – ROCKSTAR
# ============================================================

async def fetch_rockstar_news():
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features="
                "AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 2400,
            },
            locale="de-DE",
        )

        page = await context.new_page()

        try:
            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                7000
            )

            # Cookie-Dialog
            try:
                buttons = page.locator(
                    "button"
                )

                for i in range(
                    await buttons.count()
                ):
                    button = buttons.nth(i)

                    try:
                        text = clean_line(
                            await button.inner_text()
                        )

                        if any(
                            word in text.lower()
                            for word in [
                                "accept",
                                "akzeptieren",
                                "alle akzeptieren",
                            ]
                        ):
                            await button.click(
                                timeout=1500
                            )
                            break

                    except Exception:
                        continue

            except Exception:
                pass

            await page.wait_for_timeout(
                2000
            )

            articles = []

            article_nodes = page.locator(
                "article"
            )

            for i in range(
                await article_nodes.count()
            ):
                article = article_nodes.nth(i)

                try:
                    text = clean_text(
                        await article.inner_text()
                    )

                    links = article.locator(
                        "a[href]"
                    )

                    if await links.count() == 0:
                        continue

                    href = await (
                        links.first.get_attribute(
                            "href"
                        )
                    )

                    if not href:
                        continue

                    href = urljoin(
                        "https://www.rockstargames.com",
                        href,
                    )

                    title = ""

                    for selector in [
                        "h1",
                        "h2",
                        "h3",
                        "h4",
                    ]:
                        headings = article.locator(
                            selector
                        )

                        if await headings.count():
                            title = clean_line(
                                await headings.first.inner_text()
                            )

                            if title:
                                break

                    if title:
                        articles.append(
                            {
                                "title": title,
                                "url": href,
                                "text": text,
                            }
                        )

                except Exception:
                    continue

            # Fallback Links
            if not articles:
                links = page.locator(
                    "a[href]"
                )

                for i in range(
                    await links.count()
                ):
                    link = links.nth(i)

                    try:
                        href = await (
                            link.get_attribute(
                                "href"
                            )
                        )

                        title = clean_line(
                            await link.inner_text()
                        )

                        if not href or not title:
                            continue

                        href = urljoin(
                            "https://www.rockstargames.com",
                            href,
                        )

                        if "/newswire" in href.lower():
                            articles.append(
                                {
                                    "title": title,
                                    "url": href,
                                    "text": title,
                                }
                            )

                    except Exception:
                        continue

            # GTA Online Filter
            filtered = []

            excluded_words = [
                "gta vi",
                "grand theft auto vi",
                "gta 6",
                "grand theft auto 6",
                "red dead",
                "red dead redemption",
                "rockstar games launcher",
                "gta v",
                "grand theft auto v",
            ]

            for article in articles:
                title = clean_line(
                    article["title"]
                )

                text = clean_text(
                    article["text"]
                )

                combined = (
                    title + "\n" + text
                ).lower()

                if (
                    "gta online" not in combined
                    and
                    "grand theft auto online"
                    not in combined
                ):
                    continue

                if any(
                    word in title.lower()
                    for word in excluded_words
                ):
                    continue

                filtered.append(
                    article
                )

            result = []
            seen = set()

            for article in filtered:
                url = article["url"]

                if url in seen:
                    continue

                seen.add(url)
                result.append(article)

            print(
                "Relevante GTA-Online-News "
                f"gefunden: {len(result)}"
            )

            return result

        finally:
            await context.close()
            await browser.close()


# ============================================================
# DONNERSTAG – FILTER
# ============================================================

def is_relevant_gta_online_article(
    article
):
    title = clean_line(
        article.get("title", "")
    )

    text = clean_text(
        article.get("text", "")
    )

    combined = (
        title + "\n" + text
    ).lower()

    if (
        "gta online" not in combined
        and
        "grand theft auto online"
        not in combined
    ):
        return False

    excluded = [
        "gta vi",
        "grand theft auto vi",
        "gta 6",
        "grand theft auto 6",
        "red dead",
        "red dead redemption",
        "gta v",
        "grand theft auto v",
    ]

    if any(
        word in title.lower()
        for word in excluded
    ):
        return False

    return True


def article_signature(article):
    return make_signature(
        article.get("title", ""),
        article.get("url", ""),
    )


# ============================================================
# DONNERSTAG – KATEGORIEN
# ============================================================

def detect_thursday_categories(article):
    title = clean_line(
        article.get("title", "")
    )

    text = clean_text(
        article.get("text", "")
    )

    combined = (
        title + "\n" + text
    ).lower()

    categories = []

    if any(
        word in combined
        for word in [
            "new",
            "arrives",
            "available",
            "early access",
            "introducing",
        ]
    ):
        categories.append(
            "🆕 **Neu**"
        )

    if any(
        word in combined
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

    if (
        "bonus" in combined
        or re.search(
            r"\b[2-9]x\b",
            combined,
        )
    ):
        categories.append(
            "💰 **neuer Bonus**"
        )

    if any(
        word in combined
        for word in [
            "vehicle",
            "vehicles",
            "car",
            "cars",
            "motorcycle",
            "motorcycles",
            "pegassi",
            "bravado",
            "benefactor",
            "declasse",
            "vapid",
            "grotti",
            "overflod",
        ]
    ):
        categories.append(
            "🚗 **neue Fahrzeugmeldung**"
        )

    if any(
        word in combined
        for word in [
            "reward",
            "rewards",
            "free",
            "clothing",
            "prize",
            "complimentary",
        ]
    ):
        categories.append(
            "🎁 **neue Belohnung**"
        )

    return unique_items(
        categories
    )


# ============================================================
# DONNERSTAG – DESIGN
# ============================================================

def build_thursday_message(article):
    title = clean_line(
        article.get("title", "")
    )

    url = article.get(
        "url",
        "",
    )

    categories = (
        detect_thursday_categories(
            article
        )
    )

    message = []

    message.append(
        "🕵️ **LS-INSIDER – GEHEIMBERICHT**"
    )
    message.append("")

    message.append(
        "🤫 **Psst … eine neue Meldung "
        "ist eingetroffen.**"
    )
    message.append("")

    message.append(
        "Unser Informant hat Neuigkeiten "
        "aus Los Santos durchgegeben:"
    )
    message.append("")

    if title:
        message.append(
            f"**{title}**"
        )
        message.append("")

    for category in categories:
        message.append(
            category
        )

    if categories:
        message.append("")

    message.append(
        "🕵️ **INFORMANTENBERICHT**"
    )

    message.append(
        "Die aktuelle Rockstar-Meldung "
        "wurde überprüft und ist für den "
        "LS-Insider eingetroffen."
    )

    message.append("")

    message.append(
        "💬 *„Mehr darf ich dazu im Moment "
        "nicht sagen … aber behaltet Los "
        "Santos im Auge.“*"
    )

    message.append("")

    message.append(
        "🔗 **ORIGINALMELDUNG ÖFFNEN**"
    )

    message.append(
        url
    )

    return "\n".join(
        message
    )


# ============================================================
# DONNERSTAG
# ============================================================

async def run_thursday():
    print()
    print("=" * 70)
    print(
        "LS-INSIDER – DONNERSTAG / GEHEIMBERICHT"
    )
    print("=" * 70)

    articles = await fetch_rockstar_news()

    relevant = [
        article
        for article in articles
        if is_relevant_gta_online_article(
            article
        )
    ]

    if not relevant:
        print(
            "Keine relevante GTA-Online-"
            "Meldung gefunden."
        )
        return

    article = relevant[0]

    print()
    print(
        "Neueste relevante GTA-Online-"
        "Meldung:"
    )

    print(
        article.get(
            "title",
            "",
        )
    )

    print(
        article.get(
            "url",
            "",
        )
    )

    message = build_thursday_message(
        article
    )

    signature = article_signature(
        article
    )

    print()
    print("=" * 70)
    print(
        "FERTIGER DONNERSTAG-POST"
    )
    print("=" * 70)
    print(message)
    print("=" * 70)

    if TEST_MODE:
        print()
        print(
            "TEST_MODE=True"
        )
        print(
            "Donnerstag wird zu Testzwecken "
            "gesendet."
        )
        print(
            "State wird NICHT verändert."
        )

        send_discord(
            message
        )

        return

    state = load_state()

    if (
        state.get(
            "thursday_signature"
        )
        == signature
    ):
        print(
            "Diese GTA-Online-Meldung wurde "
            "bereits gepostet."
        )
        return

    send_discord(
        message
    )

    state[
        "thursday_signature"
    ] = signature

    state[
        "thursday_title"
    ] = article.get(
        "title",
        "",
    )

    state[
        "thursday_url"
    ] = article.get(
        "url",
        "",
    )

    state[
        "thursday_posted_at"
    ] = datetime.now(
        VIENNA
    ).isoformat()

    save_state(
        state
    )

    print(
        "Donnerstag-State gespeichert."
    )


# ============================================================
# TESTLAUF
# ============================================================

async def run_test():
    print()
    print("=" * 70)
    print(
        "LS-INSIDER TESTLAUF"
    )
    print("=" * 70)

    print(
        "TEST_MODE=True"
    )

    print(
        "State wird NICHT verändert."
    )

    print("=" * 70)

    await run_wednesday()

    print()
    print("=" * 70)

    await run_thursday()


# ============================================================
# HAUPTPROGRAMM
# ============================================================

async def main():

    if TEST_MODE:
        await run_test()
        return

    now = datetime.now(
        VIENNA
    )

    # Mittwoch
    if now.weekday() == 2:
        await run_wednesday()

    # Donnerstag
    elif now.weekday() == 3:
        await run_thursday()

    else:
        print(
            "Heute ist kein regulärer "
            "LS-Insider-Tag."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    asyncio.run(
        main()
    )
