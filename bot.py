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
# True:
#   Mittwoch und Donnerstag werden bei einem manuellen Lauf
#   zu Testzwecken gesendet.
#
#   Der State wird NICHT verändert.
#
# False:
#   Normalbetrieb.
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
    if not text:
        return ""

    return clean_text(
        re.sub(r"\s+", " ", text)
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

        message = message[
            split_at:
        ].lstrip()

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


def german_month(month):
    return MONTHS.get(
        month,
        month,
    )


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


def format_period(
    start_date,
    end_date,
):
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
# HTML-PARSER FÜR IGRANDTHEFTAUTO
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

        self.heading_seen = False
        self.article_started = False
        self.article_finished = False

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
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
            self.current_parts.append(
                data
            )

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
# IGRANDTHEFTAUTO – WOCHENARTIKEL FINDEN
# ============================================================

def make_expected_igta_url():
    start, _ = current_week_dates()

    month_name = start.strftime(
        "%B"
    ).lower()

    return (
        f"https://www.igrandtheftauto.com/"
        f"gtaonline/news/"
        f"this-week-in-gta-online-"
        f"{month_name}-"
        f"{start.day}-"
        f"{start.year}"
    )


def fetch_url(url):
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.text


def find_latest_weekly_article_from_listing(
    listing_html
):
    pattern = re.compile(
        r'href=["\']'
        r'([^"\']*/gtaonline/news/'
        r'this-week-in-gta-online-[^"\']+)'
        r'["\']',
        flags=re.IGNORECASE,
    )

    matches = pattern.findall(
        listing_html
    )

    for href in matches:
        absolute = urljoin(
            IGTA_NEWS_URL,
            href,
        )

        if (
            "/this-week-in-gta-online-"
            in absolute.lower()
        ):
            return absolute

    return None


def fetch_igrandtheftauto():
    expected_url = (
        make_expected_igta_url()
    )

    print(
        "iGrandTheftAuto:"
    )
    print(
        "Versuche aktuellen Wochenartikel:"
    )
    print(
        expected_url
    )

    try:
        article_html = fetch_url(
            expected_url
        )

        if (
            "This Week in GTA Online:"
            in article_html
        ):
            print(
                "Aktueller Wochenartikel "
                "direkt gefunden."
            )

            return (
                expected_url,
                article_html,
            )

    except Exception as error:
        print(
            "Direkter Abruf nicht möglich:"
        )
        print(error)

    print(
        "Suche aktuellen Wochenartikel "
        "über die GTA-Online-Newsseite ..."
    )

    listing_html = fetch_url(
        IGTA_NEWS_URL
    )

    article_url = (
        find_latest_weekly_article_from_listing(
            listing_html
        )
    )

    if not article_url:
        raise RuntimeError(
            "Kein aktueller iGrandTheftAuto-"
            "Wochenartikel gefunden."
        )

    print(
        "Gefundener Artikel:"
    )
    print(
        article_url
    )

    article_html = fetch_url(
        article_url
    )

    return (
        article_url,
        article_html,
    )


# ============================================================
# IGRANDTHEFTAUTO – STRUKTUR
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
    for name in possible_names:
        if name in sections:
            return sections[name]

    wanted = {
        clean_line(name).lower()
        for name in possible_names
    }

    for key, values in sections.items():
        if (
            clean_line(key).lower()
            in wanted
        ):
            return values

    return []


def find_entry(
    entries,
    predicate,
):
    for entry in entries:
        if predicate(
            entry["text"]
        ):
            return entry["text"]

    return ""


# ============================================================
# WOCHENSCHWERPUNKT
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
                and not text.startswith(
                    "Published "
                )
                and not text.startswith(
                    "Last Updated "
                )
            ):
                return text

    return ""


def extract_period_from_title(
    title
):
    match = re.search(
        r"This Week in GTA Online:\s*"
        r"([A-Za-z]+)\s+"
        r"(\d{1,2}),\s+"
        r"(\d{4})",
        title,
        flags=re.IGNORECASE,
    )

    if not match:
        return current_week_period()

    month_name = match.group(1)
    day = int(match.group(2))
    year = int(match.group(3))

    month_number = None

    for number, name in enumerate(
        MONTHS.keys(),
        start=1,
    ):
        if (
            name.lower()
            == month_name.lower()
        ):
            month_number = number
            break

    if month_number is None:
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
        if (
            search.lower()
            in combined.lower()
        ):
            return (
                headline,
                build_german_intro(
                    search,
                    intro,
                ),
            )

    # Fallback für neue Events
    # ohne harte Abhängigkeit von einem Namen.
    fallback = title

    fallback = re.sub(
        r"^This Week in GTA Online:\s*",
        "",
        fallback,
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
            None,
            intro,
        ),
    )


