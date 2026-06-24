"""
Скрипт для получения твоего USER_ID.
Просто отправь любое сообщение боту в личку, затем запусти этот скрипт.
"""
import requests
import sys

# Замени на свой токен (из .env)
TELEGRAM_BOT_TOKEN = input("Введи TELEGRAM_BOT_TOKEN из .env: ").strip()

_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Получаем все updates
resp = requests.get(f"{_API_BASE}/getUpdates", params={"limit": 100, "timeout": 0})
data = resp.json()

if not data.get("ok"):
    print("❌ Ошибка API:", data)
    sys.exit(1)

# Ищем личные сообщения (не channel_post, не group)
found = False
for update in reversed(data.get("result", [])):  # последние сообщения в начале
    msg = update.get("message")
    if msg:
        chat = msg.get("chat", {})
        if chat.get("type") == "private":  # личное сообщение
            user_id = chat.get("id")
            username = chat.get("username", "N/A")
            text = msg.get("text", "[нет текста]")
            print(f"✅ Найдено личное сообщение:")
            print(f"   USER_ID: {user_id}")
            print(f"   Username: @{username}")
            print(f"   Текст: {text[:50]}")
            print(f"\n💾 ДОБАВЬ В .env:")
            print(f"   YOUR_USER_ID={user_id}")
            found = True
            break

if not found:
    print("❌ Личных сообщений боту не найдено.")
    print("Отправь сообщение боту в личку, затем запусти скрипт снова.")