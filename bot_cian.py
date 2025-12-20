import asyncio
import os
from dev_bot import remove_address_block, insert_address_after_area
import aiohttp
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from dotenv import load_dotenv
from telegram import InputMediaPhoto
from telegram.error import RetryAfter
from asgiref.sync import sync_to_async
import django
from aiogram.types import InputMediaPhoto
import time
import undetected_chromedriver as uc
from aiogram.types import InputMediaPhoto
from aiogram.exceptions import TelegramRetryAfter
from dev_bot import process_text_with_gpt2
from district import get_coords_by_address, get_district_by_coords
from make_info import process_text_with_gpt_adress, process_text_with_gpt_price, process_text_with_gpt_sq, \
    process_text_with_gpt_rooms
from meters import find_nearest_metro
from proccess import process_text_with_gpt

# Настроим Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# Импортируем модель MESSAGE

# Загружаем переменные окружения

from main.models import MESSAGE, INFO, Subscription

# Загружаем переменные окружения
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
# Настройка логирования
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

bot2 = Bot(token=os.getenv("TOKEN3"))


async def send_images_with_text(bot, chat_id, text, images):
    """
    Шлём максимум 8 фото, пропуская первые 2 (обычно логотипы CIAN).
    Первое реальное фото несёт caption; если фото нет — отправляем просто текст.
    В конец добавляем цитату с HTML-ссылкой на бота.
    """
    from aiogram.types import InputMediaPhoto

    quote = ("\n\n— <i>Настройте фильтры в "
             "<a href='https://t.me/arendatoriy_find_bot'>боте</a> "
             "и получайте только подходящие варианты</i>")

    base = escape_html(text or "")
    caption = base + quote

    usable = (images or [])[2:10]  # пропускаем 2, берём до 8
    if not usable:
        await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
        return

    media_group = []
    for idx, img_url in enumerate(usable):
        if idx == 0:
            media_group.append(InputMediaPhoto(media=img_url, caption=caption, parse_mode="HTML"))
        else:
            media_group.append(InputMediaPhoto(media=img_url))

    if len(media_group) == 1:
        await bot.send_photo(chat_id=chat_id, photo=media_group[0].media, caption=caption, parse_mode="HTML")
    else:
        await bot.send_media_group(chat_id=chat_id, media=media_group)



from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def escape_html(text: str) -> str:
    if text is None:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def escape_attr(text: str) -> str:
    if text is None:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

def escape_md_v2(text):
    special_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in special_chars else char for char in text)


import os
import re
import time
import logging

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def _create_uc_driver(headless: bool = False):
    options = uc.ChromeOptions()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")

    # 🔑 ВАЖНО: реальный user-data-dir
    profile_dir = os.path.join(os.getcwd(), "chrome_profile")
    options.add_argument(f"--user-data-dir={profile_dir}")

    logging.warning("=== UC START with user profile ===")

    driver = uc.Chrome(
        options=options,
        use_subprocess=True
    )
    driver.set_page_load_timeout(60)
    return driver




async def fetch_page_data(url: str):
    """
    Async Playwright версия для CIAN.
    БЕЗ networkidle — CIAN его не даёт.
    """
    from playwright.async_api import async_playwright
    import asyncio
    import os
    import logging

    profile_dir = os.path.join(os.getcwd(), "pw_profile")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                viewport={"width": 1920, "height": 1080},
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            page = await browser.new_page()

            logging.info(f"Открываю страницу (Playwright async): {url}")

            # ⬇️ ВАЖНО: domcontentloaded вместо networkidle
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # ⬇️ ждём реальный DOM-элемент, а не "тишину сети"
            try:
                await page.wait_for_selector("body", timeout=15000)
            except:
                pass

            # небольшая пауза, чтобы догрузился контент
            await asyncio.sleep(3)

            # текст страницы
            page_text = await page.inner_text("body")

            # картинки
            images = []
            img_elements = await page.query_selector_all("img")
            for img in img_elements:
                src = await img.get_attribute("src")
                if src and src.startswith(("http://", "https://")):
                    images.append(src)
                if len(images) >= 12:
                    break

            await browser.close()
            return page_text.strip(), images

    except Exception as e:
        logging.error(f"Playwright ошибка: {e}", exc_info=True)
        return "", []



