import os
import re
import requests
from datetime import datetime
import telebot
from telebot import types  # ⬅️ тук
import time


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIRTABLE_PERSONAL_ACCESS_TOKEN = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")  # ID на Airtable базата
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

# Словар за запазване на всички записи на потребителя
user_records = {}

user_pending_type = {}

# Словар за запазване на избрания запис за редактиране
pending_transaction_data = {}  # временно съхраняваме парснатата транзакция
user_editing = {}

def normalize_text(text):
    """Привежда текста в малки букви и премахва специални символи."""
    text = text.lower()  # Преобразува в малки букви
    text = re.sub(r'[^a-zа-я0-9\s]', '', text)  # Премахва всички символи, различни от букви, цифри и интервали
    return text

def find_account(account_name):
    """Търси акаунт по ключови думи, независимо от големи/малки букви и тирета."""
    # Нормализиране на акаунта
    normalized_account_name = normalize_text(account_name)

    # Разделяме нормализирания акаунт на ключови думи
    search_terms = normalized_account_name.strip().split()

    # Изграждаме filterByFormula с AND за търсене на всички ключови думи
    conditions = [f'SEARCH("{term}", LOWER({{REG}})) > 0' for term in search_terms]
    formula = f'AND({",".join(conditions)})'
    params = {"filterByFormula": formula}

    # Изпращаме заявка към Airtable API
    res = requests.get(url_accounts, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        records = data.get("records", [])
        if len(records) > 0:
            account_id = records[0]["id"]  # Вземаме ID на първия съвпаднал акаунт
            return account_id
    return None

from datetime import datetime, timedelta

def get_user_records_from_airtable(user_name):
    """Извлича записите от последните 60 минути от Airtable за конкретен потребител."""
    now = datetime.now()
    one_hour_ago = now - timedelta(minutes=60)
    now_iso = now.isoformat()
    hour_ago_iso = one_hour_ago.isoformat()

    # Airtable filterByFormula търси по Име на потребителя и Дата (ISO формат)
    formula = (
        f"AND("
        f"{{Име на потребителя}} = '{user_name}',"
        f"IS_AFTER({{Дата}}, '{hour_ago_iso}')"
        f")"
    )

    params = {"filterByFormula": formula}
    res = requests.get(url_reports, headers=headers, params=params)

    if res.status_code == 200:
        data = res.json()
        return data.get("records", [])
    else:
        print(f"❌ Грешка при извличане на записи: {res.status_code} - {res.text}")
        return []

def parse_transaction(text):
    """
    Парсване на съобщение от вида "<сума> <валута> за <описание> от <акаунт>".
    Връща кортеж (amount, currency_code, description, account_name, is_expense).
    """
    text = text.strip()

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
import re
import requests

def get_transaction_types():
    url_types = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/ВИД%20ТРАНЗАКЦИЯ"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_PERSONAL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    res = requests.get(url_types, headers=headers)

    types_dict = {}

    if res.status_code == 200:
        data = res.json()
        for record in data.get("records", []):
            name = record["fields"].get("ТРАНЗАКЦИЯ")
            if name:
                types_dict[name] = record["id"]
    else:
        print("⚠️ Неуспешна заявка към Airtable:", res.status_code, res.text)

    return types_dict

def get_transaction_type_options():
    """Извлича всички видове транзакции от таблицата 'ВИД ТРАНЗАКЦИЯ'."""
    url_type = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/ВИД ТРАНЗАКЦИЯ"
    res = requests.get(url_type, headers=headers)
    if res.status_code == 200:
        data = res.json()
        options = {}
        for record in data.get("records", []):
            label = record["fields"].get("ТРАНЗАКЦИЯ")
            if label:
                options[label] = record["id"]
        return options
    else:
        print("❌ Грешка при зареждане на видовете транзакции:", res.text)
        return {}

def show_filtered_transaction_types(message):
    keyword = message.text.strip().lower()
    user_id = message.chat.id

    all_types = get_transaction_types()
    filtered = {
        name: id_ for name, id_ in all_types.items()
        if keyword in name.lower()
    }

    if not filtered:
        bot.send_message(user_id, "❌ Няма съвпадения. Опитай с друга дума.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(text=key, callback_data=key) for key in filtered]
    markup.add(*buttons)

    msg = bot.send_message(user_id, f"📌 Резултати за „{keyword}“:", reply_markup=markup)

    user_pending_type[user_id] = {
        "msg_id": msg.message_id,
        "options": filtered
    }

def handle_filter_input(message):
    keyword = message.text.strip().lower()
    user_id = message.chat.id

    all_types = get_transaction_types()
    filtered = {k: v for k, v in all_types.items() if keyword in k.lower()}

    if not filtered:
        bot.send_message(user_id, "❌ Няма резултати за тази дума.")
        return

    send_transaction_type_page(chat_id=user_id, page=0, filtered_types=filtered)

def send_transaction_type_page(chat_id, page=0, filtered_types=None):
    PAGE_SIZE = 20
    all_types = filtered_types if filtered_types is not None else get_transaction_types()
    sorted_keys = sorted(all_types.keys())
    total_pages = (len(sorted_keys) - 1) // PAGE_SIZE + 1
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

    # 🔄 Навигация (различен стил с емоджи)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Назад", callback_data="__prev"))
    if end < len(sorted_keys):
        nav_buttons.append(types.InlineKeyboardButton("➡️ Напред", callback_data="__next"))
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

@bot.callback_query_handler(func=lambda call: True)
def handle_transaction_type_selection(call):
    user_id = call.message.chat.id
    print(f"⚙️ Callback received: {call}")
    selected_label = call.data

    if user_id not in user_pending_type:
        bot.answer_callback_query(call.id, "❌ Няма очаквана транзакция.")
        return

    if call.data == "FILTER_BY_KEYWORD":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "🔍 Въведи дума за филтриране:")
        bot.register_next_step_handler(call.message, show_filtered_transaction_types)
        return

        
    print(f"📌 user_id: {user_id}")
    print(f"📌 selected_label: {selected_label}")
    print(f"📌 user_pending_type: {user_pending_type.get(user_id)}")
    
    if selected_label in ["__next", "__prev", "__filter"]:
        pass  # Ще обработим по-надолу
    elif selected_label not in user_pending_type[user_id]["options"]:
        bot.answer_callback_query(call.id, "❌ Невалиден избор.")
        return

    selected_id = user_pending_type[user_id]["options"].get(selected_label)


    if selected_label == "__prev":
        current_page = user_pending_type[user_id].get("page", 0)
        new_page = max(current_page - 1, 0)
        bot.delete_message(user_id, user_pending_type[user_id]["msg_id"])
        send_transaction_type_page(chat_id=user_id, page=new_page, filtered_types=user_pending_type[user_id].get("filtered"))
        return

    elif selected_label == "__next":
        current_page = user_pending_type[user_id].get("page", 0)
        new_page = current_page + 1
        bot.delete_message(user_id, user_pending_type[user_id]["msg_id"])
        send_transaction_type_page(chat_id=user_id, page=new_page, filtered_types=user_pending_type[user_id].get("filtered"))
        return

    elif selected_label == "__filter":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(user_id, "🔍 Въведи дума за търсене:")
        bot.register_next_step_handler(msg, handle_filter_input)
        return

    elif selected_label == "__reset":
        bot.answer_callback_query(call.id)
        bot.delete_message(user_id, user_pending_type[user_id]["msg_id"])
        send_transaction_type_page(chat_id=user_id, page=0)  # 🧼 Показваме всички
        return


    # 💾 Запази избора
    user_pending_type[user_id]["selected"] = selected_id
    user_pending_type[user_id]["selected_label"] = selected_label

    # ✅ Покажи избраното
    bot.edit_message_text(
        chat_id=user_id,
        message_id=user_pending_type[user_id]["msg_id"],
        text=f"✅ Избра вид: {selected_label}"
    )
    # 📥 Ако има чакащи данни за транзакция — записваме в Airtable
    if user_id in pending_transaction_data:
        tx = pending_transaction_data[user_id]
        account_id = find_account(tx["account_name"])

        fields = {
            "Дата": tx["datetime"],
            "Описание": tx["description"],
            "Име на потребителя": tx["user_name"],
            "ВИД": [selected_id],
        }


        if tx["currency_code"] == "BGN":
            fields["Сума (лв.)"] = tx["amount"]
        elif tx["currency_code"] == "EUR":
            fields["Сума (EUR)"] = tx["amount"]
        elif tx["currency_code"] == "GBP":
            fields["Сума (GBP)"] = tx["amount"]

        if account_id:
            fields["Акаунт"] = [account_id]
        else:
            fields["Описание"] = f"{tx['description']} (Акаунт: {tx['account_name']})"

        if res_post.status_code in (200, 201):
            record_id = res_post.json().get("id")
            if user_id not in user_records:
                user_records[user_id] = []
            user_records[user_id].append(record_id)
            bot.send_message(user_id, f"✅ Избра вид: {selected_label}\n📌 Отчетът е записан успешно.")
        else:
            bot.send_message(user_id, f"❌ Грешка при записването: {res_post.text}")

        # 🧹 Изчистваме временното състояние
        del pending_transaction_data[user_id]
        del user_pending_type[user_id]


    # 💾 Запази избраното ID
    user_pending_type[user_id]["selected_label"] = selected_label

# Обработчик за командата "/edit"
@bot.message_handler(commands=['edit'])
def handle_edit(message):
    user_id = message.chat.id
    user_name = message.from_user.first_name

    records = get_user_records_from_airtable(user_name)

    if not records:
        bot.reply_to(message, "❌ Няма записи за редактиране.")
        return

    user_records[user_id] = [r["id"] for r in records]

    reply_text = "Вашите записи:\n"
    for i, record in enumerate(records, 1):
        record_id = record["id"]
        fields = record.get("fields", {})
        description = fields.get("Описание", "Без описание")
        amount = fields.get("Сума (лв.)", fields.get("Сума (EUR)", fields.get("Сума (GBP)", "Неопределена сума")))
        account_name = "Неизвестен акаунт"

        # Ако има акаунт, извличаме името
        account_ids = fields.get("Акаунт", [])
        if isinstance(account_ids, list) and account_ids:
            acc_res = requests.get(f"{url_accounts}/{account_ids[0]}", headers=headers)
            if acc_res.status_code == 200:
                acc_data = acc_res.json()
                account_name = acc_data.get("fields", {}).get("REG", "Неизвестен акаунт")

        full_text = f"{amount} {description} от {account_name}"
        reply_text += f"{i}. Запис {record_id} - {full_text}\n"

    sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за редактиране (напр. /edit 1):")
    bot.register_next_step_handler(sent_msg, process_edit_choice)


def update_amount(message):
    """Обработва новата стойност на сумата и актуализира запис в Airtable."""
    user_id = message.chat.id
    if user_id in user_editing:
        record_id = user_editing[user_id]['record_id']
        new_amount_str = message.text.strip()

        try:
            # Търсене на сума с валута (например "100 лв.", "250 EUR", "50 GBP")
            m = re.match(r'^(\d+(?:\.\d+)?)(\s*(лв|lv|лев|лева|bgn|eur|€|евро|evro|gbp|£|паунд|паунда|paunda))$', new_amount_str.strip(), re.IGNORECASE)
            if m:
                amount_str = m.group(1)  # Сума (например 100, 250)
                currency_str = m.group(2).strip().lower()  # Валутата (например лв., EUR, GBP)

                # Печат на данни за диагностика
                print(f"Received amount: {amount_str}")
                print(f"Received currency: {currency_str}")

                # Преобразуване на сума в число
                new_amount = float(amount_str)

                # Преобразуване на валутата в код
                if currency_str in ("лв", "lv", "лев", "лева", "bgn"):
                    new_currency_code = "BGN"
                elif currency_str in ("eur", "€", "евро", "evro"):
                    new_currency_code = "EUR"
                elif currency_str in ("gbp", "£", "паунд", "паунда", "paunda"):
                    new_currency_code = "GBP"
                else:
                    bot.reply_to(message, "❌ Моля, въведете валидна валута: лв., EUR, GBP.")
                    return

                # Печат на данни за актуализация
                print(f"Updating fields: Сума = {new_amount}, Валута = {new_currency_code}")

                # Записваме новата сума и валута в Airtable
                new_data = {
                    "fields": {
                        "Сума (лв.)" if new_currency_code == "BGN" else "Сума (EUR)" if new_currency_code == "EUR" else "Сума (GBP)": new_amount,
                        "Валута": new_currency_code
                    }
                }

                res_put = requests.patch(f"{url_reports}/{record_id}", headers=headers, json=new_data)
                # Печат на отговора от Airtable
                print(f"Response from Airtable: {res_put.status_code} - {res_put.text}")  # Печатаме отговора от Airtable

                if res_put.status_code == 200:
                    bot.reply_to(message, "✅ Сумата и валутата са успешно актуализирани.")
                    del user_editing[user_id]  # Изтриваме записа от избраните за редактиране
                else:
                    bot.reply_to(message, "❌ Грешка при актуализирането на сумата и валутата.")
                    del user_editing[user_id]
            else:
                bot.reply_to(message, "❌ Моля, въведете валидна сума с валута. Пример: 100 лв., 250 EUR, 50 GBP.")
        except ValueError:
            bot.reply_to(message, "❌ Моля, въведете валидна сума.")
            return
    else:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")
        
@bot.message_handler(commands=['delete'])
def handle_delete(message):
    user_id = message.chat.id
    user_name = message.from_user.first_name

    records = get_user_records_from_airtable(user_name)

    if not records:
        bot.reply_to(message, "❌ Няма записи за изтриване.")
        return

    user_records[user_id] = [r["id"] for r in records]

    reply_text = "Вашите записи за изтриване:\n"
    for i, record in enumerate(records, 1):
        record_id = record["id"]
        fields = record.get("fields", {})
        description = fields.get("Описание", "Без описание")
        amount = fields.get("Сума (лв.)", fields.get("Сума (EUR)", fields.get("Сума (GBP)", "Неопределена сума")))
        account_name = "Неизвестен акаунт"

        account_ids = fields.get("Акаунт", [])
        if isinstance(account_ids, list) and account_ids:
            acc_res = requests.get(f"{url_accounts}/{account_ids[0]}", headers=headers)
            if acc_res.status_code == 200:
                acc_data = acc_res.json()
                account_name = acc_data.get("fields", {}).get("REG", "Неизвестен акаунт")

        full_text = f"{amount} {description} от {account_name}"
        reply_text += f"{i}. Запис {record_id} - {full_text}\n"

    sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за изтриване (напр. /delete 1):")
    bot.register_next_step_handler(sent_msg, process_delete_choice)



def process_delete_choice(message):
    """Обработва избора на запис за изтриване."""
    user_id = message.chat.id
    try:
        # Избор на запис за изтриване (по номер)
        record_index = int(message.text.split()[1]) - 1  # Преобразуваме в индекс
        if user_id in user_records and 0 <= record_index < len(user_records[user_id]):
            record_id = user_records[user_id][record_index]
            print(f"Deleting record {record_id}")
            
            # Изтриване на записа от Airtable
            delete_url = f"{url_reports}/{record_id}"
            res_delete = requests.delete(delete_url, headers=headers)

            if res_delete.status_code == 200:
                bot.reply_to(message, f"✅ Съобщението {record_id} беше изтрито успешно.")
                # Премахваме записа от списъка на потребителя
                user_records[user_id].remove(record_id)
            else:
                bot.reply_to(message, f"❌ Грешка при изтриването на съобщението {record_id}.")
        else:
            bot.reply_to(message, "❌ Невалиден номер на запис.")
    except (ValueError, IndexError):
        bot.reply_to(message, "❌ Моля, въведете валиден номер на запис.")       

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

       # Обработчик за новата сума с валута
def process_new_amount(message):
    """Обновява сумата и валутата в Airtable."""
    user_id = message.chat.id
    if user_id in user_editing and user_editing[user_id]['field'] == 'сума':
        record_id = user_editing[user_id]['record_id']
        new_amount_str = message.text.strip()

        try:
            # Търсене на сума с валута (например "100 лв.", "250 EUR", "50 GBP")
            m = re.match(r'^(\d+(?:\.\d+)?)(\s*(лв|lv|лев|лева|bgn|eur|€|евро|evro|gbp|£|паунд|паунда|paunda))$', new_amount_str.strip(), re.IGNORECASE)
            if m:
                amount_str = m.group(1)  # Сума (например 100, 250)
                currency_str = m.group(2).strip().lower()  # Валутата (например лв., EUR, GBP)

                # Преобразуване на сума в число
                new_amount = float(amount_str)

                # Преобразуване на валутата в код
                if currency_str in ("лв", "lv", "лев", "лева", "bgn"):
                    new_currency_code = "BGN"
                elif currency_str in ("eur", "€", "евро", "evro"):
                    new_currency_code = "EUR"
                elif currency_str in ("gbp", "£", "паунд", "паунда", "paunda"):
                    new_currency_code = "GBP"
                else:
                    bot.reply_to(message, "❌ Моля, въведете валидна валута: лв., EUR, GBP.")
                    return

                # Записваме новата сума и валута в Airtable
                bot.reply_to(message, "Моля, потвърдете редакцията на сумата и валутата.")
                new_data = {
                    "fields": {
                        "Сума (лв.)" if new_currency_code == "BGN" else "Сума (EUR)" if new_currency_code == "EUR" else "Сума (GBP)": new_amount,
                        "Валута": new_currency_code
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
                bot.reply_to(message, "❌ Моля, въведете валидна сума с валута. Пример: 100 лв., 250 EUR, 50 GBP.")
        except ValueError:
            bot.reply_to(message, "❌ Моля, въведете валидна сума.")
            return
    else:
        bot.reply_to(message, "❌ Не намерихме избрания запис за редактиране.")

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
        new_data = {
            "fields": {
                "Сума (лв.)" if new_currency_code == "BGN" else "Сума (EUR)" if new_currency_code == "EUR" else "Сума (GBP)": new_amount,
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
    text = message.text
    user_id = message.chat.id
    user_name = message.from_user.first_name
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # ⬅️ добави това тук
    
    # 📌 ПЪРВО парсваме съобщението
    amount, currency_code, description, account_name, is_expense = parse_transaction(text)

    if amount is None or currency_code is None or description == "":
        reply_text = ("⚠️ Неразпознат формат. Моля, използвайте формат като:\n"
                      "`100 лв. за <описание> от <акаунт>`")
        bot.reply_to(message, reply_text, parse_mode="Markdown")
        return

    # 📌 2. Проверката за избран ВИД
    types_list = get_transaction_types_from_airtable()
    if user_id not in user_pending_type or not user_pending_type[user_id].get("selected"):
        # 💾 Записваме парснатата транзакция, за да я използваме след избора
        pending_transaction_data[user_id] = {
            "amount": amount,
            "currency_code": currency_code,
            "description": description,
            "account_name": account_name,
            "is_expense": is_expense,
            "user_name": user_name,
            "datetime": current_datetime,
        }

        send_transaction_type_page(chat_id=user_id, page=0)

    # 📌 3. Извличане на акаунта
    account_part = ""
    if re.search(r'\bот\b', text, re.IGNORECASE):
        account_part = re.split(r'\bот\b', text, flags=re.IGNORECASE)[-1].strip()
    elif re.search(r'\bot\b', text, re.IGNORECASE):
        account_part = re.split(r'\bot\b', text, flags=re.IGNORECASE)[-1].strip()

    # Почистваме акаунта и създаваме ключови думи
    normalized_input = re.sub(r"[^\w\s]", " ", account_part).lower()
    keywords = normalized_input.split()

    # Конструираме частта с нормализиране на полето REG
    norm_reg = 'REGEX_REPLACE(LOWER({REG}), "[^0-9a-z ]", " ")'

    # Изграждаме условие за всяка дума: SEARCH("дума", нормализиран REG) > 0
    conditions = [f'SEARCH(\"{w}\", {norm_reg}) > 0' for w in keywords]

    # Свързваме всички условия с AND(...)
    formula_filter = "AND(" + ", ".join(conditions) + ")"

    # Търсене на акаунта в Airtable ("ВСИЧКИ АКАУНТИ") по колоната REG с частично съвпадение
    account_id = None
    if account_name:
        # Почистване на акаунта и търсения текст
        search_term = clean_string(account_name.strip())

        # Изпращаме заявка към Airtable API
        params = {"filterByFormula": formula_filter}
        res = requests.get(url_accounts, headers=headers, params=params)

        print(f"Search response: {res.text}")  # Това ще ни покаже отговора от Airtable

        if res.status_code == 200:
            data = res.json()
            records = data.get("records", [])
            if len(records) > 0:
                account_id = records[0]["id"]  # ID на намерения запис
                print(f"Account found: {account_id}")
            else:
                print("No account found.")
        else:
            print(f"Error searching account: HTTP {res.status_code} - {res.text}")      

    # Подготовка на данните за новия запис в "Отчет Телеграм"
    fields = {
    "Дата": current_datetime,
    "Описание": description,
}

# ✅ Добавяме "ВИД", ако има избран
    if user_id in user_pending_type:
        selected_type = user_pending_type[user_id].get("selected")
        if selected_type:
            fields["ВИД"] = [selected_type]  # ✅ не забравяй скобите []
            del user_pending_type[user_id]


    if currency_code == "BGN":
        fields["Сума (лв.)"] = amount
    elif currency_code == "EUR":
        fields["Сума (EUR)"] = amount
    elif currency_code == "GBP":
        fields["Сума (GBP)"] = amount

    if account_id:
        fields["Акаунт"] = [account_id]  # Ако акаунтът е намерен, добавяме ID на акаунта
    else:
        # Ако акаунтът не е намерен, уведомяваме бота и добавяме името на акаунта в описанието
        reply_text = f"❌ Не намерихме акаунт с име: {account_name}. Записахме акаунта в полето 'Описание'."
        bot.reply_to(message, reply_text)
        fields["Описание"] = f"{description} (Акаунт: {account_name})"

    # Добавяме името на потребителя
    fields["Име на потребителя"] = user_name  # Добавяме името на потребителя в новото поле
    
    # Изпращаме данните към Airtable
    data = {"fields": fields}
    res_post = requests.post(url_reports, headers=headers, json=data)
    if res_post.status_code in (200, 201):
        record_id = res_post.json().get("id")  # Получаваме ID на създадения запис
        # Добавяме запис в списъка с всички записи на потребителя
        if message.chat.id not in user_records:
            user_records[message.chat.id] = []
        user_records[message.chat.id].append(record_id)
        reply_text = "✅ Отчетът е записан успешно."
        bot.reply_to(message, reply_text)
    else:
        error_msg = res_post.text
        reply_text = "❌ Грешка при записването на отчета!"
        bot.reply_to(message, reply_text)
        print(f"Failed to create record: HTTP {res_post.status_code} - {error_msg}")

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