def build_german_intro(
    event_name,
    source_intro,
):
    if event_name == "Business Rivalries":
        return (
            "Die Geschäfte in Los Santos "
            "laufen auf Hochtouren. "
            "Diese Woche warten wieder "
            "erhöhte Auszahlungen, Rabatte "
            "und besondere Angebote auf euch."
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

    # Allgemeiner Fallback:
    # Die Einleitung bleibt kurz und wird
    # nicht als englischer Originaltext ausgegeben.
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

    # 2X / 3X / 4X / usw.
    match = re.match(
        r"^(\d+)X\s+"
        r"GTA\$\s*(?:and\s*RP|&\s*RP)"
        r"\s+on\s+(.+)$",
        line,
        flags=re.IGNORECASE,
    )

    if match:
        multiplier = match.group(1)
        activity = clean_line(
            match.group(2)
        )

        return (
            f"{multiplier}× GTA$ & RP – "
            f"{activity}"
        )

    match = re.match(
        r"^(\d+)X\s+GTA\$\s+on\s+(.+)$",
        line,
        flags=re.IGNORECASE,
    )

    if match:
        multiplier = match.group(1)
        activity = clean_line(
            match.group(2)
        )

        return (
            f"{multiplier}× GTA$ – "
            f"{activity}"
        )

    return line


def extract_bonuses(
    sections
):
    raw = get_section(
        sections,
        "Bonuses and Rewards",
        "Bonuses & Rewards",
        "Event Bonuses and Rewards",
    )

    results = []

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        # Der Satz über die Kleidung gehört
        # zu den Geschenken, nicht zu den Boni.
        if (
            "Deliver Business Battle crates"
            in line
        ):
            continue

        if (
            line.startswith(
                "Six Figure Tee"
            )
            or line.startswith(
                "Ride or Die Tee"
            )
            or line.startswith(
                "Red and White Ammu-Nation Cap"
            )
            or line.startswith(
                "Bourgeoix Tee"
            )
        ):
            continue

        translated = translate_bonus_line(
            line
        )

        if translated != line:
            results.append(
                translated
            )

    return unique_items(
        results
    )


# ============================================================
# GESCHENKE
# ============================================================

def extract_gifts(
    sections
):
    results = []

    bonuses = get_section(
        sections,
        "Bonuses and Rewards",
        "Bonuses & Rewards",
        "Event Bonuses and Rewards",
    )

    bonus_text = " ".join(
        bonuses
    )

    clothing_items = [
        "Six Figure Tee",
        "Ride or Die Tee",
        "Red and White Ammu-Nation Cap",
        "Bourgeoix Tee",
    ]

    for item in clothing_items:
        if item.lower() in bonus_text.lower():
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
    )

    if (
        "free Penaud La Coureuse"
        in free_text.lower()
    ):
        results.append(
            "Penaud La Coureuse – KOSTENLOS "
            "(bei erfüllter Voraussetzung)"
        )

    return unique_items(
        results
    )


# ============================================================
# RABATTE
# ============================================================

