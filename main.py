import asyncio
import logging
import feedparser
import json
import os
from datetime import datetime, timedelta
from time import mktime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from openai import AsyncOpenAI
from bs4 import BeautifulSoup

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("TELEGRAM_TOKEN и OPENAI_API_KEY должны быть установлены в файле .env!")

# Список RSS лент
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "http://bitcoinist.com/feed/",
    "https://crypto.news/feed/",
    "https://news.bitcoin.com/feed/",
    "https://cryptobriefing.com/feed/"
]

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

def clean_html(html_text):
    """Удаляет лишние теги из описания новостей"""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)[:400]

def get_recent_news(hours=24):
    """Парсит RSS и возвращает новости за последние N часов"""
    news_items = []
    time_threshold = datetime.now() - timedelta(hours=hours)
    
    logger.info(f"Начинаю парсинг RSS лент (за последние {hours} часов)...")
    
    for url in RSS_FEEDS:
        try:
            # Добавляем timeout для каждой ленты (максимум 5 секунд)
            feed = feedparser.parse(url, timeout=5)
            logger.info(f"Парсинг {url}: найдено {len(feed.entries)} записей")
            
            for entry in feed.entries[:5]:
                if hasattr(entry, 'published_parsed'):
                    pub_time = datetime.fromtimestamp(mktime(entry.published_parsed))
                elif hasattr(entry, 'updated_parsed'):
                    pub_time = datetime.fromtimestamp(mktime(entry.updated_parsed))
                else:
                    continue
                
                if pub_time > time_threshold:
                    news_items.append({
                        "title": entry.title,
                        "summary": clean_html(entry.get("summary", "") or ""),
                        "link": entry.link,
                        "source": feed.feed.title if hasattr(feed.feed, 'title') else "Unknown"
                    })
        except Exception as e:
            logger.error(f"Ошибка парсинга {url}: {e}")
    
    logger.info(f"Всего найдено {len(news_items)} новостей")
    return sorted(news_items, key=lambda x: x.get('title', ''))[:20]

async def generate_digest(news_data):
    """Отправляет новости в OpenAI и получает дайджест"""
    if not news_data:
        return "🔍 За последние 24 часа новостей не найдено."

    prompt = (
        "Ты крипто-редактор и SMM-специалист, пишущий для русскоязычной аудитории.\n"
        "На входе — JSON-массив новостей за последние 24 часа (на английском).\n"
        "Твоя задача: выбрать до 10 самых важных новостей и сделать один HTML-пост для Telegram.\n\n"
        "ОБЯЗАТЕЛЬНО:\n"
        "- ВЕСЬ текст на русском языке.\n"
        "- Переводи заголовки и описания, сохраняя смысл.\n"
        "- Названия компаний, тикеры (BTC, ETH) оставляй как есть.\n\n"
        "Правила оформления:\n"
        "1) Используй ТОЛЬКО эти HTML-теги: <b>, <i>, <u>, de>, <a href=\"URL\">.\n"
        "   НЕ используй <br>, <div>, <p>, <span>.\n"
        "2) Для переносов используй \\n (новая строка).\n"
        "3) Для каждой новости:\n"
        "   - <b>Заголовок на русском</b>\n"
        "   - Краткое объяснение (1-2 предложения)\n"
        "   - <a href=\"URL\">Читать далее</a>\n"
        "   - Пустая строка (\\n\\n)\n"
        "4) В конце: итог по рынку (2-3 предложения на русском).\n"
        "5) Верни ТОЛЬКО текст поста, без JSON, без комментариев.\n\n"
        f"JSON с новостями:\n{json.dumps(news_data, ensure_ascii=False, indent=2)}"
    )

    try:
        logger.info("Запрос к OpenAI...")
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты полезный ассистент для крипто-новостей."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=2000
        )
        result = response.choices[0].message.content
        logger.info("Дайджест успешно сгенерирован")
        return result
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "❌ Ошибка при генерации дайджеста. Попробуй позже."

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 <b>Привет!</b>\n\n"
        "Я собираю свежие новости с крипто-сайтов и делаю для тебя дайджесты.\n\n"
        "Команды:\n"
        "/digest — получить дайджест за 24 часа\n"
        "/digest12 — последние 12 часов\n"
        "/digest6 — последние 6 часов"
    )

@router.message(Command("digest"))
async def cmd_digest(message: types.Message):
    status_msg = await message.answer("🔍 Сканирую RSS ленты...")
    
    try:
        # TIMEOUT: максимум 30 секунд на парсинг RSS
        news = await asyncio.wait_for(
            asyncio.to_thread(get_recent_news, 24),
            timeout=30.0
        )
        
        if not news:
            await status_msg.edit_text("📭 Новостей за 24 часа не найдено.")
            return

        await status_msg.edit_text(f"🧠 Найдено {len(news)} новостей. Анализирую через AI...")
        
        digest_text = await generate_digest(news)
        
        await status_msg.delete()
        
        if len(digest_text) > 4096:
            parts = [digest_text[i:i+4096] for i in range(0, len(digest_text), 4096)]
            for part in parts:
                await message.answer(part, disable_web_page_preview=True)
        else:
            await message.answer(digest_text, disable_web_page_preview=True)
            
    except asyncio.TimeoutError:
        await status_msg.edit_text("⏱️ Timeout: RSS ленты загружались слишком долго. Попробуй позже.")
        logger.error("Timeout при парсинге RSS")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        logger.error(f"Ошибка в cmd_digest: {e}")

@router.message(Command("digest12"))
async def cmd_digest12(message: types.Message):
    status_msg = await message.answer("🔍 Сканирую RSS ленты за 12 часов...")
    
    try:
        # TIMEOUT: максимум 30 секунд
        news = await asyncio.wait_for(
            asyncio.to_thread(get_recent_news, 12),
            timeout=30.0
        )
        
        if not news:
            await status_msg.edit_text("📭 Новостей за 12 часов не найдено.")
            return

        await status_msg.edit_text(f"🧠 Найдено {len(news)} новостей. Анализирую...")
        digest_text = await generate_digest(news)
        
        await status_msg.delete()
        await message.answer(digest_text, disable_web_page_preview=True)
            
    except asyncio.TimeoutError:
        await status_msg.edit_text("⏱️ Timeout: RSS ленты загружались слишком долго. Попробуй позже.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

@router.message(Command("digest6"))
async def cmd_digest6(message: types.Message):
    status_msg = await message.answer("🔍 Сканирую RSS ленты за 6 часов...")
    
    try:
        # TIMEOUT: максимум 30 секунд
        news = await asyncio.wait_for(
            asyncio.to_thread(get_recent_news, 6),
            timeout=30.0
        )
        
        if not news:
            await status_msg.edit_text("📭 Новостей за 6 часов не найдено.")
            return

        await status_msg.edit_text(f"🧠 Найдено {len(news)} новостей. Анализирую...")
        digest_text = await generate_digest(news)
        
        await status_msg.delete()
        await message.answer(digest_text, disable_web_page_preview=True)
            
    except asyncio.TimeoutError:
        await status_msg.edit_text("⏱️ Timeout: RSS ленты загружались слишком долго. Попробуй позже.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

@router.message()
async def echo(message: types.Message):
    await message.answer(
        "Я не понимаю эту команду. Используй:\n"
        "/digest — дайджест\n"
        "/start — справка"
    )

async def main():
    dp.include_router(router)
    logger.info("Бот запущен (Polling режим)")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())

