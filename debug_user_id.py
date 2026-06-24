"""
Максимально простой скрипт - просто выводит ВСЕ обновления от Telegram
"""
import requests
import json

# Вставь свой токен прямо сюда (временно, для отладки)
TOKEN = input("Введи TELEGRAM_BOT_TOKEN: ").strip()

url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

print("\n⏳ Получаю все обновления от Telegram...\n")

try:
    resp = requests.get(url, params={"limit": 100, "timeout": 0})
    data = resp.json()
    
    if not data.get("ok"):
        print(f"❌ Ошибка API: {data}")
    else:
        updates = data.get("result", [])
        print(f"📊 Всего обновлений: {len(updates)}\n")
        
        if not updates:
            print("❌ Нет обновлений. Отправь боту новое сообщение и попробуй снова.")
        else:
            # Выводим ВСЕ сообщения
            for i, update in enumerate(reversed(updates), 1):  # последние в начале
                print(f"\n{'='*60}")
                print(f"Обновление #{i}")
                print(f"{'='*60}")
                
                # Личное сообщение
                if "message" in update:
                    msg = update["message"]
                    chat = msg.get("chat", {})
                    print(f"Тип: Личное сообщение")
                    print(f"USER_ID: {chat.get('id')}")
                    print(f"Username: @{chat.get('username', 'N/A')}")
                    print(f"Текст: {msg.get('text', '[нет текста]')[:100]}")
                
                # Пост из канала
                elif "channel_post" in update:
                    post = update["channel_post"]
                    chat = post.get("chat", {})
                    print(f"Тип: Пост из канала")
                    print(f"Канал: @{chat.get('username', 'unknown')}")
                    print(f"Текст: {post.get('text', '[нет текста]')[:100]}")
                
                else:
                    print(f"Тип: Другое")
                    print(json.dumps(update, indent=2, ensure_ascii=False)[:200])

except Exception as e:
    print(f"❌ Ошибка: {e}")