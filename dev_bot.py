import asyncio
import logging
import re
from telegram import InputMediaVideo
import telethon
import django
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, BadRequest
from telethon import TelegramClient, events
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
import os
from district import get_district_by_coords, get_coords_by_address
from make_info import process_text_with_gpt_price, process_text_with_gpt_sq, process_text_with_gpt_adress, \
    process_text_with_gpt_rooms
from meters import find_nearest_metro
from proccess import process_text_with_gpt2, process_text_with_gpt3, process_text_with_gpt
from typing import Any
# Загружаем переменные окружения
load_dotenv()
# Настроить Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from main.models import  DEVMESSAGE, DEVINFO, DEVSubscription

# Настройка логгера
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
processed_group_ids = set()      # (chat_id, grouped_id)
processed_message_ids = set()

bot2 = Bot(token=os.getenv("DEV_BOT_TOKEN_SUB"))
# Конфигурация
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
TELEGRAM_PASSWORD = os.getenv('TELEGRAM_PASSWORD')
BOT_TOKEN = os.getenv("DEV_BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_NAME = "session_name_lost_dev"

TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID_DEV")
YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY")
DOWNLOAD_FOLDER = "downloads/"


# Инициализация клиента Telethon
client = TelegramClient(SESSION_NAME, API_ID, API_HASH, system_version='1.2.3-zxc-custom',
                        device_model='aboba-linux-custom', app_version='1.0.1')

def _norm_text(s: Any) -> str:
    """Безопасно приводит значение к строке, убирает пробелы и NBSP."""
    if s is None:
        return ""
    return str(s).strip().replace("\u00A0", " ")

def _first_token(s: str) -> str:
    """Берём первый «словесный» токен (буквы), игнорируя пунктуацию в начале."""
    s = s.lstrip(" \t\n\r-—.,;:!?'\"()[]{}")
    # собираем до первого пробела/пунктуации
    token = []
    for ch in s:
        if ch.isalpha():
            token.append(ch.lower())
        else:
            break
    return "".join(token)

_YES_TOKENS = {"да", "yes", "true", "y", "ok", "ага", "угу"}
_NO_TOKENS  = {"нет", "no", "false", "n", "неа"}

def coerce_to_bool(value: Any, default: bool | None = None) -> bool | None:
    """
    Пытается интерпретировать value как булево.
    Возвращает True/False или default (по умолчанию None), если не распознано.

    Примеры:
      coerce_to_bool(" да ")        -> True
      coerce_to_bool("Yes!")        -> True
      coerce_to_bool(" false ")     -> False
      coerce_to_bool(None)          -> None
      coerce_to_bool("ok")          -> True
      coerce_to_bool("не знаю")     -> None
      coerce_to_bool(1)             -> True
      coerce_to_bool(0)             -> False
    """
    # числовые быстрые пути
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 0:
            return False
        if value == 1:
            return True

    text = _norm_text(value)
    if not text:
        return default

    tok = _first_token(text)

    if tok in _YES_TOKENS:
        return True
    if tok in _NO_TOKENS:
        return False

    # дополнительные формы типа "true/false" на английском могут прийти как целое слово
    low = text.lower()
    if low in {"true", "false"}:
        return low == "true"

    return default

async def get_username_by_id(user_id):
    try:
        # Преобразуем ID в целое число
        user_id = int(user_id)
        # Получаем информацию о пользователе
        user = await client.get_entity(user_id)
        if user.username:
            return f"https://t.me/{user.username}"
    except Exception as e:
        logger.error(f"Ошибка получения username: {e}")
    return None  # Если не удалось получить username


async def process_contacts(text: str) -> str | None:
    raw_contact = await asyncio.to_thread(process_text_with_gpt2, text)
    print('process')
    if raw_contact.startswith("tg://user?id="):
        user_id = raw_contact.split("=")[1]
        return await get_username_by_id(user_id) or raw_contact
    return raw_contact


async def download_media(message):
    """
    Скачивает все медиа (фото и видео) из сообщения и альбомов (по grouped_id).
    Возвращает список словарей {'type': 'photo'/'video', 'path': путь_к_файлу'}.
    """
    media_list = []
    # Если сообщение – часть альбома, собираем все сообщения с этим grouped_id
    if message.grouped_id:
        album_msgs = await client.get_messages(
            message.chat_id,
            min_id=message.id - 20,
            max_id=message.id + 20
        )
        # Фильтруем сообщения того же альбома
        album_msgs = [m for m in album_msgs if m and m.grouped_id == message.grouped_id]
    else:
        album_msgs = [message]

    # Проходим по каждому сообщению альбома
    for msg in album_msgs:
        # Скачиваем фото или видео
        if msg.photo:
            file_path = await client.download_media(msg.photo, DOWNLOAD_FOLDER)
            if file_path:
                media_list.append({'type': 'photo', 'path': file_path})
        elif msg.video:
            file_path = await client.download_media(msg.video, DOWNLOAD_FOLDER)
            if file_path:
                media_list.append({'type': 'video', 'path': file_path})
    # Ограничиваем размер до 10 элементов
    return media_list[:10]


def _is_non_empty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False

def build_post_text(base_text: str, contacts: str | None, add_quote: bool = True) -> str:
    """
    Возвращает финальный текст:
    — добавляет блок 'Контакты: ...' один раз (если его ещё нет и контакты валидные)
    — добавляет цитату с HTML-ссылкой на бота (если add_quote=True)
    — соблюдает двойные пустые строки между абзацами
    """
    text = base_text or ""
    # нормализуем переносы: двойные пустые строки между абзацами
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = "\n\n".join(lines)

    # добавим контакты, если их ещё нет
    if contacts and contacts.lower() not in ["нет", "нет."] and "Контакты:" not in text:
        text += "\n\nКонтакты: " + contacts

    if add_quote:
        text += (
            "\n\n— <i>Настройте фильтры в "
            "<a href='https://t.me/arendatoriy_find_bot'>боте</a> "
            "и получайте только подходящие варианты</i>"
        )
    return text

async def send_media_group(bot, chat_id, text, media_items, parse_mode: str = "HTML"):
    if not media_items:
        await bot.send_message(chat_id, text, parse_mode=parse_mode)
        return

    media_group, open_files, valid_paths = [], [], []

    for item in media_items:
        file_path = item.get("path")
        file_type = item.get("type")
        if not file_path or not _is_non_empty_file(file_path):
            continue
        try:
            f = open(file_path, "rb")
        except Exception:
            continue

        open_files.append(f)
        valid_paths.append((file_path, file_type))
        caption = text if len(media_group) == 0 else None

        if file_type == "photo":
            media_group.append(InputMediaPhoto(media=f, caption=caption, parse_mode=parse_mode))
        else:
            media_group.append(InputMediaVideo(media=f, caption=caption, parse_mode=parse_mode))

    if not media_group:
        await bot.send_message(chat_id, text, parse_mode=parse_mode)
        return

    try:
        if len(media_group) == 1:
            file_path, file_type = valid_paths[0]
            try:
                if open_files:
                    open_files[0].close()
            except Exception:
                pass
            open_files = []

            if not _is_non_empty_file(file_path):
                await bot.send_message(chat_id, text, parse_mode=parse_mode)
                return

            with open(file_path, "rb") as fresh_f:
                if file_type == "photo":
                    await bot.send_photo(chat_id, fresh_f, caption=text, parse_mode=parse_mode)
                else:
                    await bot.send_video(chat_id, fresh_f, caption=text, parse_mode=parse_mode)
        else:
            await bot.send_media_group(chat_id=chat_id, media=media_group)

    except BadRequest as e:
        # отправим хотя бы текст, чтобы не терять пост
        await bot.send_message(chat_id, text, parse_mode=parse_mode)
    finally:
        for f in open_files:
            try:
                f.close()
            except Exception:
                pass


async def check_subscriptions_and_notify(info_instance, contacts):
    logger.info(f"🔔 Начало обработки подписок для объявления {info_instance.id}")

    subscriptions = await sync_to_async(list)(
        DEVSubscription.objects.filter(is_active=True)
    )
    logger.info(f"📋 Найдено {len(subscriptions)} активных подписок")
    if not subscriptions:
        logger.info("❌ Нет активных подписок, пропускаем уведомления")
        return

    ad_data = {
        'price': info_instance.price,
        'rooms': info_instance.rooms,
        'count_meters_flat': info_instance.count_meters_flat,
        'location': info_instance.location,
        'count_meters_metro': info_instance.count_meters_metro,
        'address': info_instance.adress,
        'images': info_instance.message.images,
        'description': info_instance.message.new_text
    }

    logger.info(
        f"AD → price={ad_data['price']}, rooms={ad_data['rooms']}, "
        f"area={ad_data['count_meters_flat']}, metro={ad_data['count_meters_metro']}, "
        f"district={ad_data['location']}"
    )

    matched_users = set()
    for subscription in subscriptions:
        ok = await sync_to_async(is_ad_match_subscription)(ad_data, subscription)
        logger.info(
            f"[CHECK] user_id={subscription.user_id} match={ok} | "
            f"sub: price[{getattr(subscription, 'min_price', None)}..{getattr(subscription, 'max_price', None)}], "
            f"rooms[{getattr(subscription, 'min_rooms', None)}..{getattr(subscription, 'max_rooms', None)}], "
            f"area[{getattr(subscription, 'min_flat', None)}..{getattr(subscription, 'max_flat', None)}], "
            f"district={getattr(subscription, 'district', None)}, "
            f"metro_close={getattr(subscription, 'metro_close', None)} "
        )
        if ok and subscription.user_id not in matched_users:
            matched_users.add(subscription.user_id)
            await send_notification(subscription.user_id, ad_data, info_instance.message, contacts)

    logger.info(f"✅ Рассылка завершена: совпадений {len(matched_users)}")



_NUM_RE = re.compile(r'^([+\-]?\d+(?:\.\d+)?)')

def safe_parse_number(value: Any) -> float | None:
    """
    Парсит число из строки/числа:
    - понимает ведущий знак +/-
    - пробелы и NBSP игнорируются
    - запятая -> точка
    - поддерживает «длинное» минус-символ U+2212
    """
    if value is None:
        return None
    s = str(value).strip()

    # нормализуем пробелы/запятые/минусы
    s = s.replace('\u00A0', ' ')   # NBSP -> space
    s = s.replace('−', '-')        # U+2212 -> обычный дефис
    s = s.replace(',', '.')        # , -> .
    s = s.replace(' ', '')         # убираем все пробелы (разделители тысяч)

    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


async def new_message_handler(event):
    bot = Bot(token=BOT_TOKEN)
    logger.info(f"Новое сообщение из канала: {event.chat.username or event.chat.title}")

    if not event.message:
        return

    msg = event.message

    # --- защита от повторной обработки ---
    key_msg = (msg.chat_id, msg.id)
    if key_msg in processed_message_ids:
        return
    processed_message_ids.add(key_msg)

    if getattr(msg, "grouped_id", None):
        key_album = (msg.chat_id, msg.grouped_id)
        if key_album in processed_group_ids:
            return
        processed_group_ids.add(key_album)

    # -------- извлечение данных --------
    text = await extract_text_from_event(event)
    media_items = await download_media(event.message)
    contacts = await process_contacts(text)

    # нормализация tg://user?id=
    if contacts and contacts.startswith("tg://user?id="):
        user_id = contacts.split("=", 1)[1] if "=" in contacts else None
        if not user_id:
            return
        fixed = await get_username_by_id(user_id)
        if not fixed:
            return
        contacts = fixed

    # -------- GPT --------
    help_text = await asyncio.to_thread(process_text_with_gpt3, text)
    new_text = await asyncio.to_thread(process_text_with_gpt, text)

    # -------- нормализация текста --------
    new_text = new_text.replace("*", "\n\n")
    lines = [line.strip() for line in new_text.split("\n") if line.strip()]
    new_text = "\n\n".join(lines)

    # -------- фильтрация --------
    if not is_yes(help_text):
        return

    if new_text.lower() in ("нет", "нет."):
        return

    # -------- адрес --------
    address = await asyncio.to_thread(process_text_with_gpt_adress, new_text)

    # удалить адрес от GPT
    new_text = remove_address_block(new_text)

    # вставить адрес красиво и синим
    new_text = insert_address_after_area(new_text, address)

    # -------- контакты --------
    if contacts:
        new_text += f"\n\nКонтакты: {contacts}"

    # -------- цитата --------
    new_text += (
        "\n\n— <i>Настройте фильтры в "
        "<a href='https://t.me/arendatoriy_find_bot'>боте</a> "
        "и получайте только подходящие варианты</i>"
    )

    # -------- сохраняем --------
    message = await sync_to_async(DEVMESSAGE.objects.create)(
        text=text,
        images=[item["path"] for item in media_items] if media_items else None,
        new_text=new_text,
    )

    # -------- INFO --------
    coords = get_coords_by_address(address)

    def parse_flat_area(value):
        if not value:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)", str(value).replace(",", "."))
        return int(round(float(m.group(1)))) if m else None

    flat_area = parse_flat_area(
        await asyncio.to_thread(process_text_with_gpt_sq, new_text)
    )

    info = await sync_to_async(DEVINFO.objects.create)(
        message=message,
        price=await asyncio.to_thread(process_text_with_gpt_price, new_text),
        count_meters_flat=flat_area,
        count_meters_metro=find_nearest_metro(*coords) if coords else None,
        location=get_district_by_coords(*coords) if coords else None,
        adress=address,
        rooms=await asyncio.to_thread(process_text_with_gpt_rooms, new_text),
    )

    # -------- подписка --------
    asyncio.create_task(check_subscriptions_and_notify(info, contacts))

    # -------- канал --------
    try:
        if media_items:
            await send_media_group(bot, TELEGRAM_CHANNEL_ID, new_text, media_items)
        else:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=new_text,
                parse_mode="HTML",
            )
        logger.info(f"[CHANNEL] Пост отправлен в {TELEGRAM_CHANNEL_ID}")
    except Exception as e:
        logger.error(
            f"[CHANNEL] Ошибка отправки в канал {TELEGRAM_CHANNEL_ID}: {e}",
            exc_info=True,
        )



