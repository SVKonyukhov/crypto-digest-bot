from flask import Flask
import threading
import asyncio
import sys
import os

# Импортируем функцию main из main.py
from main import dp, bot

app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def run_bot():
    """Запускает бота в отдельном потоке"""
    try:
        asyncio.run(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
    except Exception as e:
        print(f"Bot error: {e}")

if __name__ == '__main__':
    # Запускаем бота в отдельном потоке (daemon)
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask на порту 10000 с host='0.0.0.0'
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)
