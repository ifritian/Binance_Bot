"""
Чтение новых постов публичного канала через t.me/s/<channel>
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config
import queue_manager

logger = logging.getLogger(__name__)

_PREVIEW_URL = config.TELEGRAM_PREVIEW_URL.format(channel=config.FOLLOWUP_CHANNEL_USERNAME)
_BG_IMAGE_RE = re.compile(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)")


@dataclass
class ChannelPost:
    post_id: int
    text: str
    image_url: Optional[str]


def fetch_new_channel_posts() -> list[ChannelPost]:
    last_id = queue_manager.get_last_message_id()
    logger.info("Проверяем канал @%s (последний обработанный пост: %s)",
                config.FOLLOWUP_CHANNEL_USERNAME, last_id)

    try:
        resp = requests.get(_PREVIEW_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Не удалось загрузить страницу канала: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")
    logger.info("Найдено всего постов на странице: %s", len(messages))

    parsed: list[ChannelPost] = []
    for msg_div in messages:
        data_post = msg_div.get("data-post", "")
        match = re.search(r"/(\d+)$", data_post)
        if not match:
            continue
        post_id = int(match.group(1))

        if post_id <= last_id:
            continue

        text_div = msg_div.find("div", class_="tgme_widget_message_text")
        text = text_div.get_text(separator="\n").strip() if text_div else ""

        image_url = None
        photo_wrap = msg_div.find("a", class_="tgme_widget_message_photo_wrap")
        if photo_wrap and photo_wrap.get("style"):
            bg_match = _BG_IMAGE_RE.search(photo_wrap["style"])
            if bg_match:
                image_url = bg_match.group(1)

        if image_url:
            logger.info("📸 Пост %s: найдена картинка %s", post_id, image_url[:60])
        elif text:
            logger.info("Пост %s: есть текст, картинки нет", post_id)
        else:
            logger.info("Пост %s: ни текста, ни картинки (вероятно альбом) - пропускаю", post_id)

        parsed.append(ChannelPost(post_id=post_id, text=text, image_url=image_url))

    parsed.sort(key=lambda p: p.post_id)

    result = [p for p in parsed if p.text or p.image_url]
    
    # ИСПРАВЛЕНИЕ: сохраняй ID только отфильтрованных постов!
    if result:
        max_id = max(p.post_id for p in result)
        queue_manager.set_last_message_id(max_id)
    
    logger.info("После фильтрации: %s постов с текстом или картинкой", len(result))
    return result