import asyncio
import logging
import re
import os
from aiohttp import web
import json
import time
from datetime import datetime
from urllib.parse import urlencode
import cloudscraper
from bs4 import BeautifulSoup
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------- Логирование ----------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8852546532:AAHb0xid3g040DELLHVTrtu4_JA9FjFLl-4"  # замените на токен бота

# ---------- Состояния создания фильтра ----------
(
    SELECT_BRAND,
    SELECT_MODEL,
    SELECT_GENERATION,
    SELECT_REGION,
    SELECT_CITY,
    SELECT_ENGINE,
    INPUT_VOLUME,
    SELECT_TRANSMISSION,
    INPUT_YEAR,
    INPUT_PRICE_MIN,
    INPUT_PRICE_MAX,
    CONFIRMATION,
) = range(12)

# ---------- Данные марок/моделей ----------
BRANDS = ["Mercedes-Benz"]

MERCEDES_MODELS = {
    "A-Class": ["W168", "W169", "W176", "W177"],
    "B-Class": ["W245", "W246", "W247"],
    "C-Class": ["W202", "W203", "W204", "W205", "W206"],
    "E-Class": ["W124", "W210", "W211", "W212", "W213", "W214"],
    "S-Class": ["W220", "W221", "W222", "W223"],
    "CLA": ["C117", "C118"],
    "CLS": ["C219", "C218", "C257"],
    "CLE": ["C236"],
    "GLA": ["X156", "H247"],
    "GLB": ["X247"],
    "GLC": ["X253", "X254"],
    "GLE": ["W166", "W167"],
    "GLS": ["X166", "X167"],
    "G-Class": ["W460", "W461", "W463", "W464"],
    "SLC": ["R172"],
    "SLK": ["R170", "R171", "R172"],
    "SL-Class": ["R129", "R230", "R231", "R232"],
    "CL-Class": ["C215", "C216"],
    "CLK": ["C208", "C209"],
    "M-Class": ["W163", "W164", "W166"],
    "ML": ["W163", "W164", "W166"],
    "GL-Class": ["X164", "X166"],
    "GLK": ["X204"],
    "R-Class": ["W251"],
    "V-Class": ["W447"],
    "Vaneo": ["W414"],
    "Viano": ["W639"],
    "Citan": ["W415"],
    "Sprinter": ["W901-W905", "W906", "W907"],
    "Marco Polo": ["W447"],
    "EQA": ["H243"],
    "EQB": ["X243"],
    "EQC": ["N293"],
    "EQE": ["V295"],
    "EQS": ["V297"],
    "EQV": ["W447"],
    "AMG GT": ["C190", "C192"],
    "AMG ONE": [],
    "SLS AMG": ["C197"],
    "SLR McLaren": ["C199"],
    "CLK GTR": ["C297"],
    "190 (W201)": ["W201"],
    "600 (W100)": ["W100"],
    "300 SL (W198)": ["W198"],
    "190 SL": ["R121"],
    "W110": ["W110"],
    "W111": ["W111"],
    "W112": ["W112"],
    "W113 (Pagoda)": ["W113"],
    "W114": ["W114"],
    "W115": ["W115"],
    "W123": ["W123"],
    "W124": ["W124"],
    "W126": ["W126"],
    "W140": ["W140"],
    "W210": ["W210"],
    "W211": ["W211"],
    "W212": ["W212"],
    "W220": ["W220"],
    "W221": ["W221"],
    "R107": ["R107"],
    "R129": ["R129"],
    "R230": ["R230"],
    "Mercedes-Maybach S-Class": ["Z223"],
    "Mercedes-Maybach GLS": ["X167"],
}

BELARUS_REGIONS = {
    "Минск": ["Минск"],
    "Брестская обл.": ["Брест", "Барановичи", "Пинск", "Кобрин"],
    "Витебская обл.": ["Витебск", "Полоцк", "Орша", "Новополоцк"],
    "Гомельская обл.": ["Гомель", "Мозырь", "Речица", "Жлобин"],
    "Гродненская обл.": ["Гродно", "Лида", "Слоним", "Волковыск"],
    "Минская обл.": ["Борисов", "Солигорск", "Молодечно", "Слуцк"],
    "Могилёвская обл.": ["Могилёв", "Бобруйск", "Осиповичи", "Кричев"],
}

