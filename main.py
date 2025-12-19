import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, BotCommand
from aiogram.types.bot_command_scope import BotCommandScopeDefault
import feedparser
import openai
from dotenv import load_dotenv
import os
import json
from aiohttp import ClientSession
import time

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения (только для локальной разработки)
if not os.getenv('RENDER'):
    load_dotenv()

# Инициализация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# RSS каналы (твои 5 каналов)
RSS_FEEDS = [
    "https://cryptonews.com/feed/",
    "https://news.bitcoin.com/feed/",
    "https://cointelegraph.com/feed/",
    "https://decrypt.co/feed/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
]

# Функция для получения новостей из RSS с обработкой ошибок
async def get_news_from_feeds(hours: int = None, limit_per_feed: int = 10):
    """
    Получает новости из RSS каналов
    
    Args:
        hours: Количество часов для фильтра (None = без фильтра, все новости)
        limit_per_feed: Максимальное кол-во новостей с одного канала
    
    Returns:
        list: Список новостей с полями title, link, source, published
    """
    all_news = []
    cutoff_time = None
    
    if hours:
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    
    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"📡 Парсинг {feed_url}")
            
            # Парсим с таймаутом 10 секунд
            feed = await asyncio.wait_for(
                asyncio.to_thread(feedparser.parse, feed_url),
                timeout=10.0
            )
            
            # Проверяем наличие entries
            if not feed.entries:
                logger.warning(f"⚠️ {feed_url}: Нет новостей (пустой канал)")
                continue
            
            # Обрабатываем каждую статью
            for entry in feed.entries[:limit_per_feed]:
                try:
                    # Извлекаем дату публикации
                    published = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        published = datetime(*entry.published_parsed[:6])
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        published = datetime(*entry.updated_parsed[:6])
                    
                    # Фильтруем по времени (если установлен фильтр)
                    if cutoff_time and published:
                        if published < cutoff_time:
                            continue
                    
                    # Создаём объект новости
                    news_item = {
                        'title': entry.get('title', 'Без заголовка'),
                        'link': entry.get('link', ''),
                        'source': feed.feed.get('title', 'Неизвестный источник'),
                        'published': published.isoformat() if published else 'Неизвестно'
                    }
                    
                    all_news.append(news_item)
                    logger.info(f"✓ Добавлена новость: {news_item['title'][:50]}")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка при обработке статьи: {e}")
                    continue
        
        except asyncio.TimeoutError:
            logger.error(f"⏱️ {feed_url}: Таймаут (сервер не отвечает)")
        except Exception as e:
            logger.error(f"❌ {feed_url}: Ошибка парсинга - {type(e).__name__}: {e}")
            continue
    
    logger.info(f"📊 Всего собрано новостей: {len(all_news)}")
    return all_news

# Функция для генерации дайджеста через OpenAI
async def generate_digest(news_list):
    """
    Генерирует дайджест через OpenAI
    """
    if not news_list:
        return None
    
    # Подготавливаем текст для OpenAI
    news_text = "\n\n".join([
        f"Заголовок: {item['title']}\nИсточник: {item['source']}\nСсылка: {item['link']}"
        for item in news_list[:20]  # Берём максимум 20 новостей
    ])
    
    prompt = f"""Ты - профессиональный аналитик криптовалютного рынка. 
    
Твоя задача - создать краткий дайджест последних новостей криптовалютного рынка на основе предоставленных материалов.

ПРАВИЛА ФОРМАТИРОВАНИЯ:
1. Ответ ИСКЛЮЧИТЕЛЬНО на русском языке
2. Используй следующую структуру:
   - 📰 **Основные новости:** (2-3 наиболее важных события)
   - 📈 **Движение рынка:** (анализ цен и трендов)
   - 🔔 **Важные обновления:** (регуляция, биржи, проекты)
   - 💡 **Аналитика:** (краткий анализ)

3. Каждый пункт - отдельный абзац, без нумерации
4. Используй эмодзи для визуальной организации
5. Максимум 500 символов
6. Вставляй источники в формате [Источник](ссылка)

НОВОСТИ ДЛЯ АНАЛИЗА:
{news_text}

Создай дайджест, следуя всем правилам выше."""

    try:
        response = await asyncio.to_thread(
            lambda: openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=600
            )
        )
        
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"❌ Ошибка OpenAI: {e}")
        return None