def is_ad_match_subscription(ad_data, subscription):
    """
    Логика:
      • Цена: [min_price .. max_price] (если заданы)
      • Комнаты: [min_rooms .. max_rooms] (если заданы), 0 → 1 (студия)
      • Площадь: [min_flat .. max_flat] (если заданы), площадь > 0
      • Округ: если subscription.district в (None, 'ANY') — не фильтруем; иначе строгое равенство
      • Метро: ИГНОРИРУЕМ subscription.max_metro_distance.
               Применяем фильтр ТОЛЬКО если metro_close == True → расстояние ≤ DEF_CLOSE_METRO.
               Если metro_close False/None → метро «не важно», объявление проходит по метро без ограничений.
    """
    DEF_CLOSE_METRO = 800.0  # можно вынести в .env при желании

    def _num(x):
        v = safe_parse_number(x)
        return v

    def _int(x):
        v = safe_parse_number(x)
        return int(v) if v is not None else None

    def _reason(ok, why):
        logger.info(f"[MATCH] {why} -> {ok}")
        return ok

    try:
        # ---- объявление (нормализация) ----
        ad_price      = _num(ad_data.get('price'))
        ad_rooms      = _int(ad_data.get('rooms'))
        ad_flat_area  = _num(ad_data.get('count_meters_flat'))
        ad_metro_dist = _num(ad_data.get('count_meters_metro'))
        ad_location   = (ad_data.get('location') or '').strip() if ad_data.get('location') is not None else None

        if ad_rooms == 0:  # студия трактуется как 1 комната
            ad_rooms = 1

        # ---- подписка (нормализация) ----
        min_price = _num(getattr(subscription, 'min_price', None))
        max_price = _num(getattr(subscription, 'max_price', None))
        min_rooms = _int(getattr(subscription, 'min_rooms', None))
        max_rooms = _int(getattr(subscription, 'max_rooms', None))
        min_flat  = _num(getattr(subscription, 'min_flat',  None))
        max_flat  = _num(getattr(subscription, 'max_flat',  None))
        metro_close = bool(getattr(subscription, 'metro_close', False))
        sub_district = getattr(subscription, 'district', None)

        # ЦЕНА
        if ad_price is not None:
            if min_price is not None and ad_price < min_price:
                return _reason(False, f"price {ad_price} < min {min_price}")
            if max_price is not None and ad_price > max_price:
                return _reason(False, f"price {ad_price} > max {max_price}")

        # КОМНАТЫ
        if ad_rooms is not None:
            if min_rooms is not None and ad_rooms < min_rooms:
                return _reason(False, f"rooms {ad_rooms} < min {min_rooms}")
            if max_rooms is not None and ad_rooms > max_rooms:
                return _reason(False, f"rooms {ad_rooms} > max {max_rooms}")

        # ПЛОЩАДЬ
        if ad_flat_area is not None and ad_flat_area > 0:
            if min_flat is not None and ad_flat_area < min_flat:
                return _reason(False, f"area {ad_flat_area} < min {min_flat}")
            if max_flat is not None and ad_flat_area > max_flat:
                return _reason(False, f"area {ad_flat_area} > max {max_flat}")

        # ОКРУГ / РАЙОН
        # Если округ «не важен» (None/ANY) — не фильтруем; иначе требуется точное совпадение.
        if sub_district not in (None, 'ANY'):
            if (ad_location or '') != str(sub_district):
                return _reason(False, f"district {ad_location} != {sub_district}")

        # МЕТРО
        # ИГНОРИРУЕМ subscription.max_metro_distance полностью.
        # Если metro_close == True → требуем расстояние ≤ DEF_CLOSE_METRO.
        # Если metro_close == False/None → метро «не важно».
        if metro_close is True:
            # применяем ограничение только если расстояние у объявления известно
            if ad_metro_dist is not None and ad_metro_dist > DEF_CLOSE_METRO:
                return _reason(False, f"metro {ad_metro_dist}m > close_limit {DEF_CLOSE_METRO}m")
            # если ad_metro_dist None — считаем неизвестным, пропускаем по метро
        # metro_close False/None → не ограничиваем по метро вовсе

        return _reason(True, "ALL OK")
    except Exception as e:
        logger.error(f"Ошибка в фильтрации подписки: {e}", exc_info=True)
        return False


