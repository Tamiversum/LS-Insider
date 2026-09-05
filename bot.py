import os
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# Hier werden später die GTA-News/Daten verarbeitet.
# Diese Funktion übersetzt den Text ins Deutsche.
def translate_to_german(text):
    # Platzhalter – die Übersetzung bauen wir im nächsten Schritt ein.
    return text


def send_to_discord(message):
    data = {
        "content": message
    }

    response = requests.post(WEBHOOK_URL, json=data)

    if response.status_code not in (200, 204):
        raise Exception(
            f"Discord-Webhook Fehler: {response.status_code} {response.text}"
        )


def main():
    # Testnachricht
    message = "🚗 **LS-Insider – GTA Eventwoche**\n\nDer Bot ist erfolgreich eingerichtet! 🎉"

    german_message = translate_to_german(message)

    send_to_discord(german_message)


if __name__ == "__main__":
    main()
