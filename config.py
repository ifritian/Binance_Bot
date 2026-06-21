"""
Авторизация в Telegram для получения сессии bot_session.session
"""
from pyrogram import Client
import config

def auth():
    client = Client(
        "bot_session",
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
    )
    
    print("📱 Начинаю авторизацию в Telegram...")
    client.connect()
    print("✅ Подключено к Telegram!")
    
    # Проверяем авторизацию
    me = client.get_me()
    print(f"✅ Авторизация успешна! Вход как {me.first_name}")
    
    client.disconnect()
    print("✅ Сессия bot_session.session создана успешно!")

if __name__ == "__main__":
    auth()