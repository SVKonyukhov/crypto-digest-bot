import asyncio
import logging
import feedparser
import json
import os
import threading
from datetime import datetime, timedelta
from time import mktime
from flask import Flask
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from openai import AsyncOpenAI
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError('TELEGRAM_TOKEN и OPENAI_API_KEY должны быть в .env файле!')

# RSS каналы
RSS_FEEDS = [
    'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'https://bitcoinist.com/feed/',
    'https://crypto.news/feed/',
    'https://news.bitcoin.com/feed/',
    'https://cryptobriefing.com/feed/'
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask для health check
app = Flask(__name__)

# Aiogram
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Флаг для работы polling
polling_active = False

def clean_html(html_text):
    """Очищает HTML от тегов"""
    if not html_text:
        return ''
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        return soup.get_text(separator=', ', strip=True)[:400]
    except:
        return html_text[:400]

def get_recent_news(hours=None, limit_per_feed=10):
    """
    Получает новости из RSS каналов.
    
    Args:
        hours: Количество часов для фильтра (None = все новости без фильтра)
        limit_per_feed: Макс кол-во новостей с одного канала
    
    Returns:
        list: Отсортированный список новостей
    """
    news_items = []
    time_threshold = datetime.now() - timedelta(hours=hours) if hours else None
    
    logger.info(f'📡 Загрузка RSS (фильтр: {hours if hours else "без фильтра"} часов)')
    
    for url in RSS_FEEDS:
        try:
            # Парсим RSS
            feed = feedparser.parse(url)
            
            # Проверяем валидность канала
            if not feed.entries:
                logger.warning(f'⚠️  {url}: Нет новостей (пустой канал)')
                continue
            
            logger.info(f'✓ {url}: найдено {len(feed.entries)} записей')
            
            for entry in feed.entries[:limit_per_feed]:
                try:
                    # Извлекаем дату
                    pubtime = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pubtime = datetime.fromtimestamp(mktime(entry.published_parsed))
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        pubtime = datetime.fromtimestamp(mktime(entry.updated_parsed))
                    else:
                        pubtime = datetime.now()
                    
                    # Фильтруем по времени (если установлен фильтр)
                    if time_threshold and pubtime < time_threshold:
                        continue
                    
                    # Добавляем новость
                    news_items.append({
                        'title': entry.get('title', 'Без заголовка'),
                        'summary': clean_html(entry.get('summary', '')),
                        'link': entry.get('link', ''),
                        'source': feed.feed.get('title', 'Unknown') if hasattr(feed, 'feed') else 'Unknown',
                        'published': pubtime.isoformat()
                    })
                    
                except Exception as e:
                    logger.error(f'❌ Ошибка обработки статьи: {e}')
                    continue
        
        except Exception as e:
            logger.error(f'❌ {url}: Ошибка парсинга - {type(e).__name__}: {str(e)[:100]}')
            continue
    
    logger.info(f'📊 Всего собрано новостей: {len(news_items)}')
    return sorted(news_items, key=lambda x: x.get('published', ''), reverse=True)

async def generate_digest(news_data, period_hours=None):
    """Генерирует дайджест через OpenAI"""
    
    if not news_data:
        return None
    
    # Подготавливаем текст для OpenAI
    news_text = '\n\n'.join([
        f"Заголовок: {item['title']}\nИсточник: {item['source']}\nСсылка: {item['link']}"
        for item in news_data[:20]  # Максимум 20 новостей
    ])
    
    period_text = f"за последние {period_hours} часов" if period_hours else "без временных ограничений"
    
    prompt = f"""Ты - профессиональный аналитик криптовалютного рынка.

Создай краткий дайджест новостей криптовалют {period_text}.

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:
1. ИСКЛЮЧИТЕЛЬНО на русском языке
2. Структура ответа:
   📰 <b>Основные новости:</b> 2-3 ключевых события
   📈 <b>Движение рынка:</b> анализ цен BTC, ETH, рыночные тренды
   🔔 <b>Важные обновления:</b> регуляция, биржи, проекты, токены
   💡 <b>Аналитика:</b> краткий прогноз

3. Форматирование:
   - Каждый раздел - отдельный абзац (используй <b></b> для заголовков)
   - Максимум 500 символов
   - Используй <a href="URL">текст</a> для ссылок
   - Эмодзи в начале каждого раздела

НОВОСТИ ДЛЯ АНАЛИЗА:
{news_text}

Создай дайджест прямо сейчас."""

    try:
        logger.info('🤖 Запрос к OpenAI...')
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.5,
            max_tokens=2000
        )
        result = response.choices[0].message.content
        logger.info('✓ OpenAI ответил')
        return result
    except Exception as e:
        logger.error(f'❌ Ошибка OpenAI: {e}')
        return None

# ===== КОМАНДЫ БОТА =====

