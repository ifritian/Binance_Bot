"""
Промежуточный бот-форвардер:
1. Слушает сообщения от @syndicateproobot
2. Автоматически отправляет их твоему Binance_Square_Bot в личку
3. Твой основной бот распознаёт и постит

Требует второго бота! Создай нового бота через @BotFather:
/newbot -> назови (например ForwarderBot) -> получишь TOKEN
"""
import requests
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ ==========
FORWARDER_TOKEN = input("Введи токен ФОРВАРДЕРА (второго бота): ").strip()
MAIN_BOT_TOKEN = "8814096475:AAG3hlfj_DiW8qgTlVotpIhLAwJ-JH0g_ZI"  # твой основной бот

SYNDICATE_BOT_USERNAME = "syndicateproobot"  # ищем сообщения от этого бота
YOUR_USER_ID = 901375482  # твой USER_ID (который мы нашли раньше)

# ========== ОСНОВНАЯ ЛОГИКА ==========

def forward_signal(text):
    """Отправляет текст сигнала твоему основному боту в личку"""
    url = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage"
    
    try:
        resp = requests.post(url, json={
            "chat_id": YOUR_USER_ID,
            "text": text
        })
        
        if resp.status_code == 200:
            logger.info(f"✅ Сигнал отправлен твоему боту!")
            return True
        else:
            logger.error(f"❌ Ошибка отправки: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Исключение: {e}")
        return False


def listen_for_signals():
    """Слушает сообщения и форвардит от @syndicateproobot"""
    offset = 0
    
    print("\n" + "="*60)
    print("🤖 ФОРВАРДЕР ЗАПУЩЕН")
    print("="*60)
    print(f"Слушаю сообщения от @{SYNDICATE_BOT_USERNAME}...")
    print(f"Форвардю на твой бот (USER_ID: {YOUR_USER_ID})")
    print("="*60 + "\n")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{FORWARDER_TOKEN}/getUpdates"
            resp = requests.get(url, params={
                "offset": offset,
                "timeout": 25,
                "allowed_updates": '["message", "channel_post"]'
            })
            
            data = resp.json()
            
            if not data.get("ok"):
                logger.error(f"❌ API ошибка: {data}")
                time.sleep(5)
                continue
            
            updates = data.get("result", [])
            
            if updates:
                for update in updates:
                    offset = max(offset, update["update_id"] + 1)
                    
                    # Ищем сообщения
                    message = update.get("message") or update.get("channel_post")
                    
                    if not message:
                        continue
                    
                    # Получаем инфо об отправителе
                    sender = message.get("from", {})
                    sender_username = sender.get("username", "unknown").lower()
                    text = message.get("text") or message.get("caption") or ""
                    
                    # Проверяем что это от @syndicateproobot
                    if sender_username == SYNDICATE_BOT_USERNAME.lower() and text:
                        logger.info(f"🔔 Получен сигнал от @{sender_username}")
                        logger.info(f"   Текст: {text[:100]}...")
                        
                        # Форвардим!
                        if forward_signal(text):
                            logger.info(f"✅ Успешно отправлен твоему боту\n")
                        else:
                            logger.warning(f"⚠️  Не удалось отправить\n")
            else:
                print(".", end="", flush=True)
                time.sleep(1)
        
        except KeyboardInterrupt:
            print("\n\n❌ Форвардер остановлен.")
            break
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    listen_for_signals()