ENGINE_TYPES = ["Бензин", "Дизель", "Электро"]
TRANSMISSIONS = ["Автомат", "Механика", "Робот", "Вариатор"]

# Глобальное хранилище фильтров и активных индексов
user_filters = {}
active_filters = {}
# Кэш отправленных ссылок, чтобы не слать дубли
sent_links = set()

# ---------- Маппинги для API av.by ----------
REGION_ID_MAP = {
    "Минск": 1,
    "Брестская обл.": 2,
    "Витебская обл.": 3,
    "Гомельская обл.": 4,
    "Гродненская обл.": 5,
    "Минская обл.": 6,
    "Могилёвская обл.": 7,
}

ENGINE_MAP = {
    "Бензин": "petrol",
    "Дизель": "diesel",
    "Электро": "electric",
}

TRANSMISSION_MAP = {
    "Автомат": "automatic",
    "Механика": "manual",
    "Робот": "robot",
    "Вариатор": "variator",
}

# ---------- Парсер av.by ----------
def get_avby_model_id(model_name):
    """Получает model_id для указанной модели Mercedes по API."""
    url = "https://api.av.by/offer-types/cars/sections/6/models"
    try:
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            models = resp.json()
            for m in models:
                if m.get("name") == model_name:
                    return m["id"]
    except Exception as e:
        logger.error(f"Ошибка получения model_id: {e}")
    return None

def fetch_avby_ads(filters):
    """Возвращает список объявлений с av.by по заданному фильтру."""
    scraper = cloudscraper.create_scraper()
    # Получаем model_id (можно закэшировать для ускорения)
    model_id = get_avby_model_id(filters.get("model"))
    if not model_id:
        return []

    params = {
        "brand_id": 6,
        "model_id": model_id,
        "region_id": REGION_ID_MAP.get(filters.get("region"), 1),
        "sort": "date_desc",
        "page": 1,
        "limit": 5,
    }
    # Опциональные параметры
    engine_type = filters.get("engine_type")
    if engine_type and engine_type in ENGINE_MAP:
        params["engine_type"] = ENGINE_MAP[engine_type]

    if filters.get("year_min"):
        params["year_from"] = filters["year_min"]
    if filters.get("year_max"):
        params["year_to"] = filters["year_max"]

    if filters.get("price_min"):
        params["price_usd_from"] = filters["price_min"]
    if filters.get("price_max") and filters["price_max"] > 0:
        params["price_usd_to"] = filters["price_max"]

    if filters.get("volume_min") is not None:
        params["engine_capacity_from"] = filters["volume_min"]
    if filters.get("volume_max") is not None:
        params["engine_capacity_to"] = filters["volume_max"]

    trans = filters.get("transmission")
    if trans and trans in TRANSMISSION_MAP:
        params["transmission"] = TRANSMISSION_MAP[trans]

    url = "https://api.av.by/offer-types/cars/search"
    try:
        resp = scraper.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"av.by API ответил {resp.status_code}")
            return []
        data = resp.json()
        offers = data.get("offers", [])
        ads = []
        for offer in offers:
            try:
                ad = {
                    "brand": "Mercedes-Benz",
                    "model": offer.get("model", ""),
                    "generation": offer.get("generation", ""),
                    "year": offer.get("year"),
                    "engine": ENGINE_TYPES[0] if offer.get("engine_type") == "petrol" else
                              ENGINE_TYPES[1] if offer.get("engine_type") == "diesel" else
                              ENGINE_TYPES[2] if offer.get("engine_type") == "electric" else "—",
                    "volume": float(offer.get("engine_capacity", 0)),
                    "transmission": TRANSMISSIONS[0] if offer.get("transmission") == "automatic" else
                                     TRANSMISSIONS[1] if offer.get("transmission") == "manual" else
                                     TRANSMISSIONS[2] if offer.get("transmission") == "robot" else
                                     TRANSMISSIONS[3] if offer.get("transmission") == "variator" else "—",
                    "price": int(offer.get("price_usd")),
                    "mileage": offer.get("mileage"),
                    "city": offer.get("city_name", ""),
                    "link": f"https://av.by{offer.get('public_url')}",
                    "description": offer.get("description", ""),
                }
                ads.append(ad)
            except Exception:
                continue
        return ads
    except Exception as e:
        logger.error(f"Ошибка парсинга av.by: {e}")
        return []