def extract_discounts(
    sections
):
    raw = get_section(
        sections,
        "Discounts",
    )

    results = []

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        # Kostenlos
        if re.search(
            r"\bfree\b",
            line,
            flags=re.IGNORECASE,
        ):
            name = re.sub(
                r"\s+[–-]\s*Free$",
                "",
                line,
                flags=re.IGNORECASE,
            )

            results.append(
                {
                    "name": clean_line(name),
                    "percentage": 100,
                    "free": True,
                }
            )

            continue

        # Prozent
        match = re.search(
            r"\s+[–-]\s*(\d+)%\s*off$",
            line,
            flags=re.IGNORECASE,
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

        results.append(
            {
                "name": clean_line(name),
                "percentage": percentage,
                "free": False,
            }
        )

    cleaned = []
    seen = set()

    for item in results:
        key = (
            item["name"].lower(),
            item["percentage"],
            item["free"],
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
# FAHRZEUGE
# ============================================================

def extract_vehicles(
    sections
):
    raw = get_section(
        sections,
        "Vehicles",
    )

    results = []

    current_subsection = ""

    for line in raw:
        line = clean_line(line)

        if not line:
            continue

        # Unterüberschriften
        if line.endswith(":"):
            current_subsection = line
            continue

        # Nur Fahrzeugzeilen.
        # Die Kategorie wird später vorangestellt.
        if (
            "(" in line
            and ")" in line
        ):
            results.append(
                {
                    "name": clean_line(
                        re.sub(
                            r"\s*\([^)]*\)",
                            "",
                            line,
                        )
                    ),
                    "category": current_subsection.rstrip(":"),
                }
            )

    # Doppelte Fahrzeuge entfernen
    cleaned = []
    seen = set()

    for item in results:
        key = item["name"].lower()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(item)

    return cleaned


def format_vehicle(
    vehicle
):
    name = vehicle["name"]
    category = vehicle.get(
        "category",
        "",
    )

    if (
        "Luxury Autos" in category
    ):
        return (
            f"🏎️ **{name}** "
            f"– Luxury Autos"
        )

    if (
        "Premium Deluxe" in category
    ):
        return (
            f"🚗 **{name}** "
            f"– Premium Deluxe Motorsport"
        )

    if (
        "Hao" in category
    ):
        return (
            f"🏎️ **{name}** "
            f"– HSW Test Ride"
        )

    if (
        "LS Car Meet" in category
    ):
        return (
            f"🚗 **{name}** "
            f"– LS Car Meet Test Ride"
        )

    if (
        "Lucky Wheel" in category
        or "Diamond Casino" in category
    ):
        return (
            f"🎰 **{name}** "
            f"– Glücksrad"
        )

    return (
        f"🚗 **{name}**"
    )


# ============================================================
# WOCHENHERAUSFORDERUNG
# ============================================================

def extract_weekly_challenge(
    sections
):
    challenges = get_section(
        sections,
        "Challenges",
    )

    if not challenges:
        return None

    result = []

    current_heading = ""

    for line in challenges:
        line = clean_line(line)

        if not line:
            continue

        if (
            line == "Weekly Challenge:"
            or line == "Weekly Challenge"
        ):
            current_heading = (
                "Weekly Challenge"
            )
            continue

        if current_heading == "Weekly Challenge":
            result.append(line)

            # Nur der erste relevante Satz
            break

    if not result:
        return None

    text = result[0]

    # Aktuelle Struktur:
    # Earn GTA$1,000,000 ... extra GTA$1,000,000
    # and the Junk Tracksuit
    #
    # Wir formulieren daraus eine kurze deutsche Version.
    if (
        "GTA$1,000,000"
        in text
        and "Junk Tracksuit"
        in text
    ):
        return (
            "GTA$1.000.000 durch den "
            "Verkauf aller Arten von Produkten "
            "verdienen – dazu gibt es "
            "weitere GTA$1.000.000 "
            "und den Junk Tracksuit."
        )

    return text


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

    # --------------------------------------------------------
    # 1. HEADER
    # --------------------------------------------------------
    message.append(
        "🗞️ **LS-INSIDER**"
    )
    message.append("")
    message.append(
        "# **DIESE WOCHE IN LOS SANTOS**"
    )
    message.append("")
    message.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )
    message.append("")

    # --------------------------------------------------------
    # 2. WOCHENSCHWERPUNKT
    # --------------------------------------------------------
    message.append(
        "### 📰 **WOCHENSCHWERPUNKT**"
    )
    message.append("")

    message.append(
        f"**{headline}**"
    )
    message.append("")

    # Zeitraum DIREKT unter der Überschrift
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

    # --------------------------------------------------------
    # 4. BONI
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # 5. FAHRZEUGE
    # --------------------------------------------------------
    if vehicles:
        message.append(
            "### 🚗 **FAHRZEUGE**"
        )
        message.append("")

        for vehicle in vehicles:
            message.append(
                format_vehicle(
                    vehicle
                )
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # --------------------------------------------------------
    # 6. SONDERANGEBOTE
    # --------------------------------------------------------
    paid_discounts = [
        item
        for item in discounts
        if not item.get("free")
    ]

    free_discounts = [
        item
        for item in discounts
        if item.get("free")
    ]

    if discounts:
        message.append(
            "### 🏷️ **SONDERANGEBOTE**"
        )
        message.append("")

        if paid_discounts:
            maximum = max(
                item["percentage"]
                for item in paid_discounts
            )

            message.append(
                f"**Bis zu {maximum} % Rabatt**"
            )
            message.append("")

            grouped = {}

            for item in paid_discounts:
                grouped.setdefault(
                    item["percentage"],
                    [],
                ).append(
                    item["name"]
                )

            for percentage in sorted(
                grouped.keys(),
                reverse=True,
            ):
                names = unique_items(
                    grouped[percentage]
                )

                for name in names:
                    message.append(
                        f"🔻 **{name}** "
                        f"– {percentage} %"
                    )

        if free_discounts:
            if paid_discounts:
                message.append("")

            for item in free_discounts:
                message.append(
                    f"🆓 **{item['name']}** "
                    "– KOSTENLOS"
                )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # --------------------------------------------------------
    # 7. GESCHENKE
    # --------------------------------------------------------
    if gifts:
        message.append(
            "### 🎁 **GESCHENKE**"
        )
        message.append("")

        for gift in gifts:
            message.append(
                f"🎁 {gift}"
            )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # --------------------------------------------------------
    # 8. WOCHENHERAUSFORDERUNG
    # --------------------------------------------------------
    if challenge:
        message.append(
            "### 🏆 **WOCHENHERAUSFORDERUNG**"
        )
        message.append("")

        message.append(
            f"🏆 {challenge}"
        )

        message.append("")
        message.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )
        message.append("")

    # --------------------------------------------------------
    # 9. QUELLE
    # --------------------------------------------------------
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
        await asyncio.to_thread(
            fetch_igrandtheftauto
        )
    )

    if not article_html:
        raise RuntimeError(
            "iGrandTheftAuto konnte "
            "nicht gelesen werden."
        )

    entries = parse_article_html(
        article_html
    )

    if not entries:
        raise RuntimeError(
            "Der iGrandTheftAuto-Artikel "
            "konnte nicht strukturiert "
            "ausgelesen werden."
        )

    sections = entries_to_sections(
        entries
    )

    title = extract_article_title(
        entries
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
    # DOPPLUNGEN ENTFERNEN
    # --------------------------------------------------------

    discount_names = {
        item["name"].lower()
        for item in discounts
    }

    # Fahrzeuge, die ohnehin nur als Rabatt
    # auftauchen, werden aus dem Fahrzeugblock
    # entfernt. So erscheint z.B. kein Auto zweimal.
    vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle["name"].lower()
        not in discount_names
    ]

    # Falls ein kostenloses Fahrzeug gleichzeitig
    # im Rabattbereich auftaucht, gehört es zu Geschenke.
    free_vehicle_names = {
        item["name"].lower()
        for item in discounts
        if item.get("free")
    }

    vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle["name"].lower()
        not in free_vehicle_names
    ]

    print()
    print(
        "MITTWOCH ERKANNT:"
    )
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
        "Einleitung:",
        german_intro,
    )

    print()
    print("BONI:")

    for item in bonuses:
        print(
            " -",
            item,
        )

    print()
    print("FAHRZEUGE:")

    for item in vehicles:
        print(
            " -",
            format_vehicle(item),
        )

    print()
    print("SONDERANGEBOTE:")

    for item in discounts:
        print(
            f" - {item['percentage']} % | "
            f"{item['name']}"
        )

    print()
    print("GESCHENKE:")

    for item in gifts:
        print(
            " -",
            item,
        )

    print()
    print("WOCHENHERAUSFORDERUNG:")

    print(
        " -",
        challenge,
    )

    # --------------------------------------------------------
    # DESIGN
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SIGNATUR
    # --------------------------------------------------------

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
        print()
        print(
            "Diese Wochenmeldung wurde "
            "bereits gepostet."
        )
        print(
            "Kein doppelter Mittwoch-Post."
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
# DONNERSTAG – GEHEIMBERICHT
# ============================================================

async def fetch_rockstar_news():
    async with playwright_context() as context:

        page = await context.new_page(
            viewport={
                "width": 1440,
                "height": 2400,
            },
            locale="de-DE",
        )

        try:
            await page.goto(
                ROCKSTAR_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                7000
            )

            # ------------------------------------------------
            # Cookie-Dialog
            # ------------------------------------------------
            try:
                buttons = page.locator(
                    "button"
                )

                for i in range(
                    await buttons.count()
                ):
                    button = buttons.nth(i)

                    try:
                        button_text = (
                            await button.inner_text()
                        )

                        lower = (
                            button_text.lower()
                        )

                        if any(
                            word in lower
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

            # ------------------------------------------------
            # Article-Elemente
            # ------------------------------------------------
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

                    link_locator = article.locator(
                        "a[href]"
                    )

                    if (
                        await link_locator.count()
                        == 0
                    ):
                        continue

                    href = await (
                        link_locator.first
                        .get_attribute("href")
                    )

                    if not href:
                        continue

                    if href.startswith("/"):
                        href = (
                            "https://www.rockstargames.com"
                            + href
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

                        if (
                            await headings.count()
                            > 0
                        ):
                            title = clean_line(
                                await headings.first.inner_text()
                            )

                            if title:
                                break

                    if not title:
                        lines = [
                            clean_line(x)
                            for x in text.splitlines()
                            if clean_line(x)
                        ]

                        for line in lines:
                            if (
                                len(line) >= 20
                                and not line.lower().startswith(
                                    "rockstar games"
                                )
                            ):
                                title = line
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

            # ------------------------------------------------
            # Fallback: Links
            # ------------------------------------------------
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

                        if href.startswith("/"):
                            href = (
                                "https://www.rockstargames.com"
                                + href
                            )

                        if (
                            "/newswire/" in href.lower()
                            or "/newswire/article/"
                            in href.lower()
                        ):
                            articles.append(
                                {
                                    "title": title,
                                    "url": href,
                                    "text": title,
                                }
                            )

                    except Exception:
                        continue

            # ------------------------------------------------
            # GTA-Online-Filter
            # ------------------------------------------------

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
                    article.get(
                        "title",
                        "",
                    )
                )

                text = clean_text(
                    article.get(
                        "text",
                        "",
                    )
                )

                combined = (
                    f"{title}\n{text}"
                ).lower()

                is_gta_online = (
                    "gta online"
                    in combined
                    or
                    "grand theft auto online"
                    in combined
                )

                if not is_gta_online:
                    continue

                if any(
                    word in title.lower()
                    for word in excluded_words
                ):
                    continue

                filtered.append(
                    article
                )

            # ------------------------------------------------
            # Doppelte URLs
            # ------------------------------------------------

            result = []
            seen = set()

            for article in filtered:
                url = article.get(
                    "url",
                    "",
                )

                if not url:
                    continue

                if url in seen:
                    continue

                seen.add(url)

                result.append(
                    article
                )

            print(
                "Relevante GTA-Online-News "
                f"gefunden: {len(result)}"
            )

            return result

        finally:
            await context.browser.close()


class playwright_context:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.browser_context = None

    async def __aenter__(self):
        self.playwright = (
            await async_playwright()
            .start()
        )

        self.browser = (
            await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features="
                    "AutomationControlled"
                ],
            )
        )

        self.browser_context = (
            await self.browser.new_context(
                viewport={
                    "width": 1440,
                    "height": 2400,
                },
                locale="de-DE",
            )
        )

        self.new_page = (
            self.browser_context.new_page
        )

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        if self.browser_context:
            await self.browser_context.close()

        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()