@router.message(Command('start'))
async def cmd_start(message: types.Message):
    await message.answer(
        '<b>👋 Добро пожаловать в Crypto News Bot!</b>\n\n'
        '<b>Доступные команды:</b>\n'
        '/digest - <i>полный дайджест (все последние новости)</i>\n'
        '/digest12 - <i>новости за последние 12 часов</i>\n'
        '/digest6 - <i>новости за последние 6 часов</i>\n\n'
        '💡 Бот собирает новости из топ-5 криптовалютных источников и анализирует их через OpenAI'
    )

@router.message(Command('digest'))
async def cmd_digest(message: types.Message):
    """Полный дайджест БЕЗ фильтра времени"""
    status_msg = await message.answer('⏳ Собираю последние новости со всех источников...')
    
    try:
        logger.info('Запрос: /digest (все новости)')
        news = await asyncio.to_thread(get_recent_news, None, 15)
        
        if not news:
            await status_msg.edit_text(
                '❌ <b>Не удалось получить новости</b>\n\n'
                'Возможные причины:\n'
                '• RSS каналы временно недоступны\n'
                '• Проблема с интернет соединением\n\n'
                'Попробуй позже (/digest6 или /digest12)'
            )
            return
        
        logger.info(f'Получено {len(news)} новостей')
        await status_msg.edit_text(f'🔄 Анализирую {len(news)} новостей через AI...')
        
        # Генерируем дайджест
        digest_text = await generate_digest(news, period_hours=None)
        
        await status_msg.delete()
        
        if digest_text:
            # Если текст больше 4096 символов, разбиваем на части
            if len(digest_text) > 4096:
                parts = [digest_text[i:i+4096] for i in range(0, len(digest_text), 4096)]
                for part in parts:
                    await message.answer(part, disable_web_page_preview=True)
            else:
                await message.answer(digest_text, disable_web_page_preview=True)
        else:
            # Fallback: показываем простой список новостей
            simple_digest = f'📰 <b>Последние новости ({len(news)} шт)</b>\n\n'
            for idx, item in enumerate(news[:10], 1):
                simple_digest += f'{idx}. <a href="{item["link"]}">{item["title"][:80]}</a>\n'
                simple_digest += f'   <i>{item["source"]}</i>\n'
            
            if len(simple_digest) > 4096:
                parts = [simple_digest[i:i+4096] for i in range(0, len(simple_digest), 4096)]
                for part in parts:
                    await message.answer(part, disable_web_page_preview=True)
            else:
                await message.answer(simple_digest, disable_web_page_preview=True)
    
    except Exception as e:
        logger.error(f'Ошибка в /digest: {e}')
        await status_msg.edit_text(f'❌ Ошибка: {str(e)[:200]}')

@router.message(Command('digest12'))
async def cmd_digest_12h(message: types.Message):
    """Дайджест за 12 часов"""
    status_msg = await message.answer('⏳ Собираю новости за последние 12 часов...')
    
    try:
        logger.info('Запрос: /digest12 (12 часов)')
        news = await asyncio.to_thread(get_recent_news, 12, 10)
        
        if not news:
            await status_msg.edit_text(
                '❌ <b>Новостей за последние 12 часов не найдено</b>\n\n'
                'Попробуй /digest (все новости) или /digest6'
            )
            return
        
        logger.info(f'Получено {len(news)} новостей')
        await status_msg.edit_text(f'🔄 Анализирую {len(news)} новостей...')
        
        digest_text = await generate_digest(news, period_hours=12)
        
        await status_msg.delete()
        
        if digest_text:
            if len(digest_text) > 4096:
                parts = [digest_text[i:i+4096] for i in range(0, len(digest_text), 4096)]
                for part in parts:
                    await message.answer(part, disable_web_page_preview=True)
            else:
                await message.answer(digest_text, disable_web_page_preview=True)
        else:
            simple_digest = f'📰 <b>Новости за 12 часов ({len(news)} шт)</b>\n\n'
            for idx, item in enumerate(news[:10], 1):
                simple_digest += f'{idx}. {item["title"][:100]}\n'
            await message.answer(simple_digest[:4096], disable_web_page_preview=True)
    
    except Exception as e:
        logger.error(f'Ошибка в /digest12: {e}')
        await status_msg.edit_text(f'❌ Ошибка: {str(e)[:200]}')

@router.message(Command('digest6'))
async def cmd_digest_6h(message: types.Message):
    """Дайджест за 6 часов"""
    status_msg = await message.answer('⏳ Собираю новости за последние 6 часов...')
    
    try:
        logger.info('Запрос: /digest6 (6 часов)')
        news = await asyncio.to_thread(get_recent_news, 6, 10)
        
        if not news:
            await status_msg.edit_text(
                '❌ <b>Новостей за последние 6 часов не найдено</b>\n\n'
                'Попробуй /digest (все новости) или /digest12'
            )
            return
        
        logger.info(f'Получено {len(news)} новостей')
        await status_msg.edit_text(f'🔄 Анализирую {len(news)} новостей...')
        
        digest_text = await generate_digest(news, period_hours=6)
        
        await status_msg.delete()
        
        if digest_text:
            if len(digest_text) > 4096:
                parts = [digest_text[i:i+4096] for i in range(0, len(digest_text), 4096)]
                for part in parts:
                    await message.answer(part, disable_web_page_preview=True)