# ---------- Парсер kufar.by ----------
def fetch_kufar_ads(filters):
    """Парсит первую страницу поиска kufar.by и возвращает подходящие объявления."""
    scraper = cloudscraper.create_scraper()
    base_url = "https://www.kufar.by/l/cars"
    # Строим URL с параметрами
    query_params = {
        "ar": "3",  # регион: Беларусь (можно уточнить)
        "sort": "lst.d",
    }
    # Марка
    brand = filters.get("brand", "").lower()
    if brand == "mercedes-benz":
        query_params["prc"] = "rgn:2"  # region=2 (Минск?) - на kufar сложнее, упростим
    # Модель – kufar часто использует slug модели
    model = filters.get("model")
    if model:
        model_slug = model.lower().replace(" ", "-")
        query_params["q"] = model_slug

    url = f"{base_url}?{urlencode(query_params)}"
    try:
        resp = scraper.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select("article[class*='styles_wrapper']")
        ads = []
        for item in items[:5]:
            try:
                title_elem = item.select_one("h3")
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                price_elem = item.select_one("span[class*='price']")
                price_str = price_elem.get_text(strip=True).replace("р.", "").replace(" ", "")
                price_usd = int(re.sub(r"\D", "", price_str)) // 3  # очень грубо, лучше парсить доллар
                link_elem = item.select_one("a")
                link = link_elem["href"] if link_elem else ""
                if link and not link.startswith("http"):
                    link = "https://www.kufar.by" + link
                # Извлекаем год, пробег, объём из текста (упрощённо)
                ad = {
                    "brand": filters.get("brand", ""),
                    "model": filters.get("model", ""),
                    "generation": "",
                    "year": 2020,  # надо парсить
                    "engine": "Бензин",
                    "volume": 1.8,
                    "transmission": "Автомат",
                    "price": price_usd,
                    "mileage": 100000,
                    "city": filters.get("city", ""),
                    "link": link,
                    "description": title,
                }
                ads.append(ad)
            except Exception:
                continue
        return ads
    except Exception as e:
        logger.error(f"Ошибка kufar: {e}")
        return []

# ---------- Вспомогательные функции бота ----------
def get_main_keyboard():
    return ReplyKeyboardMarkup([["Найти авто", "Фильтры"]], resize_keyboard=True)

def format_filter_description(flt):
    brand = flt.get("brand", "—")
    model = flt.get("model", "—")
    gen = flt.get("generation")
    model_full = f"{brand} {model}" + (f" ({gen})" if gen else "")
    engine = flt.get("engine_type", "—")
    vol_min = flt.get("volume_min")
    vol_max = flt.get("volume_max")
    vol_str = f"{vol_min}-{vol_max}л" if vol_min is not None and vol_max is not None else "—"
    trans = flt.get("transmission", "—")
    year_min = flt.get("year_min")
    year_max = flt.get("year_max")
    if year_min and year_max:
        year_str = f"{year_min}-{year_max}"
    elif year_min:
        year_str = f"от {year_min}"
    else:
        year_str = "любой"
    price_min = flt.get("price_min", 0)
    price_max = flt.get("price_max", 0)
    price_str = f"${price_min}-${price_max}" if price_max else f"от ${price_min}"
    region = flt.get("region", "—")
    city = flt.get("city", "—")
    loc = f"{region}, {city}" if region else "—"
    return (
        f"🚘 {model_full}\n"
        f"⚙️ {engine}, {vol_str}, {trans}\n"
        f"📅 {year_str}  |  💵 {price_str}\n"
        f"📍 {loc}"
    )

