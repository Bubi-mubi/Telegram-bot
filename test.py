from email.mime import text
import re
import requests
from datetime import datetime
import telebot

# Конфигурация – въведете вашите токени/ключове
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIRTABLE_PERSONAL_ACCESS_TOKEN = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")  # ID на Airtable базата
TABLE_ACCOUNTS = "ВСИЧКИ АКАУНТИ"
TABLE_REPORTS = "Отчет Телеграм"

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
    # Можеш да замениш със стойности от Airtable в бъдеще
    return [
        "Proxy", "New SIM card UK", "Office supplies",
        "Ivelin money", "GSM", "Такси", "Пътуване", "Други"
    ]

from telebot import types  # Увери се, че този импорт е наличен!

@bot.message_handler(commands=['settype'])
def ask_transaction_type(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    types_list = get_transaction_types()
    buttons = [types.InlineKeyboardButton(text=typ, callback_data=typ) for typ in types_list]
    markup.add(*buttons)

    msg = bot.send_message(message.chat.id, "📌 Избери вид на транзакцията:", reply_markup=markup)
    user_pending_type[message.chat.id] = {"msg_id": msg.message_id}

@bot.callback_query_handler(func=lambda call: call.data in get_transaction_types())
def handle_transaction_type_selection(call):
    user_id = call.message.chat.id
    selected_type = call.data

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        chat_id=user_id,
        message_id=user_pending_type[user_id]["msg_id"],
        text=f"✅ Избра вид: {selected_type}"
    )

    user_pending_type[user_id]["selected"] = selected_type


# Обработчик за командата "/edit"
@bot.message_handler(commands=['edit'])
def handle_edit(message):
    """Редактиране на съществуващ запис."""
    user_id = message.chat.id
    if user_id in user_records and user_records[user_id]:
        # Покажете на потребителя списък с неговите записи
        records = user_records[user_id]
        reply_text = "Вашите записи:\n"
        for i, record_id in enumerate(records, 1):
            # Извличане на подробности за записите от Airtable
            record_url = f"{url_reports}/{record_id}"
            res = requests.get(record_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                record_fields = data.get("fields", {})
                description = record_fields.get("Описание", "Без описание")
                amount = record_fields.get("Сума (лв.)", "Неопределена сума")
                account_id = record_fields.get("Акаунт", "Неизвестен акаунт")

                # Извличане на името на акаунта
                if isinstance(account_id, list) and account_id:
                    account_url = f"{url_accounts}/{account_id[0]}"
                    res_account = requests.get(account_url, headers=headers)
                    if res_account.status_code == 200:
                        account_data = res_account.json()
                        account_name = account_data.get("fields", {}).get("REG", "Неизвестен акаунт")
                    else:
                        account_name = "Неизвестен акаунт"
                else:
                    account_name = "Неизвестен акаунт"
                
                # Показваме целия запис в едно съобщение
                full_text = f"{amount} {description} от {account_name}"
                reply_text += f"{i}. Запис {record_id} - {full_text}\n"
            else:
                reply_text += f"{i}. Запис {record_id} - Неуспешно извличане на данни.\n"
        
        sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за редактиране (например /edit 1):")
        bot.register_next_step_handler(sent_msg, process_edit_choice)
    else:
        bot.reply_to(message, "❌ Няма записи за редактиране.")

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
    """Изтриване на съществуващ запис."""
    user_id = message.chat.id
    
    if user_id in user_records and user_records[user_id]:
        # Покажете на потребителя списък с неговите записи
        records = user_records[user_id]
        reply_text = "Вашите записи за изтриване:\n"
        
        for i, record_id in enumerate(records, 1):
            # Извличане на подробности за записите от Airtable
            record_url = f"{url_reports}/{record_id}"
            res = requests.get(record_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                record_fields = data.get("fields", {})
                description = record_fields.get("Описание", "Без описание")
                amount = record_fields.get("Сума (лв.)", "Неопределена сума")
                account_id = record_fields.get("Акаунт", "Неизвестен акаунт")

                # Извличане на името на акаунта
                if isinstance(account_id, list) and account_id:
                    account_url = f"{url_accounts}/{account_id[0]}"
                    res_account = requests.get(account_url, headers=headers)
                    if res_account.status_code == 200:
                        account_data = res_account.json()
                        account_name = account_data.get("fields", {}).get("REG", "Неизвестен акаунт")
                    else:
                        account_name = "Неизвестен акаунт"
                else:
                    account_name = "Неизвестен акаунт"
                
                # Показваме целия запис в едно съобщение
                full_text = f"{amount} {description} от {account_name}"
                reply_text += f"{i}. Запис {record_id} - {full_text}\n"
            else:
                reply_text += f"{i}. Запис {record_id} - Неуспешно извличане на данни.\n"
        
        # Изпращаме списъка със записи и запитваме за номер на запис за изтриване
        sent_msg = bot.reply_to(message, reply_text + "Изберете номер на запис за изтриване (например /delete 1):")
        bot.register_next_step_handler(sent_msg, process_delete_choice)
    else:
        bot.reply_to(message, "❌ Няма записи за изтриване.")


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

# Обработчик за съобщения с финансови отчети
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text  # Получаваме текста от съобщението
    user_name = message.from_user.first_name  # Извличаме името на потребителя
    user_id = message.chat.id  # ID на потребителя в чата
    """Обработва всяко получено текстово съобщение като финансов отчет."""

    # Вземаме текущата дата и час в желания формат
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Вземаме дата и час
    
    if re.search(r'\bот\b', text, re.IGNORECASE):  # Търсим "от"
        account_part = re.split(r'\bот\b', text, flags=re.IGNORECASE)[-1].strip()
    elif re.search(r'\bot\b', text, re.IGNORECASE):  # Търсим "ot"
        account_part = re.split(r'\bot\b', text, flags=re.IGNORECASE)[-1].strip()
    else:
        account_part = ""
    amount, currency_code, description, account_name, is_expense = parse_transaction(text)

    if amount is None or currency_code is None or description == "":
        reply_text = ("⚠️ Неразпознат формат. Моля, използвайте формат като:\n"
                      "`100 лв. за <описание> от <акаунт>`")
        bot.reply_to(message, reply_text, parse_mode="Markdown")
        return

    # Извличаме само частта след "от" или "ot"
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
        "Дата": current_datetime,  # Добавяме текущата дата и час в полето "Дата"
        "Описание": description,
        
    }
    if user_id in user_pending_type and user_pending_type[user_id].get("selected"):
        fields["ВИД"] = user_pending_type[user_id]["selected"]

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

# Стартиране на бота
print("🤖 Bot is polling...")
# Завършваме първоначалното пускане на бота
bot.polling(none_stop=True)
