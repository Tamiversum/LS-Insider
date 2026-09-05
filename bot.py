import os
import requests
import json

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

GRAPHQL_URL = "https://graph.rockstargames.com/"

QUERY = """
query NewswireList($locale: String!, $index: Boolean!) {
    newswire(locale: $locale, index: $index) {
        results {
            title
            url
        }
    }
}
"""


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
    response = requests.post(
        GRAPHQL_URL,
        json={
            "query": QUERY,
            "variables": {
                "locale": "de",
                "index": True
            }
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://www.rockstargames.com",
            "Referer": "https://www.rockstargames.com/"
        }
    )

    message = (
        "🔍 **LS-Insider – GraphQL-Test**\n\n"
        f"HTTP-Status: `{response.status_code}`\n\n"
        f"Antwort:\n```json\n"
        f"{response.text[:1500]}\n"
        f"```"
    )

    send_to_discord(message)


if __name__ == "__main__":
    main()