async def send_notification(user_id, ad_data, message, contacts):
    try:
        # ❗ Готовый финальный текст из БД
        safe_text = message.new_text or ""

        media_paths = ad_data.get("images") or []
        media_group = []

        for idx, media_path in enumerate(media_paths[:10]):
            caption = safe_text if idx == 0 else None

            if isinstance(media_path, str) and media_path.startswith("http"):
                media_group.append(
                    InputMediaPhoto(
                        media=media_path,
                        caption=caption,
                        parse_mode="HTML"
                    )
                )
            elif media_path and os.path.exists(media_path):
                media_group.append(
                    InputMediaPhoto(
                        media=open(media_path, "rb"),
                        caption=caption,
                        parse_mode="HTML"
                    )
                )

        await asyncio.sleep(5)

        if media_group:
            if len(media_group) == 1:
                await bot2.send_photo(
                    chat_id=user_id,
                    photo=media_group[0].media,
                    caption=safe_text,
                    parse_mode="HTML"
                )
            else:
                await bot2.send_media_group(chat_id=user_id, media=media_group)
        else:
            await bot2.send_message(
                chat_id=user_id,
                text=safe_text,
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"[NOTIFY] Ошибка: {e}", exc_info=True)

async def extract_text_from_event(event):
    """
    Если сообщение — часть альбома (grouped_id), собираем подписи со всех
    сообщений альбома и берём первую непустую. Иначе — обычный text/caption.
    """
    msg = event.message
    if getattr(msg, "grouped_id", None):
        # Небольшая задержка, чтобы остальные части альбома успели прилететь
        # (по желанию — можно убрать)
        # import asyncio
        # await asyncio.sleep(0.5)

        album_msgs = await client.get_messages(msg.chat_id, min_id=msg.id - 50, max_id=msg.id + 50)
        album_msgs = [m for m in album_msgs if m and m.grouped_id == msg.grouped_id]
        album_msgs.sort(key=lambda x: x.id)
        for m in album_msgs:
            t = (m.text or "").strip()
            if t:
                return t
    return (msg.text or "").strip()

