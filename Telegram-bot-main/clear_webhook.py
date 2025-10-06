import requests
import os

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ Не е намерен токен! Увери се, че TELEGRAM_BOT_TOKEN е в Secrets.")
    exit(1)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
response = requests.get(url)
print("📡 Отговор от Telegram:")
print(response.json())
