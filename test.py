import os
import re
import requests
from datetime import datetime, timedelta
import telebot
from telebot import types
import time
import threading
import functools
from collections import OrderedDict


# Validate credentials on startup
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIRTABLE_PERSONAL_ACCESS_TOKEN = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

if not TELEGRAM_BOT_TOKEN or not AIRTABLE_PERSONAL_ACCESS_TOKEN or not AIRTABLE_BASE_ID:
    raise ValueError("❌ Missing required environment variables: TELEGRAM_BOT_TOKEN, AIRTABLE_PERSONAL_ACCESS_TOKEN, AIRTABLE_BASE_ID")

TABLE_ACCOUNTS = "ВСИЧКИ АКАУНТИ"
TABLE_REPORTS = "Отчет Телеграм"
TABLE_TRANSACTION_TYPES = "ВИД ТРАНЗАКЦИЯ"

# Подготовка на URL и headers за Airtable API
url_accounts = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{TABLE_ACCOUNTS}"
url_reports = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{TABLE_REPORTS}"
headers = {
    "Authorization": f"Bearer {AIRTABLE_PERSONAL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# Инициализиране на Telegram бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Constants for memory limits
MAX_USER_RECORDS = 10
MAX_USERS_IN_MEMORY = 100
MAX_STATE_AGE_MINUTES = 30

# LRU cache implementation for user data
class LRUCache(OrderedDict):
    def __init__(self, maxsize=128):
        self.maxsize = maxsize
        super().__init__()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]

# Словар за запазване на всички записи на потребителя с ограничен размер
user_records = LRUCache(maxsize=MAX_USERS_IN_MEMORY)
user_pending_type = LRUCache(maxsize=MAX_USERS_IN_MEMORY)
pending_transaction_data = LRUCache(maxsize=MAX_USERS_IN_MEMORY)
user_editing = LRUCache(maxsize=MAX_USERS_IN_MEMORY)

# State timestamps for cleanup
user_state_timestamps = LRUCache(maxsize=MAX_USERS_IN_MEMORY)

# Rate limiting
class RateLimiter:
    def __init__(self, max_requests=30, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = LRUCache(maxsize=MAX_USERS_IN_MEMORY)

    def is_allowed(self, user_id):
        now = datetime.now()
        if user_id not in self.requests:
            self.requests[user_id] = []

        # Remove old requests
        self.requests[user_id] = [ts for ts in self.requests[user_id] if (now - ts).total_seconds() < self.time_window]

        if len(self.requests[user_id]) >= self.max_requests:
            return False

        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter()

# Cleanup thread reference
cleanup_thread = None

# Функция за изчистване на стари user states (предотвратява memory leaks)
def cleanup_old_user_data():
    """Изчиства user data по-стари от 30 минути"""
    global cleanup_thread

    try:
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=MAX_STATE_AGE_MINUTES)

        # Изчистваме стари записи
        for user_id in list(user_records.keys()):
            if isinstance(user_records[user_id], list) and len(user_records[user_id]) > MAX_USER_RECORDS:
                user_records[user_id] = user_records[user_id][-MAX_USER_RECORDS:]

        # Изчистваме pending states по timestamps
        for user_id in list(user_state_timestamps.keys()):
            if user_state_timestamps[user_id] < cutoff_time:
                user_state_timestamps.pop(user_id, None)
                pending_transaction_data.pop(user_id, None)
                user_pending_type.pop(user_id, None)
                user_editing.pop(user_id, None)

    except Exception as e:
        print(f"❌ Грешка при cleanup: {e}")
    finally:
        # Повтаряме всеки 30 минути
        cleanup_thread = threading.Timer(1800, cleanup_old_user_data)
        cleanup_thread.daemon = True
        cleanup_thread.start()

# Стартираме cleanup thread
cleanup_old_user_data()

def normalize_text(text):
    """Привежда текста в малки букви и премахва специални символи."""
    if not text or not isinstance(text, str):
        return ""

    # Ограничаваме дължината
    text = text[:500]
    text = text.lower()
    text = re.sub(r'[^a-zа-я0-9\s]', '', text)
    return text