@sync_to_async
def save_message_to_db(text, images, new_text):
    """Сохранение объявления в БД."""
    return MESSAGE.objects.create(text=text, images=images, new_text=new_text)



async def fetch_message_from_db():
    """Получение последнего сообщения из базы"""
    return await sync_to_async(lambda: MESSAGE.objects.last())()

async def download_images(images):
    """Загружает изображения и сохраняет ссылки в БД"""
    async with aiohttp.ClientSession() as session:
        filenames = []
        for index, img_url in enumerate(images):
            async with session.get(img_url) as response:
                if response.status == 200:
                    filenames.append(img_url)  # Сохраняем ссылки вместо файлов
        return filenames


@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Привет! Отправь мне ссылку, и я сохраню текст и изображения.")

async def check_subscriptions_and_notify(info_instance):
    # Получаем все активные подписки
    subscriptions = await sync_to_async(list)(Subscription.objects.filter(is_active=True))

    # Получаем данные объявления
    ad_data = {
        'price': info_instance.price,
        'rooms': info_instance.rooms,
        'count_meters_flat': info_instance.count_meters_flat,  # Добавлено поле площади
        'location': info_instance.location,
        'count_meters_metro': info_instance.count_meters_metro,
        'address': info_instance.adress,
        'images': info_instance.message.images,
        'description': info_instance.message.new_text
    }

    for subscription in subscriptions:
        if await sync_to_async(is_ad_match_subscription)(ad_data, subscription):
            await send_notification(subscription.user_id, ad_data, info_instance.message)

def is_ad_match_subscription(ad_data, subscription):
    """
    Соответствие объявления подписке (под новые кнопки цены):
      ЦЕНА:
        1) "До 35 000₽"         -> min=None,  max=35000
        2) "35–65 тыс. ₽"       -> min=35000, max=65000
        3) "50–100 тыс. ₽"      -> min=50000, max=100000
        4) "Не важно"           -> min=None,  max=None  (фильтр цены не применяется)

      Другое:
        - Комнаты: 0 -> 1 (студия = 1 комната)
        - Площадь: сверяем только если > 0
        - Район: игнорируем, если None/ 'ANY'
        - Метро: объявление подходит, если расстояние <= лимита
    """
    try:
        ad_price = safe_parse_number(ad_data.get('price'))
        ad_rooms = safe_parse_number(ad_data.get('rooms'))
        ad_flat_area = safe_parse_number(ad_data.get('count_meters_flat'))
        ad_metro_distance = safe_parse_number(ad_data.get('count_meters_metro'))

        # Студия как 1 комната
        if ad_rooms == 0:
            ad_rooms = 1

        # ---------- ЦЕНА ----------
        # Если выбрано "Не важно" -> min_price/max_price должны быть None
        min_price = getattr(subscription, 'min_price', None)
        max_price = getattr(subscription, 'max_price', None)

        if ad_price is not None:
            if min_price is not None and ad_price < min_price:
                return False
            if max_price is not None and ad_price > max_price:
                return False
        # Если ad_price None — не валим объявление по цене, оставляем шанс другим фильтрам

        # ---------- КОМНАТЫ ----------
        if ad_rooms is not None:
            if getattr(subscription, 'min_rooms', None) is not None and int(ad_rooms) < subscription.min_rooms:
                return False
            if getattr(subscription, 'max_rooms', None) is not None and int(ad_rooms) > subscription.max_rooms:
                return False

        # ---------- ПЛОЩАДЬ ----------
        if ad_flat_area and ad_flat_area > 0:
            if getattr(subscription, 'min_flat', None) is not None and ad_flat_area < subscription.min_flat:
                return False
            if getattr(subscription, 'max_flat', None) is not None and ad_flat_area > subscription.max_flat:
                return False

        # ---------- РАЙОН ----------
        sub_district = getattr(subscription, 'district', None)
        if sub_district not in (None, 'ANY'):
            # Пример: в объявлении район хранится в ad_data['location']
            if ad_data.get('location') != sub_district:
                return False

        # ---------- МЕТРО ----------
        # Условие: объявление подходит, если фактическое расстояние <= максимального лимита подписки
        max_metro = getattr(subscription, 'max_metro_distance', None)
        if ad_metro_distance is not None and max_metro is not None:
            if ad_metro_distance > max_metro:
                return False

        return True

    except Exception as e:
        logger.error(f"Ошибка в фильтрации подписки: {e}", exc_info=True)
        return False


