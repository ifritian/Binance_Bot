"""
Слушание сигналов от @syndicateproobot через Telegram API (pyrogram).
Синхронный подход, совместимый с BlockingScheduler.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from pyrogram import Client

import config
import queue_manager
import signal_parser

logger = logging.getLogger(__name__)


@dataclass
class ChannelPost:
    post_id: int
    text: str
    image_url: Optional[str]


# Инициализируем pyrogram client (один раз)
_client = None


def _get_client() -> Client:
    """Получаем или создаём клиент."""
    global _client
    if _client is None:
        _client = Client(
            "bot_session",
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH,
        )
    return _client


def fetch_new_channel_posts() -> list[ChannelPost]:
    """
    Получаем новые сообщения от @syndicateproobot.
    Вызывается каждые ~10 минут из main.py.
    """
    client = _get_client()
    
    try:
        # Подключаемся если нужно
        if not client.is_connected:
            logger.info("🔌 Подключаюсь к Telegram API...")
            client.connect()
            logger.info("✅ Подключено!")
    except Exception as e:
        logger.warning("Не удалось подключиться к Telegram: %s", e)
        return []

    try:
        # Получаем последние сообщения от @syndicateproobot
        messages = client.get_chat_history("syndicateproobot", limit=10)
        
        result = []
        for msg in messages:
            if msg.text:
                logger.info("📨 Сообщение от @syndicateproobot: %s...", msg.text[:80])
                
                # Парсим сигнал
                signal = signal_parser.parse_signal(msg.text)
                
                if signal is not None:
                    logger.info("✅ Распознан сигнал: %s", signal.title)
                    queue_manager.set_pending_digest(signal)
                    queue_manager.log_digest_history(signal.top, signal.title)
                    
                    # Создаём ChannelPost для совместимости
                    result.append(ChannelPost(
                        post_id=msg.id,
                        text=msg.text,
                        image_url=None
                    ))
        
        return result
    
    except Exception as e:
        logger.warning("Ошибка при получении сообщений: %s", e)
        return []