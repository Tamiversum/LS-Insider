import os
import re
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

ROCKSTAR_URL = "https://www.rockstargames.com/de/newswire?tag_id=735"


def send_to_discord(message):
    response = requests.post(
        WEBHOOK_URL,
        json={"content": message}
    )

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


def main():
    response = requests.get(
        ROCKSTAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml"
        }
    )

    html = response.text

    scripts = re.findall(
        r'<script[^>]+src=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )

    message = (
        "🔎 **LS-Insider – Rockstar Script-Diagnose**\n\n"
        f"HTTP-Status: `{response.status_code}`\n"
        f"Gefundene JavaScript-Dateien: `{len(scripts)}`\n\n"
    )

    for script in scripts[:10]:
        message += f"• `{script}`\n"

    send_to_discord(message)


if __name__ == "__main__":
    main()