def insert_address_after_area(text: str, address: str) -> str:
    """
    Вставляет строку адреса В ОДНУ СТРОКУ:
    📍 Адрес: <code>...</code>
    строго после строки с площадью.
    """
    if not address:
        return text

    lines = text.split("\n")
    result = []
    inserted = False

    for line in lines:
        result.append(line)
        if not inserted and line.strip().startswith("👞 Площадь"):
            result.append(f"📍 Адрес: <code>{address}</code>")
            inserted = True

    if not inserted:
        result.append(f"📍 Адрес: <code>{address}</code>")

    return "\n".join(result).strip()





def remove_address_block(text: str) -> str:
    """
    Удаляет строки вида:
    📍 Адрес: ...
    Адрес: ...
    """
    lines = []
    for line in text.split("\n"):
        if re.match(r"\s*(📍\s*)?адрес\s*:", line, flags=re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()



def is_yes(value: Any) -> bool:
    """Жёсткая проверка на согласие. Нераспознанное -> False."""
    return coerce_to_bool(value, default=False) is True


def is_no(value: Any) -> bool:
    """Жёсткая проверка на отрицание. Нераспознанное -> False."""
    return coerce_to_bool(value, default=False) is False and coerce_to_bool(value, default=None) is False



async def main():
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(PHONE_NUMBER)
            code = input('Введите код из Telegram: ')
            try:
                await client.sign_in(PHONE_NUMBER, code)
            except telethon.errors.SessionPasswordNeededError:
                password = os.getenv('TELEGRAM_PASSWORD')
                await client.sign_in(password=password)

        CHANNEL_USERNAMES = [
            "devarendatoriybotpytest",
            "onmojetprogat",
        ]
        try:
            channel_entities = await asyncio.gather(
                *[client.get_entity(u) for u in CHANNEL_USERNAMES]
            )
        except Exception as e:
            logger.error(f"Ошибка при получении каналов: {e}")
            return

        @client.on(events.NewMessage(chats=channel_entities))
        async def handler_wrapper(event):
            await new_message_handler(event)

        async with client:
            logger.info("Бот запущен и слушает каналы...")
            await client.run_until_disconnected()

    finally:
        # снимаем PID-лок ТОЛЬКО при полном завершении работы бота
        if os.path.exists("bot.pid"):
            os.unlink("bot.pid")


if __name__ == "__main__":
    asyncio.run(main())