def find_account(account_name):
    """Търси акаунт по ключови думи, независимо от големи/малки букви и тирета."""
    if not account_name or not isinstance(account_name, str):
        return None

    try:
        # Нормализиране на акаунта
        normalized_account_name = normalize_text(account_name)
        if not normalized_account_name:
            return None

        # Разделяме нормализирания акаунт на ключови думи
        search_terms = normalized_account_name.strip().split()
        if not search_terms or len(search_terms) > 10:  # Ограничаваме броя термини
            return None

        # Изграждаме filterByFormula с AND за търсене на всички ключови думи
        conditions = [f'SEARCH("{term[:50]}", LOWER({{REG}})) > 0' for term in search_terms]
        formula = f'AND({",".join(conditions)})'
        params = {"filterByFormula": formula}

        # Изпращаме заявка към Airtable API
        res = requests.get(url_accounts, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            records = data.get("records", [])
            if records and len(records) > 0:
                account_id = records[0]["id"]  # Вземаме ID на първия съвпаднал акаунт
                return account_id
    except requests.exceptions.Timeout:
        print(f"⏱️ Timeout при търсене на акаунт: {account_name}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Грешка при търсене на акаунт: {e}")
    except Exception as e:
        print(f"❌ Неочаквана грешка в find_account: {e}")
    return None

def get_user_records_from_airtable(user_name):
    """Извлича записите от последните 60 минути от Airtable за конкретен потребител."""
    if not user_name or not isinstance(user_name, str):
        return []

    try:
        now = datetime.now()
        one_hour_ago = now - timedelta(minutes=60)
        now_iso = now.isoformat()
        hour_ago_iso = one_hour_ago.isoformat()

        # Escape single quotes in user_name
        safe_user_name = user_name.replace("'", "\\'")[:100]

        # Airtable filterByFormula търси по Име на потребителя и Дата (ISO формат)
        formula = (
            f"AND("
            f"{{Име на потребителя}} = '{safe_user_name}',"
            f"IS_AFTER({{Дата}}, '{hour_ago_iso}')"
            f")"
        )

        params = {"filterByFormula": formula}
        res = requests.get(url_reports, headers=headers, params=params, timeout=10)

        if res.status_code == 200:
            data = res.json()
            records = data.get("records", [])
            return records[:50]  # Ограничаваме броя записи
        else:
            print(f"❌ Грешка при извличане на записи: {res.status_code}")
            return []
    except requests.exceptions.Timeout:
        print(f"⏱️ Timeout при извличане на записи за: {user_name}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"❌ Грешка при връзка с Airtable: {e}")
        return []
    except Exception as e:
        print(f"❌ Неочаквана грешка в get_user_records_from_airtable: {e}")
        return []

def parse_transaction(text):
    """
    Парсване на съобщение от вида "<сума> <валута> за <описание> от <акаунт>".
    Връща кортеж (amount, currency_code, description, account_name, is_expense).
    """
    # Input validation
    if not text or not isinstance(text, str):
        return None, None, "", None, False

    text = text.strip()

    # Ограничаваме дължината на input
    if len(text) > 500:
        return None, None, "", None, False

    # Разпознаване на латиница и кирилица за "за" и "от"
    text = text.replace('za', 'за').replace('ot', 'от')

    amount = None
    currency_code = None
    description = ""
    account_name = None
    is_expense = False  # Флаг, който показва дали е разход

    # Отделяне на сегмента с акаунта (ако има "от ..." или "from ...")
    pre_acc = text
    if re.search(r'(?i)\bот\b', text):
        parts = text.rsplit(" от ", 1)
        pre_acc = parts[0].strip()
        account_name = parts[1].strip() if len(parts) > 1 else ""
    elif re.search(r'(?i)\bfrom\b', text):
        parts = text.rsplit(" from ", 1)
        pre_acc = parts[0].strip()
        account_name = parts[1].strip() if len(parts) > 1 else ""

    # Отделяне на описанието (ако има "за ..." или "for ...")
    amount_currency_segment = pre_acc
    if re.search(r'(?i)\bза\b', pre_acc):
        parts = pre_acc.split(" за ", 1)  # разделя само при първото срещане на "за"
        amount_currency_segment = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
    elif re.search(r'(?i)\bfor\b', pre_acc):
        parts = pre_acc.split(" for ", 1)
        amount_currency_segment = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""

    # Определяне на разход или приход
    if re.search(r'(?i)\brazhod\b', description) or re.search(r'(?i)\bplateno\b', description):
        is_expense = True
    elif re.search(r'(?i)\bprihod\b', description) or re.search(r'(?i)\bpostuplenie\b', description):
        is_expense = False

    # Извличане на сумата и валутата от първия сегмент (напр. "100 лв." или "250 EUR")
    amount_str = ""
    currency_str = ""
    parts = amount_currency_segment.replace(",", ".").split()
    if len(parts) >= 2:
        amount_str = parts[0]
        currency_str = parts[1]
    else:
        m = re.match(r'^(\d+(?:\.\d+)?)(\D+)$', amount_currency_segment.replace(",", "."))
        if m:
            amount_str = m.group(1)
            currency_str = m.group(2)

    amount_str = amount_str.strip()
    currency_str = currency_str.strip().rstrip(".")
    if currency_str:
        cs = currency_str.lower()
        # Разпознаване на валути с латиница и кирилица
        if cs in ("лв", "lv", "лев", "лева", "bgn"):
            currency_code = "BGN"
        elif cs in ("eur", "€", "евро", "evro"):
            currency_code = "EUR"
        elif cs in ("gbp", "£", "паунд", "паунда", "paunda"):
            currency_code = "GBP"
        elif cs in ("usd", "$", "долар", "долара", "долари", "дол", "щ", "щатски", "щатски долари", "щатски долара", "долар сащ", "долара сащ", "американски долар", "американски долари"):
            currency_code = "USD"

    if amount_str:
        try:
            amount = float(amount_str)
        except ValueError:
            amount = None

    # Ако е разход, поставяме знак минус пред сумата
    if is_expense and amount is not None:
        amount = -amount

    return amount, currency_code, description, account_name, is_expense

def clean_string(s):
    """Премахва препинателни знаци и прави всичко малки букви."""
    return re.sub(r'[^\w\s]', '', s).lower()
# Cache for transaction types with TTL
transaction_types_cache = {"data": None, "timestamp": None, "ttl": 300}  # 5 minutes TTL (намален от 1 час)

def clear_transaction_types_cache():
    """Изчиства кеша на типовете транзакции."""
    global transaction_types_cache
    transaction_types_cache["data"] = None
    transaction_types_cache["timestamp"] = None
    # Clear lru_cache too
    get_transaction_types.cache_clear()
    print("🔄 Кешът на типовете транзакции е изчистен")

@functools.lru_cache(maxsize=1)
def get_transaction_types():
    """Извлича типовете транзакции с кеширане."""
    url_types = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/ВИД%20ТРАНЗАКЦИЯ"

    types_dict = {}

    try:
        # Check cache first
        now = datetime.now()
        if (transaction_types_cache["data"] is not None and
            transaction_types_cache["timestamp"] is not None and
            (now - transaction_types_cache["timestamp"]).total_seconds() < transaction_types_cache["ttl"]):
            return transaction_types_cache["data"]

        res = requests.get(url_types, headers=headers, timeout=10)

        if res.status_code == 200:
            data = res.json()
            for record in data.get("records", []):
                name = record["fields"].get("ТРАНЗАКЦИЯ")
                if name:
                    types_dict[name] = record["id"]

            # Update cache
            transaction_types_cache["data"] = types_dict
            transaction_types_cache["timestamp"] = now
            print(f"📦 Кешът е обновен с {len(types_dict)} типа транзакции")
        else:
            print(f"⚠️ Неуспешна заявка към Airtable: {res.status_code}")
    except requests.exceptions.Timeout:
        print(f"⏱️ Timeout при зареждане на типове транзакции")
    except requests.exceptions.RequestException as e:
        print(f"❌ Грешка при зареждане на типове транзакции: {e}")
    except Exception as e:
        print(f"❌ Неочаквана грешка в get_transaction_types: {e}")

    return types_dict

def get_transaction_type_options():
    """Извлича всички видове транзакции от таблицата 'ВИД ТРАНЗАКЦИЯ' - използва кеширането."""
    return get_transaction_types()

def handle_filter_input(message):
    keyword = message.text.strip().lower()
    user_id = message.chat.id

    all_types = get_transaction_types()
    filtered = {k: v for k, v in all_types.items() if keyword in k.lower()}

    if not filtered:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Опитай нова дума", callback_data="__filter"))
        markup.add(types.InlineKeyboardButton("📜 Покажи всички", callback_data="__reset"))

        bot.send_message(user_id, "❌ Няма резултати за тази дума. Опитай отново:", reply_markup=markup)
        return

    send_transaction_type_page(chat_id=user_id, page=0, filtered_types=filtered)

def send_transaction_type_page(chat_id, page=0, filtered_types=None):
    PAGE_SIZE = 20
    all_types = filtered_types if filtered_types is not None else get_transaction_types()
    sorted_keys = sorted(all_types.keys())
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    current_page_keys = sorted_keys[start:end]

    markup = types.InlineKeyboardMarkup(row_width=2)

    # 🔲 Добавяме бутоните по двойки
    for i in range(0, len(current_page_keys), 2):
        row_buttons = []
        for j in range(2):
            if i + j < len(current_page_keys):
                key = current_page_keys[i + j]
                row_buttons.append(types.InlineKeyboardButton(text=key, callback_data=key))
        markup.add(*row_buttons)

    # 🔄 Навигация (с емоджи)
    nav_buttons = []
    if end < len(sorted_keys):
        nav_buttons.append(types.InlineKeyboardButton("➡️ Напред", callback_data="__next"))
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Назад", callback_data="__prev"))
    if nav_buttons:
        markup.add(*nav_buttons)
        
    # 🔍 Филтър бутон със стил
    markup.add(types.InlineKeyboardButton("🔍 Въведи ключова дума 🔍", callback_data="__filter"))

    # 📬 Изпращане на съобщението
    msg = bot.send_message(chat_id, "📌 Моля, изберете ВИД на транзакцията:", reply_markup=markup)

    # 💾 Запазваме състоянието
    user_pending_type[chat_id] = {
        "msg_id": msg.message_id,
        "options": all_types,
        "page": page,
        "filtered": filtered_types,
        "selected": None
    }

@bot.message_handler(commands=['settype'])
def ask_transaction_type(message):
    send_transaction_type_page(chat_id=message.chat.id, page=0)

@bot.message_handler(commands=['refresh'])
def refresh_transaction_types(message):
    """Изчиства кеша и обновява типовете транзакции."""
    user_id = message.chat.id

    # Rate limiting
    if not rate_limiter.is_allowed(user_id):
        bot.reply_to(message, "⏸️ Твърде много заявки. Моля, изчакайте малко.")
        return

    try:
        clear_transaction_types_cache()
        # Force reload
        types = get_transaction_types()
        bot.reply_to(message, f"✅ Кешът е обновен! Намерени са {len(types)} типа транзакции.")
    except Exception as e:
        print(f"❌ Error in refresh_transaction_types: {e}")
        bot.reply_to(message, "❌ Грешка при обновяване на типовете транзакции.")

@bot.callback_query_handler(func=lambda call: True)
def handle_transaction_type_selection(call):
    user_id = None
    try:
        user_id = call.message.chat.id

        # Rate limiting
        if not rate_limiter.is_allowed(user_id):
            bot.answer_callback_query(call.id, "⏸️ Твърде много заявки.")
            return

        selected_label = call.data

        if user_id not in user_pending_type:
            bot.answer_callback_query(call.id, "❌ Няма очаквана транзакция.")
            return

        if call.data == "FILTER_BY_KEYWORD":
            bot.answer_callback_query(call.id)
            bot.send_message(user_id, "🔍 Въведи дума за филтриране:")
            bot.register_next_step_handler(call.message, show_filtered_transaction_types)
            return

        # 🔄 Навигация и филтриране
        if selected_label == "__prev":
            current_page = user_pending_type[user_id].get("page", 0)
            new_page = max(current_page - 1, 0)
            try:
                bot.delete_message(user_id, user_pending_type[user_id]["msg_id"])
            except Exception as e:
                print(f"⚠️ Cannot delete message: {e}")
            send_transaction_type_page(
                chat_id=user_id,
                page=new_page,
                filtered_types=user_pending_type[user_id].get("filtered")
            )
            return

        elif selected_label == "__next":
            current_page = user_pending_type[user_id].get("page", 0)
            new_page = current_page + 1
            try:
                bot.delete_message(user_id, user_pending_type[user_id]["msg_id"])
            except Exception as e:
                print(f"⚠️ Cannot delete message: {e}")
            send_transaction_type_page(
                chat_id=user_id,
                page=new_page,
                filtered_types=user_pending_type[user_id].get("filtered")
            )
            return

        elif selected_label == "__filter":
            bot.answer_callback_query(call.id)
            msg = bot.send_message(user_id, "🔍 Въведи дума за търсене:")
            bot.register_next_step_handler(msg, handle_filter_input)
            return

        elif selected_label == "__reset":
            bot.answer_callback_query(call.id)
            send_transaction_type_page(chat_id=user_id, page=0)
            return

        # ✅ Проверка дали е валиден тип
        user_options = user_pending_type[user_id].get("options", {})
        if selected_label not in user_options:
            bot.answer_callback_query(call.id, "❌ Невалиден избор.")
            return

        selected_id = user_options.get(selected_label)

        # 💾 Запази избора
        user_pending_type[user_id]["selected"] = selected_id
        user_pending_type[user_id]["selected_label"] = selected_label
        user_state_timestamps[user_id] = datetime.now()

        # ✅ Покажи избраното
        try:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=user_pending_type[user_id]["msg_id"],
                text=f"✅ Избра вид: {selected_label}"
            )
        except Exception as e:
            print(f"⚠️ Cannot edit message: {e}")

        # 📥 Ако има чакащи данни за транзакция — записваме в Airtable
        if user_id in pending_transaction_data:
            tx = pending_transaction_data[user_id]
            account_id = find_account(tx.get("account_name", ""))

            fields = {
                "Дата": tx.get("datetime", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "Описание": tx.get("description", "")[:500],
                "Име на потребителя": tx.get("user_name", "")[:100],
                "ВИД": [selected_id],
            }

            currency_code = tx.get("currency_code")
            amount = tx.get("amount")

            if currency_code == "BGN":
                fields["Сума (лв.)"] = amount
            elif currency_code == "EUR":
                fields["Сума (EUR)"] = amount
            elif currency_code == "GBP":
                fields["Сума (GBP)"] = amount
            elif currency_code == "USD":
                fields["Сума (USD)"] = amount

            if account_id:
                fields["Акаунт"] = [account_id]
            else:
                acc_name = tx.get("account_name", "")[:100]
                fields["Описание"] = f"{fields['Описание']} (Акаунт: {acc_name})"

            data = {"fields": fields}

            try:
                res_post = requests.post(url_reports, headers=headers, json=data, timeout=10)

                if res_post.status_code in (200, 201):
                    record_id = res_post.json().get("id")
                    if user_id not in user_records:
                        user_records[user_id] = []
                    user_records[user_id].append(record_id)

                    # Limit records per user
                    if len(user_records[user_id]) > MAX_USER_RECORDS:
                        user_records[user_id] = user_records[user_id][-MAX_USER_RECORDS:]

                    bot.send_message(user_id, f"✅ Избра вид: {selected_label}\n📌 Отчетът е записан успешно.")
                else:
                    print(f"❌ Airtable error: {res_post.status_code}")
                    bot.send_message(user_id, "❌ Грешка при записването в базата.")
            except requests.exceptions.Timeout:
                print(f"⏱️ Timeout при записване на транзакция")
                bot.send_message(user_id, "⏱️ Заявката отне твърде много време.")
            except requests.exceptions.RequestException as e:
                print(f"❌ Request error: {e}")
                bot.send_message(user_id, "❌ Грешка при връзка с базата.")

            # 🧹 Изчистваме временното състояние
            pending_transaction_data.pop(user_id, None)
            user_pending_type.pop(user_id, None)

    except Exception as e:
        print(f"❌ Грешка в handle_transaction_type_selection: {e}")
        if user_id:
            try:
                bot.answer_callback_query(call.id, "❌ Възникна грешка.")
            except Exception as inner_e:
                print(f"❌ Cannot answer callback: {inner_e}")

# Обработчик за командата "/edit"
@bot.message_handler(commands=['edit'])
def handle_edit(message):
    user_id = message.chat.id

    # Rate limiting
    if not rate_limiter.is_allowed(user_id):
        bot.reply_to(message, "⏸️ Твърде много заявки. Моля, изчакайте малко.")
        return

    user_name = message.from_user.first_name if message.from_user.first_name else "Unknown"

    records = get_user_records_from_airtable(user_name)

    if not records:
        bot.reply_to(message, "❌ Няма записи за редактиране.")
        return

    user_records[user_id] = [r["id"] for r in records[:MAX_USER_RECORDS]]

    reply_text = "Вашите записи:\n"
    for i, record in enumerate(records[:MAX_USER_RECORDS], 1):
        record_id = record["id"]
        fields = record.get("fields", {})
        description = fields.get("Описание", "Без описание")[:100]
        amount = fields.get("Сума (лв.)", fields.get("Сума (EUR)", fields.get("Сума (GBP)", fields.get("Сума (USD)", "?"))))
        account_name = "?"

        # Ако има акаунт, извличаме името
        account_ids = fields.get("Акаунт", [])
        if isinstance(account_ids, list) and account_ids and len(account_ids) > 0:
            try:
                acc_res = requests.get(f"{url_accounts}/{account_ids[0]}", headers=headers, timeout=10)
                if acc_res.status_code == 200:
                    acc_data = acc_res.json()
                    account_name = acc_data.get("fields", {}).get("REG", "?")[:50]
            except requests.exceptions.Timeout:
                print(f"⏱️ Timeout при извличане на акаунт")
            except requests.exceptions.RequestException as e:
                print(f"❌ Error fetching account: {e}")

        full_text = f"{amount} {description} от {account_name}"
        reply_text += f"{i}. {full_text[:150]}\n"

    user_state_timestamps[user_id] = datetime.now()
    sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за редактиране (напр. /edit 1):")
    bot.register_next_step_handler(sent_msg, process_edit_choice)


def update_amount(message):
    """Обработва новата стойност на сумата и актуализира запис в Airtable."""
    user_id = message.chat.id

    # Check rate limiting
    if not rate_limiter.is_allowed(user_id):
        bot.reply_to(message, "⏸️ Твърде много заявки. Моля, изчакайте малко.")
        return

    if user_id not in user_editing:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")
        return

    record_id = user_editing[user_id].get('record_id')
    if not record_id:
        bot.reply_to(message, "❌ Невалидно състояние на редактиране.")
        return

    new_amount_str = message.text.strip() if message.text else ""

    if not new_amount_str or len(new_amount_str) > 50:
        bot.reply_to(message, "❌ Невалидна дължина на сумата.")
        return

    try:
        # Търсене на сума с валута
        m = re.match(r'^(\d+(?:\.\d+)?)(\s*(лв|lv|лев|лева|bgn|eur|€|евро|evro|gbp|£|паунд|паунда|paunda))$', new_amount_str, re.IGNORECASE)
        if not m:
            bot.reply_to(message, "❌ Моля, въведете валидна сума с валута. Пример: 100 лв., 250 EUR, 50 GBP.")
            return

        amount_str = m.group(1)
        currency_str = m.group(2).strip().lower()

        # Преобразуване на сума в число
        new_amount = float(amount_str)

        # Validate amount range
        if new_amount < 0 or new_amount > 1_000_000_000:
            bot.reply_to(message, "❌ Сумата е извън допустимия диапазон.")
            return

        # Преобразуване на валутата в код
        currency_map = {
            "bgn": ("лв", "lv", "лев", "лева", "bgn"),
            "eur": ("eur", "€", "евро", "evro"),
            "gbp": ("gbp", "£", "паунд", "паунда", "paunda"),
            "usd": ("usd", "$", "долар", "долара", "долари", "дол", "щ", "щатски", "щатски долари", "щатски долара", "долар сащ", "долара сащ", "американски долар", "американски долари")
        }

        new_currency_code = None
        for code, aliases in currency_map.items():
            if currency_str in aliases:
                new_currency_code = code.upper()
                break

        if not new_currency_code:
            bot.reply_to(message, "❌ Моля, въведете валидна валута: лв., EUR, GBP, USD.")
            return

        # Записваме новата сума и валута в Airtable
        if new_currency_code == "BGN":
            field_name = "Сума (лв.)"
        elif new_currency_code == "EUR":
            field_name = "Сума (EUR)"
        elif new_currency_code == "GBP":
            field_name = "Сума (GBP)"
        elif new_currency_code == "USD":
            field_name = "Сума (USD)"

        new_data = {
            "fields": {
                field_name: new_amount,
                "Валута": new_currency_code
            }
        }

        res_put = requests.patch(f"{url_reports}/{record_id}", headers=headers, json=new_data, timeout=10)

        if res_put.status_code == 200:
            bot.reply_to(message, "✅ Сумата и валутата са успешно актуализирани.")
            user_editing.pop(user_id, None)
        else:
            print(f"❌ Airtable error: {res_put.status_code} - {res_put.text}")
            bot.reply_to(message, "❌ Грешка при актуализирането на сумата и валутата.")
            user_editing.pop(user_id, None)

    except ValueError as e:
        print(f"❌ ValueError in update_amount: {e}")
        bot.reply_to(message, "❌ Моля, въведете валидна сума.")
    except requests.exceptions.Timeout:
        print(f"⏱️ Timeout при актуализиране на сума")
        bot.reply_to(message, "⏱️ Заявката отне твърде много време. Опитайте отново.")
    except requests.exceptions.RequestException as e:
        print(f"❌ Request error in update_amount: {e}")
        bot.reply_to(message, "❌ Грешка при връзка с базата данни.")
    except Exception as e:
        print(f"❌ Unexpected error in update_amount: {e}")
        bot.reply_to(message, "❌ Възникна неочаквана грешка.")
        
@bot.message_handler(commands=['delete'])
def handle_delete(message):
    user_id = message.chat.id

    # Rate limiting
    if not rate_limiter.is_allowed(user_id):
        bot.reply_to(message, "⏸️ Твърде много заявки. Моля, изчакайте малко.")
        return

    user_name = message.from_user.first_name if message.from_user.first_name else "Unknown"

    records = get_user_records_from_airtable(user_name)

    if not records:
        bot.reply_to(message, "❌ Няма записи за изтриване.")
        return

    user_records[user_id] = [r["id"] for r in records[:MAX_USER_RECORDS]]

    reply_text = "Вашите записи за изтриване:\n"
    for i, record in enumerate(records[:MAX_USER_RECORDS], 1):
        record_id = record["id"]
        fields = record.get("fields", {})
        description = fields.get("Описание", "Без описание")[:100]
        amount = fields.get("Сума (лв.)", fields.get("Сума (EUR)", fields.get("Сума (GBP)", fields.get("Сума (USD)", "?"))))
        account_name = "?"

        account_ids = fields.get("Акаунт", [])
        if isinstance(account_ids, list) and account_ids and len(account_ids) > 0:
            try:
                acc_res = requests.get(f"{url_accounts}/{account_ids[0]}", headers=headers, timeout=10)
                if acc_res.status_code == 200:
                    acc_data = acc_res.json()
                    account_name = acc_data.get("fields", {}).get("REG", "?")[:50]
            except requests.exceptions.Timeout:
                print(f"⏱️ Timeout при извличане на акаунт")
            except requests.exceptions.RequestException as e:
                print(f"❌ Error fetching account: {e}")

        full_text = f"{amount} {description} от {account_name}"
        reply_text += f"{i}. {full_text[:150]}\n"

    user_state_timestamps[user_id] = datetime.now()
    sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за изтриване (напр. /delete 1):")
    bot.register_next_step_handler(sent_msg, process_delete_choice)



def process_delete_choice(message):
    """Обработва избора на запис за изтриване."""
    user_id = message.chat.id

    if not message.text:
        bot.reply_to(message, "❌ Невалидно съобщение.")
        return

    try:
        # Избор на запис за изтриване (по номер)
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ Моля, въведете валиден номер на запис.")
            return

        record_index = int(parts[1]) - 1  # Преобразуваме в индекс

        if user_id not in user_records:
            bot.reply_to(message, "❌ Няма записи за изтриване.")
            return

        user_record_list = user_records[user_id]
        if not isinstance(user_record_list, list) or not (0 <= record_index < len(user_record_list)):
            bot.reply_to(message, "❌ Невалиден номер на запис.")
            return

        record_id = user_record_list[record_index]

        # Изтриване на записа от Airtable
        delete_url = f"{url_reports}/{record_id}"

        try:
            res_delete = requests.delete(delete_url, headers=headers, timeout=10)

            if res_delete.status_code == 200:
                bot.reply_to(message, "✅ Записът беше изтрит успешно.")
                # Премахваме записа от списъка на потребителя
                user_record_list.remove(record_id)
            else:
                print(f"❌ Delete error: {res_delete.status_code}")
                bot.reply_to(message, "❌ Грешка при изтриването на записа.")
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout при изтриване на запис")
            bot.reply_to(message, "⏱️ Заявката отне твърде много време.")
        except requests.exceptions.RequestException as e:
            print(f"❌ Request error in delete: {e}")
            bot.reply_to(message, "❌ Грешка при връзка с базата.")

    except (ValueError, IndexError) as e:
        print(f"❌ Parse error in process_delete_choice: {e}")
        bot.reply_to(message, "❌ Моля, въведете валиден номер на запис.")
    except Exception as e:
        print(f"❌ Unexpected error in process_delete_choice: {e}")
        bot.reply_to(message, "❌ Възникна неочаквана грешка.")       

# Функция за обработка на полето за редактиране (описание, сума или акаунт)
def process_edit_field(message):
    user_id = message.chat.id
    field_to_edit = message.text.lower()

    if field_to_edit == "описание":
        user_editing[user_id]['field'] = 'описание'
        bot.reply_to(message, "Моля, въведете новото описание за този запис:")
        bot.register_next_step_handler(message, process_new_description)
    elif field_to_edit == "сума":
        user_editing[user_id]['field'] = 'сума'
        bot.reply_to(message, "Моля, въведете новата стойност за сумата:")
        bot.register_next_step_handler(message, update_amount)  # Извикваме update_amount за сума
    elif field_to_edit == "акаунт":
        user_editing[user_id]['field'] = 'акаунт'
        bot.reply_to(message, "Моля, въведете новия акаунт:")
        bot.register_next_step_handler(message, process_new_account)  # Извикваме process_new_account за акаунт
    else:
        bot.reply_to(message, "❌ Моля, въведете една от следните опции: описание, сума, акаунт.")
        bot.register_next_step_handler(message, process_edit_field)

# Обработчик за избор на запис
@bot.message_handler(func=lambda message: message.text.startswith('/edit '))
def process_edit_choice(message):
    """Обработва избора на запис за редактиране."""
    user_id = message.chat.id
    try:
        record_index = int(message.text.split()[1]) - 1  # Преобразуваме в индекс
        if user_id in user_records and 0 <= record_index < len(user_records[user_id]):
            record_id = user_records[user_id][record_index]
            # Записваме кой запис ще редактираме и кой поле се редактира
            user_editing[user_id] = {'record_id': record_id, 'field': None}
            # Изпращаме заявка за редактиране на този запис в Airtable
            bot.reply_to(message, "Моля, въведете какво искате да промените: описание, сума или акаунт.")
            bot.register_next_step_handler(message, process_edit_field)
        else:
            bot.reply_to(message, "❌ Невалиден номер на запис. Моля, въведете валиден номер.")
    except ValueError:
        bot.reply_to(message, "❌ Моля, въведете валиден номер на запис.")

def process_new_description(message):
    """Обновява описание в Airtable."""
    user_id = message.chat.id
    if user_id in user_editing:
        record_id = user_editing[user_id]['record_id']
        new_description = message.text.strip()

        # Печат на новото описание
        print(f"Updating description: {new_description}")

        # Записваме новото описание в Airtable
        new_data = {"fields": {"Описание": new_description}}
        res_put = requests.patch(f"{url_reports}/{record_id}", headers=headers, json=new_data)

        if res_put.status_code == 200:
            bot.reply_to(message, "✅ Записът е редактиран успешно.")
            del user_editing[user_id]  # Изтриваме записа от избраните за редактиране
        else:
            print(f"Error response: {res_put.status_code} - {res_put.text}")  # Печатаме отговора от Airtable
            bot.reply_to(message, "❌ Грешка при редактирането на записа.")
            del user_editing[user_id]
    else:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")

       # Обработчик за новата сума с валута - използва update_amount
def process_new_amount(message):
    """Обновява сумата и валутата в Airtable - използва update_amount."""
    update_amount(message)

# Обработчик за новата валута
def process_new_currency(message, new_amount):
    """Обновява валутата в Airtable."""
    user_id = message.chat.id
    new_currency = message.text.strip().lower()

    # Преобразуваме валутата към формат за Airtable
    if new_currency in ["лв", "lv", "лев", "лева", "bgn"]:
        new_currency_code = "BGN"
    elif new_currency in ["eur", "€", "евро", "evro"]:
        new_currency_code = "EUR"
    elif new_currency in ["gbp", "£", "паунд", "паунда", "paunda"]:
        new_currency_code = "GBP"
    else:
        bot.reply_to(message, "❌ Моля, въведете валидна валута: лв., EUR, GBP.")
        return

    # Актуализираме данните за сумата и валутата в Airtable
    user_id = message.chat.id
    if user_id in user_editing:
        record_id = user_editing[user_id]['record_id']

        # Determine field name based on currency
        if new_currency_code == "BGN":
            field_name = "Сума (лв.)"
        elif new_currency_code == "EUR":
            field_name = "Сума (EUR)"
        elif new_currency_code == "GBP":
            field_name = "Сума (GBP)"
        elif new_currency_code == "USD":
            field_name = "Сума (USD)"
        else:
            field_name = "Сума (лв.)"  # default

        new_data = {
            "fields": {
                field_name: new_amount,
                "Валута": new_currency_code  # Добавяме полето за валута
            }
        }

        res_put = requests.patch(f"{url_reports}/{record_id}", headers=headers, json=new_data)
        if res_put.status_code == 200:
            bot.reply_to(message, "✅ Сумата и валутата са успешно актуализирани.")
            del user_editing[user_id]  # Изтриваме записа от избраните за редактиране
        else:
            bot.reply_to(message, "❌ Грешка при актуализирането на сумата и валутата.")
            del user_editing[user_id]
    else:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")

        
def process_new_account(message):
    """Обновява акаунта в Airtable с частично съвпадение на името."""
    user_id = message.chat.id
    if user_id in user_editing and user_editing[user_id]['field'] == 'акаунт':
        record_id = user_editing[user_id]['record_id']
        new_account = message.text.strip().lower()  # Преобразуваме в малки букви

        # Нормализираме входа: премахваме специални символи
        normalized_input = re.sub(r"[^0-9A-Za-z]+", " ", new_account).strip().lower()
        keywords = normalized_input.split()

        # Създаване на формулата за търсене по ключови думи с логика AND
        formula = "AND(" + ", ".join([f"SEARCH('{term}', LOWER({{REG}})) > 0" for term in keywords]) + ")"
        params = {"filterByFormula": formula}

        # Търсене на акаунта в Airtable ("ВСИЧКИ АКАУНТИ") по колоната REG с частично съвпадение
        res = requests.get(url_accounts, headers=headers, params=params)

        if res.status_code == 200:
            data = res.json()
            records = data.get("records", [])
            if len(records) > 0:
                account_id = records[0]["id"]  # ID на намерен акаунт
                # Актуализираме акаунта в Airtable
                new_data = {"fields": {"Акаунт": [account_id]}}
                res_put = requests.patch(f"{url_reports}/{record_id}", headers=headers, json=new_data)
                if res_put.status_code == 200:
                    bot.reply_to(message, "✅ Акаунтът е актуализиран успешно.")
                else:
                    bot.reply_to(message, "❌ Грешка при актуализирането на акаунта.")
            else:
                bot.reply_to(message, "❌ Не намерихме акаунт с това име.")
        else:
            bot.reply_to(message, "❌ Грешка при търсенето на акаунт.")
    else:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")
        
def get_transaction_types_from_airtable():
            return list(get_transaction_type_options().keys())
    
# Обработчик за съобщения с финансови отчети
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = None
    try:
        user_id = message.chat.id

        # Rate limiting
        if not rate_limiter.is_allowed(user_id):
            bot.reply_to(message, "⏸️ Твърде много заявки. Моля, изчакайте малко.")
            return

        # Input validation
        if not message.text or len(message.text) > 500:
            bot.reply_to(message, "⚠️ Съобщението е празно или твърде дълго (макс. 500 символа).")
            return

        text = message.text
        user_name = message.from_user.first_name if message.from_user.first_name else "Unknown"
        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 📌 ПЪРВО парсваме съобщението
        amount, currency_code, description, account_name, is_expense = parse_transaction(text)

        if amount is None or currency_code is None or description == "":
            reply_text = ("⚠️ Неразпознат формат. Моля, използвайте формат като:\n"
                          "`100 лв. за <описание> от <акаунт>`")
            bot.reply_to(message, reply_text, parse_mode="Markdown")
            return

        # 📌 2. Проверката за избран ВИД
        if user_id not in user_pending_type or not user_pending_type[user_id].get("selected"):
            # 💾 Записваме парснатата транзакция, за да я използваме след избора
            pending_transaction_data[user_id] = {
                "amount": amount,
                "currency_code": currency_code,
                "description": description[:500],
                "account_name": account_name[:100] if account_name else "",
                "is_expense": is_expense,
                "user_name": user_name[:100],
                "datetime": current_datetime,
            }

            user_state_timestamps[user_id] = datetime.now()
            send_transaction_type_page(chat_id=user_id, page=0)

    except Exception as e:
        print(f"❌ Грешка в handle_message: {e}")
        if user_id:
            try:
                bot.reply_to(message, "❌ Възникна грешка при обработка на съобщението.")
            except Exception as inner_e:
                print(f"❌ Cannot reply to message: {inner_e}")      


WEBHOOK_URL = f"{os.getenv('WEBHOOK_BASE_URL')}/bot{TELEGRAM_BOT_TOKEN}"

# Настройваме webhook-а
bot.remove_webhook()
time.sleep(1)
bot.set_webhook(url=WEBHOOK_URL)

from flask import Flask, request

app = Flask(__name__)

@app.route(f"/bot{TELEGRAM_BOT_TOKEN}", methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
