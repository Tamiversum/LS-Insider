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

GTABASE_URL = (
    "https://www.gtabase.com/gta-online/"
    "weekly-update-bonuses-discounts"
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
#   Mittwoch und Donnerstag werden bei jedem manuellen Lauf
#   einmal zu Testzwecken gesendet.
#
#   Der gespeicherte State wird dabei NICHT verändert.
#
# False:
#   Normalbetrieb. Bereits gepostete Inhalte werden übersprungen.
TEST_MODE = True

DISCORD_LIMIT = 1900


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)

    return text.strip()


def clean_line(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


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
# MONATE / DATUM
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


def extract_period(text):
    if not text:
        return None

    patterns = [
        (
            r"from\s+"
            r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})"
            r"\s+to\s+"
            r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})"
        ),
        (
            r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})"
            r"\s*[–-]\s*"
            r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
        )

        if match:
            (
                month1,
                day1,
                year1,
                month2,
                day2,
                year2,
            ) = match.groups()

            return (
                f"{day1}. "
                f"{german_month(month1)} "
                f"{year1} – "
                f"{day2}. "
                f"{german_month(month2)} "
                f"{year2}"
            )

    return None


def current_week_period():
    now = datetime.now(VIENNA).date()

    # Donnerstag = Beginn der GTA-Online-Woche
    days_since_thursday = (
        now.weekday() - 3
    ) % 7

    start = now.fromordinal(
        now.toordinal()
        - days_since_thursday
    )

    end = start.fromordinal(
        start.toordinal() + 6
    )

    return (
        f"{start.day:02d}."
        f"{start.month:02d}."
        f"{start.year}"
        f" – "
        f"{end.day:02d}."
        f"{end.month:02d}."
        f"{end.year}"
    )


# ============================================================
# PLAYWRIGHT – GTABASE
# ============================================================

