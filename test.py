import os
import requests
import base64

import re

TABLE_ACCOUNTS = "ВСИЧКИ АКАУНТИ"
TABLE_REPORTS = "Отчет Телеграм"

url_accounts = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{TABLE_ACCOUNTS}"
url_reports = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{TABLE_REPORTS}"

headers = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

def normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^a-zа-я0-9\s]', '', text)
    return text

def find_account(account_name):
    normalized_account_name = normalize_text(account_name)
    search_terms = normalized_account_name.strip().split()
    conditions = [f'SEARCH("{term}", LOWER({{REG}})) > 0' for term in search_terms]
    formula = f'AND({",".join(conditions)})'
    params = {"filterByFormula": formula}

    res = requests.get(url_accounts, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        records = data.get("records", [])
        if records:
            return records[0]["id"]
    return None

def parse_transaction(text):
    text = text.strip()
    text = text.replace('za', 'за').replace('ot', 'от')

    amount = None
    currency_code = None
    description = ""
    account_name = None
    is_expense = False

    pre_acc = text
    if re.search(r'(?i)\bот\b', text):
        parts = text.rsplit(" от ", 1)
        pre_acc = parts[0].strip()
        account_name = parts[1].strip()

    match = re.match(r"(\d+(?:[.,]\d{1,2})?)\s*(лв|лев|bgn)?\s*за\s*(.+)", pre_acc, re.IGNORECASE)
    if match:
        amount_str, currency, desc = match.groups()
        amount = float(amount_str.replace(",", "."))
        description = desc.strip()
        currency_code = currency.upper() if currency else "BGN"
        is_expense = True

    return amount, currency_code, description, account_name, is_expense

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = "Отчет Телеграм"
AIRTABLE_FIELD_NAME = "ВИД"

user_states = {}

# 1. Вземаме опциите от колоната "ВИД"
def get_transaction_types():
    print("🛠️ Влизаме в get_transaction_types()")
    url = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    tables = res.json()["tables"]
    for table in tables:
        if table["name"] == AIRTABLE_TABLE_NAME:
            for field in table["fields"]:
                if field["name"] == AIRTABLE_FIELD_NAME:
                    choices = [opt["name"] for opt in field["options"]["choices"]]
                    print("✅ Извлечени типове транзакции:", choices)
                    return choices
    print("⚠️ Не бяха намерени стойности за колоната 'ВИД'")
    return []

# 2. Строим клавиатура с опции + странициране
def build_keyboard(options, page=0, page_size=5):
    start = page * page_size
    end = start + page_size
    page_options = options[start:end]
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"select_{opt}")]
        for opt in page_options
    ]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page_{page-1}"))
    if end < len(options):
        nav_buttons.append(InlineKeyboardButton("➡️ Напред", callback_data=f"page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Здрасти! Въведи сума + акаунт.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    user_states[user_id] = {"entry": text}
    transaction_types = get_transaction_types()
    keyboard = build_keyboard(transaction_types)
    if not transaction_types:
        await update.message.reply_text("⚠️ Няма налични типове транзакции от Airtable.")
        return
    await update.message.reply_text("📌 За какъв вид транзакция се отнася?", reply_markup=keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("page_"):
        page = int(data.split("_")[1])
        transaction_types = get_transaction_types()
        keyboard = build_keyboard(transaction_types, page)
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data.startswith("select_"):
        selection = data.replace("select_", "")
        info = user_states.get(user_id, {}).get("entry", "няма сума/акаунт")
        await query.edit_message_text(
            f"✅ Записано: {info}\n📌 Вид: {selection}\n(тук ще запишем в Airtable)"

        )
        # тук може да добавим логика за запис в Airtable
        # и да изчистим user_states[user_id]

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()