# Регистрация команд
async def set_commands():
    commands = [
        BotCommand(command="digest", description="📰 Полный дайджест (все последние новости)"),
        BotCommand(command="digest6", description="⏱️ Новости за последние 6 часов"),
        BotCommand(command="digest12", description="⏱️ Новости за последние 12 часов"),
        BotCommand(command="start", description="Начать работу с ботом"),
        BotCommand(command="help", description="Справка по командам"),
    ]
    await bot.set_my_commands(commands, BotCommandScopeDefault())

# Обработчик /start
@dp.message(Command("start"))
async def handle_start(message: Message):
    await message.reply(
        "👋 Добро пожаловать в Crypto News Bot!\n\n"
        "Доступные команды:\n"
        "/digest — получить полный дайджест (все последние новости)\n"
        "/digest6 — новости за последние 6 часов\n"
        "/digest12 — новости за последние 12 часов\n\n"
        "Выбери команду для получения дайджеста новостей криптовалют 📰"
    )

# Обработчик /digest (без временного фильтра)
@dp.message(Command("digest"))
async def handle_digest_all(message: Message):
    status_msg = await message.reply("⏳ Собираю новости со всех источников...")
    
    try:
        # Получаем ВСЕ новости без фильтра по времени
        news = await get_news_from_feeds(hours=None, limit_per_feed=15)
        
        if not news:
            await status_msg.edit_text("❌ Не удалось получить новости. Попробуй позже.")
            return
        
        # Генерируем дайджест через OpenAI
        digest = await generate_digest(news)
        
        if digest:
            await status_msg.edit_text(digest)
        else:
            # Если OpenAI не сработал, показываем простой список
            simple_digest = "📰 **Последние новости:**\n\n"
            for item in news[:10]:
                simple_digest += f"• {item['title']}\n  Источник: {item['source']}\n  🔗 {item['link']}\n\n"
            await status_msg.edit_text(simple_digest[:4096])  # Лимит Telegram
        
    except Exception as e:
        logger.error(f"Ошибка в /digest: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# Обработчик /digest6
@dp.message(Command("digest6"))
async def handle_digest_6h(message: Message):
    status_msg = await message.reply("⏳ Собираю новости за последние 6 часов...")
    
    try:
        news = await get_news_from_feeds(hours=6, limit_per_feed=10)
        
        if not news:
            await status_msg.edit_text("❌ Новостей за последние 6 часов не найдено.")
            return
        
        digest = await generate_digest(news)
        
        if digest:
            await status_msg.edit_text(f"**Новости за 6 часов:**\n\n{digest}")
        else:
            simple_digest = f"📰 **Новости за 6 часов ({len(news)} шт):**\n\n"
            for item in news[:10]:
                simple_digest += f"• {item['title']}\n"
            await status_msg.edit_text(simple_digest[:4096])
        
    except Exception as e:
        logger.error(f"Ошибка в /digest6: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# Обработчик /digest12
@dp.message(Command("digest12"))
async def handle_digest_12h(message: Message):
    status_msg = await message.reply("⏳ Собираю новости за последние 12 часов...")
    
    try:
        news = await get_news_from_feeds(hours=12, limit_per_feed=10)
        
        if not news:
            await status_msg.edit_text("❌ Новостей за последние 12 часов не найдено.")
            return
        
        digest = await generate_digest(news)
        
        if digest:
            await status_msg.edit_text(f"**Новости за 12 часов:**\n\n{digest}")
        else:
            simple_digest = f"📰 **Новости за 12 часов ({len(news)} шт):**\n\n"
            for item in news[:10]:
                simple_digest += f"• {item['title']}\n"
            await status_msg.edit_text(simple_digest[:4096])
        
    except Exception as e:
        logger.error(f"Ошибка в /digest12: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# Обработчик /help
@dp.message(Command("help"))
async def handle_help(message: Message):
    await message.reply(
        "📚 **Справка:**\n\n"
        "/digest — полный дайджест всех последних новостей (независимо от даты)\n"
        "/digest6 — только новости за последние 6 часов\n"
        "/digest12 — только новости за последние 12 часов\n\n"
        "Каждый дайджест содержит анализ от AI с выделением ключевых событий 🚀"
    )

# Функция для запуска бота
async def main():
    logger.info("🚀 Запуск бота...")
    
    # Удаляем старый webhook
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✓ Webhook очищен")
    except Exception as e:
        logger.warning(f"Webhook не был установлен: {e}")
    
    # Регистрируем команды
    await set_commands()
    logger.info("✓ Команды зарегистрированы")
    
    # Запускаем polling
    logger.info("📡 Запуск polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_allowed_updates())

if __name__ == "__main__":
    asyncio.run(main())
