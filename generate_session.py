"""
Одноразовый скрипт: логинимся в Telegram и получаем session string.
Запускать ТОЛЬКО локально, руками. Telegram пришлёт код подтверждения
в приложение/SMS на номер, который ты укажешь.

После получения строки - вставь её в .env как TELEGRAM_SESSION_STRING
и больше этот скрипт не запускай (и не публикуй вывод никому!).
"""
from pyrogram import Client

import config

with Client(
    "session_gen",
    api_id=config.TELEGRAM_API_ID,
    api_hash=config.TELEGRAM_API_HASH,
    in_memory=True,
) as app:
    session_string = app.export_session_string()
    print("\n\n=== ТВОЙ SESSION STRING (сохрани в .env как TELEGRAM_SESSION_STRING) ===\n")
    print(session_string)
    print("\n=== НЕ ПУБЛИКУЙ ЭТУ СТРОКУ НИГДЕ - это полный доступ к твоему аккаунту ===\n")