# ============================================================
# DONNERSTAG – FILTER
# ============================================================

def is_relevant_gta_online_article(
    article
):
    title = clean_line(
        article.get(
            "title",
            "",
        )
    )

    text = clean_text(
        article.get(
            "text",
            "",
        )
    )

    combined = (
        f"{title}\n{text}"
    ).lower()

    if (
        "gta online"
        not in combined
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

    title_lower = title.lower()

    if any(
        word in title_lower
        for word in excluded
    ):
        return False

    return True


def article_signature(
    article
):
    return make_signature(
        article.get(
            "title",
            "",
        ),
        article.get(
            "url",
            "",
        ),
    )


# ============================================================
# DONNERSTAG – KATEGORIEN
# ============================================================

def detect_thursday_categories(
    article
):
    title = clean_line(
        article.get(
            "title",
            "",
        )
    )

    text = clean_text(
        article.get(
            "text",
            "",
        )
    )

    combined = (
        f"{title}\n{text}"
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

    vehicle_words = [
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

    if any(
        word in combined
        for word in vehicle_words
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

def build_thursday_message(
    article
):
    title = clean_line(
        article.get(
            "title",
            "",
        )
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

    if categories:
        for category in categories:
            message.append(
                category
            )

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

    # --------------------------------------------------------
    # TEST
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # NORMALBETRIEB
    # --------------------------------------------------------

    state = load_state()

    if (
        state.get(
            "thursday_signature"
        )
        == signature
    ):
        print()
        print(
            "Diese GTA-Online-Meldung wurde "
            "bereits gepostet."
        )
        print(
            "Kein doppelter Donnerstag-Post."
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