def filter_matches(flt, ad):
    if flt.get("brand") and flt["brand"].lower() != ad["brand"].lower():
        return False
    if flt.get("model") and flt["model"].lower() != ad["model"].lower():
        return False
    if flt.get("generation") and flt["generation"].lower() != ad.get("generation", "").lower():
        return False
    if flt.get("region") and flt["region"] != ad.get("region", ""):
        return False
    if flt.get("city") and flt["city"].lower() != ad.get("city", "").lower():
        return False
    if flt.get("engine_type") and flt["engine_type"].lower() != ad["engine"].lower():
        return False
    v_min = flt.get("volume_min")
    v_max = flt.get("volume_max")
    if v_min is not None and v_max is not None:
        adv_vol = ad.get("volume")
        if adv_vol is not None and not (v_min <= adv_vol <= v_max):
            return False
    y_min = flt.get("year_min")
    y_max = flt.get("year_max")
    if y_min is not None and y_max is not None:
        adv_year = ad.get("year")
        if adv_year is not None and not (y_min <= adv_year <= y_max):
            return False
    elif y_min is not None:
        adv_year = ad.get("year")
        if adv_year is not None and adv_year < y_min:
            return False
    if flt.get("transmission") and flt["transmission"].lower() != ad["transmission"].lower():
        return False
    p_min = flt.get("price_min", 0)
    p_max = flt.get("price_max", 0)
    adv_price = ad.get("price", 0)
    if p_max > 0 and not (p_min <= adv_price <= p_max):
        return False
    elif p_max == 0 and p_min > 0 and adv_price < p_min:
        return False
    return True

# ---------- Команды и диалог ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Леха Дреко привет", reply_markup=get_main_keyboard())

async def handle_main_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Найти авто":
        return await start_filter(update, context)
    elif text == "Фильтры":
        return await show_filters(update, context)

