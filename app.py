from flask import Flask
import threading
import asyncio
import logging
from main import bot, dp, router

app = Flask(__name__)
logger = logging.getLogger(__name__)

@app.route('/health')
def health():
    return 'OK', 200

async def run_bot():
    """Запускает бота"""
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удален успешно")
    except Exception as e:
        logger.error(f"Ошибка при удалении webhook: {e}")
    
    dp.include_router(router)
    logger.info("Бот запущен (Polling режим)")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

def start_bot():
    """Запускает бота в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == '__main__':
    # Запускаем бота в отдельном потоке (daemon)
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask на порту 10000
    app.run(host='0.0.0.0', port=10000, debug=False, threaded=True)
