from flask import Flask
import threading
import subprocess
import os
import signal
import time

app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def start_bot():
    """Запускает бота в отдельном процессе"""
    time.sleep(2)  # Даем Flask время на запуск
    try:
        subprocess.Popen([
            'python', '-c',
            'import asyncio; from main import main; asyncio.run(main())'
        ])
    except Exception as e:
        print(f"Bot start error: {e}")

if __name__ == '__main__':
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    app.run(host='0.0.0.0', port=10000)
