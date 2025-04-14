import os
import requests
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = "Транзакции"
AIRTABLE_FIELD_NAME = "ВИД"

user_states = {}

# 1. Вземаме опциите от колоната "ВИД"
def get_transaction_types():
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
                    return [opt["name"] for opt in field["options"]["choices"]]
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
            f"✅ Записано: {info}
📌 Вид: {selection}
(тук ще запишем в Airtable)"
        )
        # тук може да добавим логика за запис в Airtable
        # и да изчистим user_states[user_id]

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()
