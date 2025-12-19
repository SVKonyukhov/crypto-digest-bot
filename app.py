from flask import Flask
import threading
import asyncio
from main import main

app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def run_bot():
    """Запускает бота в отдельном потоке"""
    asyncio.run(main())

if __name__ == '__main__':
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask на порту 10000
    app.run(host='0.0.0.0', port=10000)