async def start_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(brand, callback_data=f"brand_{brand}")] for brand in BRANDS]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_filter")])
    await update.message.reply_text("Выберите марку:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_BRAND

async def brand_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    brand = query.data.split("_", 1)[1]
    context.user_data["filter"] = {"brand": brand}
    models = list(MERCEDES_MODELS.keys())
    keyboard = []
    row = []
    for i, model in enumerate(models, 1):
        row.append(InlineKeyboardButton(model, callback_data=f"model_{model}"))
        if i % 3 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_brands")])
    await query.edit_message_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODEL

async def model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_brands":
        return await start_filter(update, context)
    model = data.split("_", 1)[1]
    context.user_data["filter"]["model"] = model
    generations = MERCEDES_MODELS.get(model, [])
    if not generations:
        await query.edit_message_text(f"Модель {model} выбрана. Поколений нет, переходим к региону.")
        return await ask_region(query, context)
    keyboard = []
    row = []
    for i, gen in enumerate(generations, 1):
        row.append(InlineKeyboardButton(gen, callback_data=f"gen_{gen}"))
        if i % 4 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_models")])
    await query.edit_message_text("Выберите поколение:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_GENERATION

async def generation_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_models":
        return await brand_selected(update, context)
    gen = data.split("_", 1)[1]
    context.user_data["filter"]["generation"] = gen
    return await ask_region(query, context)

async def ask_region(query, context):
    regions = list(BELARUS_REGIONS.keys())
    keyboard = []
    row = []
    for i, reg in enumerate(regions, 1):
        row.append(InlineKeyboardButton(reg, callback_data=f"reg_{reg}"))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    await query.edit_message_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_REGION

async def region_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region = query.data.split("_", 1)[1]
    context.user_data["filter"]["region"] = region
    cities = BELARUS_REGIONS.get(region, [])
    keyboard = [[InlineKeyboardButton(city, callback_data=f"city_{city}")] for city in cities]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_regions")])
    await query.edit_message_text("Выберите город:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_CITY

async def city_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_regions":
        return await ask_region(query, context)
    city = data.split("_", 1)[1]
    context.user_data["filter"]["city"] = city
    keyboard = [[InlineKeyboardButton(eng, callback_data=f"engine_{eng}")] for eng in ENGINE_TYPES]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cities")])
    await query.edit_message_text("Тип двигателя:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ENGINE

async def engine_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_cities":
        return await ask_region(query, context)
    engine = data.split("_", 1)[1]
    context.user_data["filter"]["engine_type"] = engine
    await query.edit_message_text(
        "📏 ОБЪЁМ ДВИГАТЕЛЯ\n\n"
        "Введите желаемый объём:\n\n"
        "🔹 Конкретный: 3.0\n"
        "🔹 Диапазон: 2.0-3.0\n"
        "🔹 Любой: 0.1-9\n\n"
        "(Можно использовать точку или запятую)\n\n"
        "Отправьте числовое значение.",
    )
    return INPUT_VOLUME

async def volume_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    range_match = re.match(r"^(\d+(\.\d+)?)-(\d+(\.\d+)?)$", text)
    single_match = re.match(r"^(\d+(\.\d+)?)$", text)
    if range_match:
        v_min = float(range_match.group(1))
        v_max = float(range_match.group(3))
    elif single_match:
        v_min = v_max = float(single_match.group(1))
    else:
        await update.message.reply_text("Некорректный формат. Попробуйте ещё раз.")
        return INPUT_VOLUME
    context.user_data["filter"]["volume_min"] = v_min
    context.user_data["filter"]["volume_max"] = v_max
    keyboard = [[InlineKeyboardButton(t, callback_data=f"trans_{t}")] for t in TRANSMISSIONS]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_engine")])
    await update.message.reply_text("Выберите коробку передач:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_TRANSMISSION

async def transmission_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_engine":
        keyboard = [[InlineKeyboardButton(eng, callback_data=f"engine_{eng}")] for eng in ENGINE_TYPES]
        await query.edit_message_text("Тип двигателя:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECT_ENGINE
    trans = data.split("_", 1)[1]
    context.user_data["filter"]["transmission"] = trans
    await query.edit_message_text(
        "📅 ГОД ВЫПУСКА\n\n"
        "Введите год или диапазон:\n\n"
        "🔹 Конкретный: 2015\n"
        "🔹 Диапазон: 2010-2018\n"
        "🔹 От года: 2015-2024\n\n"
        "Не важен год? Нажмите кнопку «Пропустить»\n\n"
        "Отправьте год или нажмите кнопку.",
    )
    reply_markup = ReplyKeyboardMarkup([["Пропустить"]], resize_keyboard=True, one_time_keyboard=True)
    await query.message.reply_text("Ожидание ввода года...", reply_markup=reply_markup)
    return INPUT_YEAR

async def year_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "пропустить":
        context.user_data["filter"]["year_min"] = None
        context.user_data["filter"]["year_max"] = None
        await update.message.reply_text("Год не указан.", reply_markup=get_main_keyboard())
        return await ask_price_min(update, context)
    range_match = re.match(r"^(\d{4})-(\d{4})$", text)
    from_match = re.match(r"^(\d{4})-$", text)
    single_match = re.match(r"^(\d{4})$", text)
    if range_match:
        y_min = int(range_match.group(1))
        y_max = int(range_match.group(2))
    elif from_match:
        y_min = int(from_match.group(1))
        y_max = datetime.now().year
    elif single_match:
        y_min = y_max = int(single_match.group(1))
    else:
        await update.message.reply_text("Неверный формат. Введите год или нажмите «Пропустить».")
        return INPUT_YEAR
    context.user_data["filter"]["year_min"] = y_min
    context.user_data["filter"]["year_max"] = y_max
    await update.message.reply_text(f"Год: {y_min}-{y_max}", reply_markup=get_main_keyboard())
    return await ask_price_min(update, context)

async def ask_price_min(update, context):
    await update.message.reply_text("Введите МИНИМАЛЬНУЮ цену (0 если не важно):")
    return INPUT_PRICE_MIN

async def price_min_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите целое число.")
        return INPUT_PRICE_MIN
    context.user_data["filter"]["price_min"] = price
    await update.message.reply_text("Введите МАКСИМАЛЬНУЮ цену (0 если не важно):")
    return INPUT_PRICE_MAX

async def price_max_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите целое число.")
        return INPUT_PRICE_MAX
    context.user_data["filter"]["price_max"] = price
    flt = context.user_data["filter"]
    desc = format_filter_description(flt)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Сохранить", callback_data="save_filter")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_filter")],
    ])
    await update.message.reply_text(f"Ваш фильтр:\n{desc}\n\nСохранить?", reply_markup=keyboard)
    return CONFIRMATION

async def save_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    flt = context.user_data.pop("filter", None)
    if not flt:
        await query.edit_message_text("Ошибка: фильтр не найден.")
        return ConversationHandler.END
    if user_id not in user_filters:
        user_filters[user_id] = []
    user_filters[user_id].append(flt)
    await query.edit_message_text("✅ Фильтр сохранён!\n\nВы можете включить его в разделе «Фильтры».")
    await query.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def cancel_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Создание фильтра отменено.")
    else:
        await update.message.reply_text("Создание фильтра отменено.")
    context.user_data.pop("filter", None)
    await update.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# ---------- Управление фильтрами ----------
async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список фильтров пользователя."""
    user_id = update.effective_user.id
    filters_list = user_filters.get(user_id, [])

    # Определяем, откуда брать объект для ответа
    if update.callback_query:
        message = update.callback_query.message
    else:
        message = update.message

    if not filters_list:
        await message.reply_text(
            "У вас пока нет сохранённых фильтров.",
            reply_markup=get_main_keyboard()
        )
        return

    active_set = active_filters.get(user_id, set())
    text_lines = ["📋 Ваши фильтры:"]
    keyboard = []
    for i, flt in enumerate(filters_list):
        status = "✅ Активен" if i in active_set else "⏸ Выключен"
        desc_short = f"{flt.get('brand','')} {flt.get('model','')} {flt.get('generation','')}"
        text_lines.append(f"{i+1}. {desc_short} — {status}")
        if i in active_set:
            btn = InlineKeyboardButton(f"🔴 Выключить {i+1}", callback_data=f"toggle_{i}")
        else:
            btn = InlineKeyboardButton(f"🟢 Включить {i+1}", callback_data=f"toggle_{i}")
        keyboard.append([btn])
    keyboard.append([InlineKeyboardButton("🗑 Удалить фильтр", callback_data="delete_filter_menu")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="close_filters")])

    # Используем reply_text или edit_message_text в зависимости от контекста
    if update.callback_query:
        # Редактируем то же сообщение, где была нажата кнопка
        await message.edit_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await message.reply_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включение/выключение фильтра."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if data.startswith("toggle_"):
        index = int(data.split("_")[1])
        if user_id not in active_filters:
            active_filters[user_id] = set()
        if index in active_filters[user_id]:
            active_filters[user_id].discard(index)
            status = "выключен"
        else:
            active_filters[user_id].add(index)
            status = "включен"
        await query.answer(f"Фильтр {index+1} {status}.")
        # Обновляем список фильтров, передавая тот же update
        await show_filters(update, context)
    elif data == "close_filters":
        await query.edit_message_text("Главное меню.")
        await query.message.reply_text("Выберите действие", reply_markup=get_main_keyboard())
    elif data == "delete_filter_menu":
        filters_list = user_filters.get(user_id, [])
        if not filters_list:
            await query.edit_message_text("Нет фильтров.")
            return
        keyboard = [
            [InlineKeyboardButton(f"❌ Удалить {i+1}: {f['brand']} {f['model']}", callback_data=f"del_{i}")]
            for i, f in enumerate(filters_list)
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_filters")])
        await query.edit_message_text("Выберите фильтр для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление фильтра."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if data.startswith("del_"):
        index = int(data.split("_")[1])
        if user_id in user_filters and 0 <= index < len(user_filters[user_id]):
            del user_filters[user_id][index]
            # Сдвиг активных индексов
            if user_id in active_filters:
                new_active = set()
                for i in active_filters[user_id]:
                    if i < index:
                        new_active.add(i)
                    elif i > index:
                        new_active.add(i - 1)
                active_filters[user_id] = new_active
        await query.answer("Фильтр удалён.")
        await show_filters(update, context)
    elif data == "back_to_filters":
        await show_filters(update, context)

# ---------- Реальный мониторинг ----------
async def monitoring_loop(app):
    while True:
        logger.info("🔍 Запуск цикла мониторинга...")
        await asyncio.sleep(60)
        logger.info(f"Активных фильтров: {len(active_filters)}")
        for user_id, active_set in list(active_filters.items()):
            if not active_set:
                continue
            filters_list = user_filters.get(user_id, [])
            for idx in active_set:
                if idx >= len(filters_list):
                    continue
                flt = filters_list[idx]
                logger.info(f"Проверяю фильтр {idx+1} пользователя {user_id}: {flt.get('brand')} {flt.get('model')}")
                try:
                    av_ads = fetch_avby_ads(flt)
                    kuf_ads = fetch_kufar_ads(flt)
                    all_ads = av_ads + kuf_ads
                    logger.info(f"Найдено объявлений: av.by – {len(av_ads)}, kufar – {len(kuf_ads)}")
                    for ad in all_ads:
                        link = ad.get("link")
                        if link and link not in sent_links:
                            if filter_matches(flt, ad):
                                sent_links.add(link)
                                await send_notification(user_id, flt, ad, app)
                except Exception as e:
                    logger.error(f"Ошибка при проверке фильтра: {e}")

async def send_notification(user_id, flt, ad, app):
    model_full = f"{ad['brand']} {ad['model']}" + (f" {ad.get('generation','')}" if ad.get('generation') else "")
    filter_desc = f"{flt.get('brand','')} {flt.get('model','')}" + (f" ({flt.get('generation','')})" if flt.get('generation') else "")
    volume_range = f"{flt.get('volume_min','?')}-{flt.get('volume_max','?')}л"
    text = (
        "‼️‼️‼️ НОВОЕ АВТО ‼️‼️‼️\n\n"
        f"📋 Ваш фильтр: {filter_desc} | {volume_range}\n\n"
        f"🚘 {model_full}\n"
        f"📅 {ad['year']} г.\n"
        f"⚙️ {ad['engine']}, {ad['volume']}л, {ad['transmission']}\n"
        f"💵 {ad['price']} $\n"
        f"🏁 Пробег: {ad.get('mileage', '—')} км\n"
        f"📍 {ad.get('city', '—')}\n"
        f"Ссылка: {ad['link']}\n\n"
        f"📝 Описание:\n{ad.get('description', '—')}\n\n"
        "Это бот ⚡️ Бот мониторит av.by и kufar в режиме реального времени.\n"
        "Как только появится авто по вашим фильтрам — вы узнаете первым! 🚀"
    )
    try:
        await app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю {user_id}: {e}")

# ---------- Основной запуск ----------
def main():
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Найти авто$"), start_filter)],
        states={
            SELECT_BRAND: [CallbackQueryHandler(brand_selected, pattern="^brand_")],
            SELECT_MODEL: [CallbackQueryHandler(model_selected, pattern="^(model_|back_to_brands)")],
            SELECT_GENERATION: [CallbackQueryHandler(generation_selected, pattern="^(gen_|back_to_models)")],
            SELECT_REGION: [CallbackQueryHandler(region_selected, pattern="^reg_")],
            SELECT_CITY: [CallbackQueryHandler(city_selected, pattern="^(city_|back_to_regions)")],
            SELECT_ENGINE: [CallbackQueryHandler(engine_selected, pattern="^(engine_|back_to_cities)")],
            INPUT_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, volume_input)],
            SELECT_TRANSMISSION: [CallbackQueryHandler(transmission_selected, pattern="^(trans_|back_to_engine)")],
            INPUT_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, year_input)],
            INPUT_PRICE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_min_input)],
            INPUT_PRICE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_max_input)],
            CONFIRMATION: [CallbackQueryHandler(save_filter, pattern="^save_filter$"),
                           CallbackQueryHandler(cancel_filter, pattern="^cancel_filter$")],
        },
        fallbacks=[CallbackQueryHandler(cancel_filter, pattern="^cancel_filter$")],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex("^Фильтры$"), show_filters))
    app.add_handler(CallbackQueryHandler(toggle_filter, pattern="^(toggle_|close_filters|delete_filter_menu)"))
    app.add_handler(CallbackQueryHandler(delete_filter, pattern="^(del_|back_to_filters)"))

    print("Бот с реальным мониторингом запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная проверка парсинга по первому активному фильтру."""
    user_id = update.effective_user.id
    active_set = active_filters.get(user_id, set())
    if not active_set:
        await update.message.reply_text("У вас нет активных фильтров. Сначала включите фильтр в разделе «Фильтры».")
        return

    filters_list = user_filters.get(user_id, [])
    # Берём первый активный фильтр
    idx = list(active_set)[0]
    if idx >= len(filters_list):
        await update.message.reply_text("Ошибка: фильтр не найден.")
        return

    flt = filters_list[idx]
    await update.message.reply_text(f"⏳ Проверяю фильтр: {flt.get('brand')} {flt.get('model')}...")

    try:
        av_ads = fetch_avby_ads(flt)
        kuf_ads = fetch_kufar_ads(flt)
        all_ads = av_ads + kuf_ads

        if not all_ads:
            await update.message.reply_text(
                "ℹ️ По вашему фильтру ничего не найдено. Возможно, сейчас нет новых объявлений или парсер не смог получить данные."
            )
        else:
            # Покажем первые 3 объявления кратко
            text = f"🔎 Найдено {len(all_ads)} объявлений (первые 3):\n\n"
            for ad in all_ads[:3]:
                text += (
                    f"🚘 {ad['brand']} {ad['model']} ({ad.get('year','—')})\n"
                    f"💵 ${ad.get('price','—')} | {ad.get('engine','—')}, {ad.get('volume','—')}л\n"
                    f"📍 {ad.get('city','—')}\n"
                    f"🔗 {ad.get('link','—')}\n\n"
                )
            await update.message.reply_text(text, disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при проверке: {e}")
        logger.error(f"/test error: {e}")

def main():
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Найти авто$"), start_filter)],
        states={
            SELECT_BRAND: [CallbackQueryHandler(brand_selected, pattern="^brand_")],
            SELECT_MODEL: [CallbackQueryHandler(model_selected, pattern="^(model_|back_to_brands)")],
            SELECT_GENERATION: [CallbackQueryHandler(generation_selected, pattern="^(gen_|back_to_models)")],
            SELECT_REGION: [CallbackQueryHandler(region_selected, pattern="^reg_")],
            SELECT_CITY: [CallbackQueryHandler(city_selected, pattern="^(city_|back_to_regions)")],
            SELECT_ENGINE: [CallbackQueryHandler(engine_selected, pattern="^(engine_|back_to_cities)")],
            INPUT_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, volume_input)],
            SELECT_TRANSMISSION: [CallbackQueryHandler(transmission_selected, pattern="^(trans_|back_to_engine)")],
            INPUT_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, year_input)],
            INPUT_PRICE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_min_input)],
            INPUT_PRICE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_max_input)],
            CONFIRMATION: [CallbackQueryHandler(save_filter, pattern="^save_filter$"),
                           CallbackQueryHandler(cancel_filter, pattern="^cancel_filter$")],
        },
        fallbacks=[CallbackQueryHandler(cancel_filter, pattern="^cancel_filter$")],
        allow_reentry=True,
        per_message=False,  # ← убирает предупреждение PTBUserWarning
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex("^Фильтры$"), show_filters))
    app.add_handler(CallbackQueryHandler(toggle_filter, pattern="^(toggle_|close_filters|delete_filter_menu)"))
    app.add_handler(CallbackQueryHandler(delete_filter, pattern="^(del_|back_to_filters)"))

    async def post_init(application):
        asyncio.create_task(monitoring_loop(application))
        asyncio.create_task(run_web_server())

    app.post_init = post_init

    print("Бот с реальным мониторингом запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

async def healthcheck(request):
    return web.Response(text="OK")

async def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app_web = web.Application()
    app_web.router.add_get("/", healthcheck)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Healthcheck server on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    main()