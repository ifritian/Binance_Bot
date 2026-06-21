"""
Чтение новых постов публичного канала через t.me/s/<channel> -
это публичная превью-страница Telegram, доступная без какой-либо
авторизации, бота или аккаунта. Подходит, потому что канал открытый.

Структура страницы (стабильна, используется во многих открытых
скраперах): каждый пост - это <div class="tgme_widget_message"
data-post="channel/123">, текст внутри
<div class="tgme_widget_message_text">, картинка - внутри
<a class="tgme_widget_message_photo_wrap" style="background-image:url('...')">.

ВАЖНОЕ ОГРАНИЧЕНИЕ: если пост - это альбом из нескольких фото
(медиагруппа), Telegram не отдаёт прямую ссылку на картинку в этой
публичной превью-странице - вместо текста показывается заглушка
"Please open Telegram to view this post". В таком случае image_url
будет None, и это правильно - не нужно угадывать картинку из других
элементов страницы (там может быть аватар канала, эмодзи-иконки и
другой мусор, не относящийся к конкретному посту).

Ограничение по объёму: страница отдаёт только последние ~20 сообщений
канала. Если бот не запускался дольше, чем успело накопиться 20+
постов, самые старые из них будут пропущены. Для опроса раз в минуту
это не проблема.
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
    text: str                    # пустая строка, если подписи нет
    image_url: Optional[str]     # None, если картинки нет или это альбом


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

        # Картинку ищем ТОЛЬКО через background-image у photo_wrap -
        # это надёжный признак конкретно фото этого сообщения.
        # Если его нет - картинки нет (например, это альбом из нескольких
        # фото, который t.me/s/ не показывает напрямую) - НЕ пытаемся
        # угадать картинку через произвольный <img>, чтобы не подхватить
        # аватар канала/эмодзи/другой мусор с разметки страницы.
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

    if parsed:
        max_id = max(p.post_id for p in parsed)
        queue_manager.set_last_message_id(max_id)

    result = [p for p in parsed if p.text or p.image_url]
    logger.info("После фильтрации: %s постов с текстом или картинкой", len(result))
    return result