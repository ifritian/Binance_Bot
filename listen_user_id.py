"""
Слушает сообщения В РЕАЛЬНОМ ВРЕМЕНИ и показывает USER_ID
Просто запусти, потом отправь боту сообщение в личку
"""
import requests
import time

TOKEN = "8814096475:AAG3hlfj_DiW8qgTlVotpIhLAwJ-JH0g_ZI"  # твой токен
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

offset = 0

print("\n" + "="*60)
print("⏳ СЛУШАЮ СООБЩЕНИЯ...")
print("="*60)
print("Отправь боту @SquarePbot ЛЮБОЕ сообщение в личку")
print("="*60 + "\n")

while True:
    try:
        resp = requests.get(url, params={
            "offset": offset,
            "timeout": 25,
            "allowed_updates": '["message", "channel_post"]'
        })
        
        data = resp.json()
        if not data.get("ok"):
            print(f"❌ Ошибка API: {data}")
            break
        
        updates = data.get("result", [])
        
        if updates:
            print(f"\n🔔 ПОЛУЧЕНО {len(updates)} ОБНОВЛЕНИЕ(Й)!\n")
            
            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                
                if "message" in update:
                    msg = update["message"]
                    chat = msg.get("chat", {})
                    
                    if chat.get("type") == "private":
                        user_id = chat.get("id")
                        username = chat.get("username", "N/A")
                        text = msg.get("text", "[нет текста]")
                        
                        print("="*60)
                        print("✅ НАЙДЕНО ЛИЧНОЕ СООБЩЕНИЕ!")
                        print("="*60)
                        print(f"📱 USER_ID: {user_id}")
                        print(f"👤 Username: @{username}")
                        print(f"💬 Текст: {text}")
                        print("="*60)
                        print(f"\n💾 ДОБАВЬ В .env:")
                        print(f"   YOUR_USER_ID={user_id}")
                        print("="*60 + "\n")
                        
                        exit(0)
        else:
            print(".", end="", flush=True)
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n❌ Отменено.")
        break
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        time.sleep(5)