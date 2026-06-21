"""
Авторизация в Telegram для получения сессии bot_session.session
"""
from pyrogram import Client
import config

client = Client(
    "bot_session",
    api_id=config.TELEGRAM_API_ID,
    api_hash=config.TELEGRAM_API_HASH,
)

print("📱 Авторизация в Telegram...")
with client:
    print("✅ Авторизация завершена! Сессия создана.")