async def fetch_gtabase():
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 2400,
            },
            locale="en-US",
        )

        try:
            await page.goto(
                GTABASE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(
                5000
            )

            text = await page.locator(
                "body"
            ).inner_text()

            text = clean_text(text)

            print(
                f"GTABase geladen: "
                f"{len(text)} Zeichen"
            )

            return text

        finally:
            await browser.close()


# ============================================================
# PLAYWRIGHT – ROCKSTAR
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
                            button_text
                            .lower()
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
            # Zuerst echte Article-Elemente
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

                    if await link_locator.count() == 0:
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
                                and not line.lower()
                                .startswith("rockstar games")
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
            # Fallback: Links durchsuchen
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
                        href = await link.get_attribute(
                            "href"
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
                            or "/newswire/article/" in href.lower()
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

                # GTA Online muss eindeutig vorkommen
                is_gta_online = (
                    "gta online" in combined
                    or "grand theft auto online"
                    in combined
                )

                if not is_gta_online:
                    continue

                # GTA VI / andere Spiele ausschließen
                if any(
                    word in title.lower()
                    for word in excluded_words
                ):
                    continue

                filtered.append(
                    article
                )

            # ------------------------------------------------
            # Doppelte URLs entfernen
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

    async def __aenter__(self):
        self.playwright = (
            await async_playwright().start()
        )

        self.browser = (
            await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled"
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
        await self.browser_context.close()
        await self.browser.close()
        await self.playwright.stop()


# ============================================================
# GTABASE – SECTIONS
# ============================================================

def lines_from_text(text):
    return [
        clean_line(line)
        for line in text.splitlines()
        if clean_line(line)
    ]


def find_line(lines, target):
    target = target.lower()

    for index, line in enumerate(lines):
        if line.lower() == target:
            return index

    return -1


def section_between(
    lines,
    start_names,
    end_names,
):
    start = -1

    for name in start_names:
        start = find_line(
            lines,
            name,
        )

        if start != -1:
            break

    if start == -1:
        return []

    end = len(lines)

    for name in end_names:
        position = find_line(
            lines[start + 1:],
            name,
        )

        if position != -1:
            absolute = (
                start
                + 1
                + position
            )

            if absolute < end:
                end = absolute

    return lines[
        start + 1:end
    ]


# ============================================================
# BONI
# ============================================================

def is_multiplier(line):
    return bool(
        re.fullmatch(
            r"\d+(?:\.\d+)?x",
            line,
            flags=re.IGNORECASE,
        )
    )


def extract_bonuses(lines):
    section = section_between(
        lines,
        [
            "GTA$ & RP Bonuses",
            "GTA$ & RP Bonuses This Week",
        ],
        [
            "In-Game Discounts",
            "Showrooms & Test Rides",
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    results = []

    pending = None

    for line in section:

        if is_multiplier(line):
            if pending:
                results.append(
                    f"{pending} – {line}"
                )

            continue

        if re.fullmatch(
            r"\d+",
            line,
        ):
            continue

        if "$" in line:
            continue

        lower = line.lower()

        if lower in {
            "bonus",
            "bonuses",
            "gta$ & rp bonuses",
        }:
            continue

        if len(line) < 2:
            continue

        pending = line

    return unique_items(
        results
    )


# ============================================================
# RABATTE
# ============================================================

def is_price_line(line):
    return bool(
        re.search(
            r"\$[\d,]+",
            line,
        )
    )


def is_discount_line(line):
    return bool(
        re.search(
            r"-\d+%",
            line,
        )
        or re.search(
            r"\bfree\b",
            line,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\bkostelos\b",
            line,
            flags=re.IGNORECASE,
        )
    )


def is_noise(line):
    lower = line.lower()

    noise_words = [
        "remove ads",
        "vip membership",
        "support us",
        "advertisement",
        "cookie",
        "sign in",
        "login",
        "mygta",
        "loading",
    ]

    return any(
        word in lower
        for word in noise_words
    )


def extract_discounts(lines):
    section = section_between(
        lines,
        ["In-Game Discounts"],
        [
            "Showrooms & Test Rides",
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    results = []

    pending_name = None

    for line in section:

        if is_noise(line):
            continue

        # ----------------------------------------------------
        # Rabatt
        # ----------------------------------------------------
        match = re.search(
            r"-(\d+)%",
            line,
        )

        if match:
            percentage = int(
                match.group(1)
            )

            if pending_name:
                results.append(
                    {
                        "name": pending_name,
                        "percentage": percentage,
                        "free": False,
                    }
                )

            continue

        # ----------------------------------------------------
        # Kostenlos
        # ----------------------------------------------------
        if (
            re.search(
                r"\bfree\b",
                line,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"\bkostelos\b",
                line,
                flags=re.IGNORECASE,
            )
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

        # ----------------------------------------------------
        # Preiszeilen
        # ----------------------------------------------------
        if is_price_line(line):
            continue

        if line.startswith("~~"):
            continue

        if len(line) < 2:
            continue

        # ----------------------------------------------------
        # Nicht als Name verwenden:
        # ----------------------------------------------------
        lower = line.lower()

        if lower in {
            "vehicles",
            "properties",
            "weapons",
            "clothing",
            "other",
            "in-game discounts",
        }:
            continue

        pending_name = line

    # --------------------------------------------------------
    # Doppelte Rabattzeilen entfernen
    # --------------------------------------------------------
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
# KOSTENLOSE INHALTE
# ============================================================

def extract_free_items(
    lines,
    discounts,
):
    results = []

    # Gratis-Inhalte aus Rabattbereich
    for item in discounts:
        if item.get("free"):
            results.append(
                f"{item['name']} – KOSTENLOS"
            )

    full_text = "\n".join(lines)

    # --------------------------------------------------------
    # Penaud La Coureuse
    # --------------------------------------------------------
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
    # Kleidung aus Business Battle
    # --------------------------------------------------------
    clothing = [
        "Six Figure Tee",
        "Ride or Die Tee",
        "Red and White Ammu-Nation Cap",
        "Bourgeoix Tee",
    ]

    for item in clothing:
        if item.lower() in full_text.lower():
            results.append(
                f"{item} – kostenlose Belohnung"
            )

    return unique_items(
        results
    )


# ============================================================
# FAHRZEUGE
# ============================================================

KNOWN_VEHICLES = [
    "Pegassi Horus",
    "Penaud La Coureuse",
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
]


def extract_new_vehicles(lines):
    full_text = "\n".join(lines)

    results = []

    # GTA+ Early Access
    if re.search(
        r"Pegassi Horus",
        full_text,
        flags=re.IGNORECASE,
    ):
        results.append(
            "Pegassi Horus"
        )

    # La Coureuse NICHT als neues Fahrzeug ausgeben,
    # wenn sie nur als zukünftige Gratis-Belohnung erwähnt wird.
    # Sie gehört dann in Geschenke.
    return unique_items(
        results
    )


def extract_showroom_vehicles(lines):
    section = section_between(
        lines,
        ["Showrooms & Test Rides"],
        [
            "Weekly Challenge",
            "This Week's Bonuses & Discounts",
        ],
    )

    if not section:
        return []

    text = "\n".join(section)

    results = []

    for vehicle in KNOWN_VEHICLES:
        if vehicle.lower() in text.lower():
            results.append(
                vehicle
            )

    return unique_items(
        results
    )


# ============================================================
# WEEKLY CHALLENGE
# ============================================================

def extract_weekly_challenge(lines):
    section = section_between(
        lines,
        ["Weekly Challenge"],
        [
            "This Week's Bonuses & Discounts",
        ],
    )

    if not section:
        return None

    text = clean_line(
        " ".join(section)
    )

    if not text:
        return None

    reward_parts = []

    if "$1,000,000" in text:
        reward_parts.append(
            "GTA$1.000.000"
        )

    if "Junk Tracksuit" in text:
        reward_parts.append(
            "Junk Tracksuit"
        )

    if reward_parts:
        reward = " + ".join(
            reward_parts
        )

        return (
            "GTA$1.000.000 durch den "
            "Verkauf aller Arten von "
            f"Produkten – Belohnung: {reward}"
        )

    return text


# ============================================================
# WEITERE WOCHENINHALTE
# ============================================================

def extract_other_content(lines):
    section = section_between(
        lines,
        [
            "This Week's Bonuses & Discounts"
        ],
        [],
    )

    if not section:
        return []

    text = "\n".join(section)

    results = []

    patterns = [
        (
            r"Kortz Center Heist",
            "Kortz Center Heist",
        ),
        (
            r"Salvage Yard Robberies",
            "Salvage Yard Robberies",
        ),
        (
            r"Premium Race",
            "Premium Race",
        ),
        (
            r"Time Trial",
            "Time Trial",
        ),
        (
            r"HSW Time Trial",
            "HSW Time Trial",
        ),
    ]

    for pattern, output in patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            results.append(
                output
            )

    return unique_items(
        results
    )


# ============================================================
# WOCHEN-HEADLINE
# ============================================================

def extract_weekly_headline(
    lines,
):
    full_text = "\n".join(lines)

    # Aktueller GTABase-Titel
    patterns = [
        r"GTA Online Weekly Update "
        r"\(September 10-16\): "
        r"Business Rivalries Event Continues",
        r"Business Rivalries Event Continues",
        r"Business Rivalries",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            full_text,
            flags=re.IGNORECASE,
        )

        if match:
            return (
                "BUSINESS RIVALRIES"
            )

    return "GTA ONLINE EVENTWOCHE"


# ============================================================
# MITTWOCH DESIGN
# ============================================================

def build_wednesday_message(
    period,
    headline,
    bonuses,
    new_vehicles,
    showroom_vehicles,
    discounts,
    free_items,
    challenge,
):
    message = []

    # --------------------------------------------------------
    # HEADER
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
        f"📅 **{period}**"
    )
    message.append("")
    message.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )
    message.append("")

    # --------------------------------------------------------
    # EVENT
    # --------------------------------------------------------
    message.append(
        f"### **{headline}**"
    )
    message.append("")

    # --------------------------------------------------------
    # GROSSES GESCHÄFT
    # --------------------------------------------------------
    if bonuses:
        message.append(
            "### 💰 **DAS GROSSE GESCHÄFT**"
        )

        # Wichtig:
        # Der erste gefundene Bonus wird als Hauptbonus
        # verwendet.
        first_bonus = bonuses[0]

        match = re.search(
            r"(.+?)\s*[–-]\s*(\d+(?:\.\d+)?x)",
            first_bonus,
        )

        if match:
            activity = clean_line(
                match.group(1)
            )
            multiplier = match.group(2)

            message.append(
                f"**{multiplier.upper()} GTA$ & RP**"
            )
            message.append("")
            message.append(
                f"Auf **{activity}** warten "
                "diese Woche erhöhte Belohnungen."
            )

        else:
            message.append(
                f"**{first_bonus}**"
            )

        remaining = bonuses[1:]

        if remaining:
            message.append("")
            message.append(
                "**Weitere Boni:**"
            )

            for bonus in remaining:
                message.append(
                    f"• {bonus}"
                )

        message.append("")
        message.append("---")
        message.append("")

    # --------------------------------------------------------
    # FAHRZEUGE
    # --------------------------------------------------------
    if new_vehicles:
        message.append(
            "### 🚗 **NEU AUF DEN STRASSEN**"
        )
        message.append("")
        message.append(
            "**Neues Fahrzeug:**"
        )

        for vehicle in new_vehicles:
            message.append(
                f"🏎️ **{vehicle}**"
            )

        if showroom_vehicles:
            message.append("")
            message.append(
                "Auch weitere Fahrzeuge stehen "
                "diese Woche bei den Händlern "
                "und Showrooms bereit."
            )

        message.append("")
        message.append("---")
        message.append("")

    elif showroom_vehicles:
        message.append(
            "### 🚗 **FAHRZEUGE DIESER WOCHE**"
        )
        message.append("")

        for vehicle in showroom_vehicles:
            message.append(
                f"🚗 **{vehicle}**"
            )

        message.append("")
        message.append("---")
        message.append("")

    # --------------------------------------------------------
    # RABATTE
    # --------------------------------------------------------
    if discounts:
        # Nur echte Rabatte in diesem Abschnitt.
        # Kostenlose Dinge gehen zu Geschenke.
        paid_discounts = [
            item
            for item in discounts
            if not item.get("free")
        ]

        if paid_discounts:
            message.append(
                "### 🏷️ **SONDERANGEBOTE**"
            )
            message.append("")

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
                percentage = (
                    item["percentage"]
                )

                grouped.setdefault(
                    percentage,
                    [],
                ).append(
                    item["name"]
                )

            for percentage in sorted(
                grouped.keys(),
                reverse=True,
            ):
                for name in unique_items(
                    grouped[percentage]
                ):
                    message.append(
                        f"🔻 **{name}** "
                        f"– {percentage} %"
                    )

            message.append("")
            message.append("---")
            message.append("")

    # --------------------------------------------------------
    # GESCHENKE
    # --------------------------------------------------------
    if free_items:
        message.append(
            "### 🎁 **GESCHENKE FÜR DIE COMMUNITY**"
        )
        message.append("")
        message.append(
            "Diese Woche können sich Spieler "
            "über folgende kostenlose Inhalte freuen:"
        )
        message.append("")

        for item in free_items:
            message.append(
                f"🎁 {item}"
            )

        message.append("")
        message.append("---")
        message.append("")

    # --------------------------------------------------------
    # WEEKLY CHALLENGE
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
        message.append("---")
        message.append("")

    # --------------------------------------------------------
    # AKTUELLE WOCHE
    # --------------------------------------------------------
    message.append(
        "### 📅 **AKTUELLE WOCHE**"
    )
    message.append("")
    message.append(
        period
    )
    message.append("")
    message.append("---")
    message.append("")

    # --------------------------------------------------------
    # QUELLE
    # --------------------------------------------------------
    message.append(
        "**Quelle:** GTABase"
    )
    message.append(
        "🔗 **Vollständigen Artikel lesen**"
    )
    message.append(
        GTABASE_URL
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

    text = await fetch_gtabase()

    if not text:
        raise RuntimeError(
            "GTABase konnte nicht gelesen werden."
        )

    lines = lines_from_text(
        text
    )

    print(
        f"GTABase-Zeilen: {len(lines)}"
    )

    period = (
        extract_period(text)
        or current_week_period()
    )

    headline = extract_weekly_headline(
        lines
    )

    bonuses = extract_bonuses(
        lines
    )

    discounts = extract_discounts(
        lines
    )

    free_items = extract_free_items(
        lines,
        discounts,
    )

    new_vehicles = extract_new_vehicles(
        lines
    )

    showroom_vehicles = (
        extract_showroom_vehicles(
            lines
        )
    )

    challenge = extract_weekly_challenge(
        lines
    )

    # --------------------------------------------------------
    # Doppelungen zwischen Fahrzeug-/Geschenke-Bereichen
    # --------------------------------------------------------
    free_lower = {
        item.lower()
        for item in free_items
    }

    new_vehicles = [
        vehicle
        for vehicle in new_vehicles
        if vehicle.lower()
        not in free_lower
    ]

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------
    print()
    print("MITTWOCH ERKANNT:")
    print(
        "Zeitraum:",
        period,
    )
    print(
        "Headline:",
        headline,
    )

    print()
    print("BONI:")
    for item in bonuses:
        print(
            " -",
            item,
        )

    print()
    print("RABATTE:")
    for item in discounts:
        print(
            f" - {item['percentage']} % | "
            f"{item['name']}"
        )

    print()
    print("KOSTENLOSE SACHEN:")
    for item in free_items:
        print(
            " -",
            item,
        )

    print()
    print("NEUE FAHRZEUGE:")
    for item in new_vehicles:
        print(
            " -",
            item,
        )

    print()
    print("SHOWROOM-FAHRZEUGE:")
    for item in showroom_vehicles:
        print(
            " -",
            item,
        )

    print()
    print("WEEKLY CHALLENGE:")
    print(
        " -",
        challenge,
    )

    # --------------------------------------------------------
    # DESIGN ERSTELLEN
    # --------------------------------------------------------
    message = build_wednesday_message(
        period=period,
        headline=headline,
        bonuses=bonuses,
        new_vehicles=new_vehicles,
        showroom_vehicles=showroom_vehicles,
        discounts=discounts,
        free_items=free_items,
        challenge=challenge,
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
    # Signatur
    # --------------------------------------------------------
    signature = make_signature(
        period,
        headline,
        bonuses,
        new_vehicles,
        showroom_vehicles,
        discounts,
        free_items,
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
            "Mittwoch wird zu Testzwecken gesendet."
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
            "Diese Wochenmeldung wurde bereits "
            "gepostet."
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
        and "grand theft auto online"
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


def article_signature(article):
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

    # --------------------------------------------------------
    # Neu
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # Änderung
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # Bonus
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # Fahrzeug
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # Belohnung
    # --------------------------------------------------------
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
        "Die aktuelle Rockstar-Meldung wurde "
        "überprüft und ist für den LS-Insider "
        "eingetroffen."
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
            "Keine relevante GTA-Online-Meldung gefunden."
        )

        return

    # Der erste Treffer ist die neueste passende
    # GTA-Online-Meldung.
    article = relevant[0]

    print()
    print(
        "Neueste relevante GTA-Online-Meldung:"
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
            "Donnerstag wird zu Testzwecken gesendet."
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