def safe_parse_number(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(',', '.').strip()
        # оставляем только цифры и точку
        value = ''.join(c for c in value if c.isdigit() or c == '.')
    try:
        return float(value)
    except:
        return None

async def send_notification(user_id: int, ad_data: dict, message):
    """
    Отправка уведомления пользователю (aiogram v3):
    - пропускаем первые 2 картинки (логотипы),
    - берём максимум 8,
    - caption кладём на первое реальное фото,
    - добавляем в конец цитату с ссылкой на бота (HTML),
    - если картинок нет — шлём только текст.
    """
    import os
    import asyncio
    from aiogram.types import InputMediaPhoto
    try:
        from aiogram.exceptions import TelegramRetryAfter
    except Exception:
        TelegramRetryAfter = Exception  # на случай старых версий

    safe_text = message.new_text or ""

    # Добавим контакты, если их нет в тексте
    if "Контакты" not in safe_text:
        contacts = await asyncio.to_thread(process_text_with_gpt2, message.text)
        if contacts and contacts.lower() not in ['нет', 'нет.']:
            safe_text += " Контакты: " + contacts

    # Собираем итоговый HTML-caption
    quote = ("\n\n— <i>Настройте фильтры в "
             "<a href='https://t.me/arendatoriy_find_bot'>боте</a> "
             "и получайте только подходящие варианты</i>")
    caption_html = safe_text + quote

    media_paths = ad_data.get('images') or []
    usable = media_paths[2:10]  # пропускаем 2, максимум 8

    media_group = []
    for idx, media_path in enumerate(usable):
        cap = caption_html if idx == 0 else None
        if isinstance(media_path, str) and media_path.startswith("http"):
            item = InputMediaPhoto(media=media_path, caption=cap)
            if cap:
                item.parse_mode = "HTML"
            media_group.append(item)
        elif media_path and os.path.exists(media_path):
            item = InputMediaPhoto(media=open(media_path, "rb"), caption=cap)
            if cap:
                item.parse_mode = "HTML"
            media_group.append(item)

    try:
        if media_group:
            if len(media_group) == 1:
                await bot2.send_photo(chat_id=user_id, photo=media_group[0].media, caption=caption_html, parse_mode="HTML")
            else:
                await bot2.send_media_group(chat_id=user_id, media=media_group)
        else:
            await bot2.send_message(chat_id=user_id, text=caption_html, parse_mode="HTML")

        logger.info(f"[NOTIFY] Отправлено объявление пользователю {user_id}")
    except TelegramRetryAfter as e:
        logger.warning(f"[NOTIFY] Flood control, повтор через {getattr(e, 'timeout', 1)} сек.")
        await asyncio.sleep(getattr(e, 'timeout', 1))
        await send_notification(user_id, ad_data, message)
    except Exception as e:
        logger.error(f"[NOTIFY] Ошибка при отправке уведомления пользователю {user_id}: {e}", exc_info=True)


async def send_to_channel(bot, channel_id: int, new_text: str, url: str, image_urls: list[str]):
    """
    Публикуем в канал:
    - пропускаем первые 2 изображения (логотипы),
    - берём максимум 8,
    - caption ставим на первое реальное фото,
    - добавляем цитату с HTML-ссылкой на бота,
    - используем parse_mode="HTML".
    """
    from aiogram.types import InputMediaPhoto

    base = new_text or ""
    link = f"<a href='{escape_attr(url)}'>Контакты</a>"
    quote = ("\n\n— <i>Настройте фильтры в "
             "<a href='https://t.me/arendatoriy_find_bot'>боте</a> "
             "и получайте только подходящие варианты</i>")
    caption = f"{base}\n📞 {link}{quote}"

    usable = (image_urls or [])[2:10]

    if usable:
        media_group = []
        for idx, img in enumerate(usable):
            if idx == 0:
                media_group.append(InputMediaPhoto(media=img, caption=caption, parse_mode="HTML"))
            else:
                media_group.append(InputMediaPhoto(media=img))

        if len(media_group) == 1:
            await bot.send_photo(chat_id=channel_id,
                                 photo=media_group[0].media,
                                 caption=caption,
                                 parse_mode="HTML")
        else:
            await bot.send_media_group(chat_id=channel_id, media=media_group)
    else:
        await bot.send_message(chat_id=channel_id, text=caption, parse_mode="HTML")


@dp.message()
async def message_handler(message: Message):
    # 1) Берём URL
    url = (message.text or "").strip()
    if not url:
        await message.answer("Пришлите ссылку на объявление CIAN.")
        return

    await message.answer("🔍 Обрабатываю страницу, подождите...")

    # 2) Парсим страницу
    text, images = await fetch_page_data(url)
    if not text and not images:
        await message.answer("⚠️ Не удалось получить данные со страницы.")
        return

    image_urls = await download_images(images)

    # 3) GPT → человекочитаемый текст
    new_text = await asyncio.to_thread(process_text_with_gpt, text)

    # --- НОРМАЛИЗАЦИЯ (как в dev_bot.py) ---
    new_text = new_text.replace("*", "\n\n")
    lines = [line.strip() for line in new_text.split("\n") if line.strip()]
    new_text = "\n\n".join(lines)

    # --- АДРЕС (ТОЧНО КАК В dev_bot.py) ---
    address = await asyncio.to_thread(process_text_with_gpt_adress, new_text)

    # 1️⃣ удаляем возможный адрес от GPT
    new_text = remove_address_block(new_text)

    # 2️⃣ вставляем адрес после строки с площадью
    new_text = insert_address_after_area(new_text, address)

    # 4) Сохраняем сообщение в БД (БЕЗ добавок "Контакты")
    mmessage = await sync_to_async(MESSAGE.objects.create)(
        text=text,
        images=image_urls if image_urls else None,
        new_text=new_text,
    )

    # 5) INFO (для подписок)
    if new_text.lower() not in ("нет", "нет."):
        coords = get_coords_by_address(address)

        def parse_flat_area(value):
            try:
                if isinstance(value, str):
                    digits = "".join(c for c in value if c.isdigit())
                    return int(digits) if digits else None
                return int(value) if value is not None else None
            except Exception:
                return None

        flat_area = parse_flat_area(
            await asyncio.to_thread(process_text_with_gpt_sq, new_text)
        )

        info = await sync_to_async(INFO.objects.create)(
            message=mmessage,
            price=await asyncio.to_thread(process_text_with_gpt_price, new_text),
            count_meters_flat=flat_area,
            count_meters_metro=find_nearest_metro(*coords) if coords else None,
            location=get_district_by_coords(*coords) if coords else None,
            adress=address,
            rooms=await asyncio.to_thread(process_text_with_gpt_rooms, new_text),
        )

        asyncio.create_task(check_subscriptions_and_notify(info))

    # 6) Отправка в канал — текст уже ПРАВИЛЬНЫЙ
    await send_to_channel(
        bot,
        TELEGRAM_CHANNEL_ID,
        new_text,
        url,
        image_urls,
    )

    await message.answer("✅ Объявление сохранено и отправлено.")



async def main():
    await asyncio.sleep(10)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())