"""
Чтение новых постов канала через настоящий Telegram Bot API, а не
скрапинг превью-страницы. Работает, потому что бот добавлен админом
в канал @resultrsi (доступно, так как пользователь сам админ канала).

Почему это лучше скрапинга t.me/s/:
- Альбомы (медиагруппы) приходят как отдельные сообщения с реальным
  файлом каждой картинки - раньше такие посты приходилось полностью
  пропускать, потому что превью-страница не отдаёт прямую ссылку.
- Фото настоящего разрешения, не зависит от того, что Telegram решит
  показать в html-превью.
- Никаких хрупких html-селекторов, которые могут сломаться при
  изменении разметки страницы.

Используется обычный long polling (getUpdates) с сохранением offset,
без webhook - подходит и для постоянно работающего процесса, и для
разовых запусков (--once в GitHub Actions).
"""
import logging
from dataclasses import dataclass
from typing import Optional

import requests

import config
import queue_manager

logger = logging.getLogger(__name__)

_API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


@dataclass
class ChannelPost:
    post_id: int
    text: str                    # пустая строка, если подписи нет
    image_url: Optional[str]     # None, если картинки нет


def _get_file_url(file_id: str) -> Optional[str]:
    """Получает прямую ссылку на файл по file_id через Bot API."""
    try:
        resp = requests.get(f"{_API_BASE}/getFile", params={"file_id": file_id}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("Не удалось получить ссылку на файл %s: %s", file_id, e)
        return None

    if not data.get("ok"):
        logger.warning("Telegram API (getFile) вернул ошибку: %s", data)
        return None

    file_path = data["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"


def _delete_webhook() -> None:
    """Удаляет webhook, если он каким-то образом установлен - webhook и
    getUpdates (long polling) не могут работать одновременно с одним
    токеном, это одна из частых причин 409 Conflict."""
    try:
        resp = requests.get(f"{_API_BASE}/deleteWebhook", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            logger.info("Webhook удалён (на случай, если был установлен)")
        else:
            logger.warning("deleteWebhook вернул ошибку: %s", data)
    except requests.RequestException as e:
        logger.warning("Не удалось вызвать deleteWebhook: %s", e)


def _call_get_updates(offset: int) -> Optional[dict]:
    params = {
        "offset": offset + 1,
        # Короткий timeout вместо длинного long-poll: при разовом запуске
        # (--once, GitHub Actions) нам не нужно держать соединение 10с
        # в ожидании - это только увеличивает окно, в которое может
        # попасть конкурентный запрос и вызвать 409 Conflict.
        "timeout": 0,
        "allowed_updates": '["channel_post"]',
    }
    try:
        resp = requests.get(f"{_API_BASE}/getUpdates", params=params, timeout=15)
    except requests.RequestException as e:
        logger.warning("Не удалось получить обновления Telegram: %s", e)
        return None

    if resp.status_code == 409:
        # Кто-то ещё держит активное long-poll подключение этим же
        # токеном (другой запущенный процесс) или установлен webhook.
        # Пробуем снять webhook (если он есть) и повторить один раз -
        # если конфликт из-за другого активного процесса, повтор не
        # поможет, и это нормально, попробуем на следующем тике.
        logger.info(
            "getUpdates: 409 Conflict (обычно значит, что этим же токеном "
            "сейчас пользуется другой процесс, или висит webhook) - "
            "пробую снять webhook и повторить один раз"
        )
        _delete_webhook()
        try:
            resp = requests.get(f"{_API_BASE}/getUpdates", params=params, timeout=15)
        except requests.RequestException as e:
            logger.warning("Повтор getUpdates не удался: %s", e)
            return None
        if resp.status_code == 409:
            logger.info(
                "getUpdates: конфликт повторился - похоже, другой процесс "
                "активно использует этот токен прямо сейчас. Пропускаю "
                "проверку канала до следующего запуска."
            )
            return None

    try:
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Не удалось разобрать ответ Telegram: %s", e)
        return None

    if not data.get("ok"):
        logger.warning("Telegram API (getUpdates) вернул ошибку: %s", data)
        return None

    return data


def fetch_new_channel_posts() -> list[ChannelPost]:
    """
    Возвращает новые посты канала с момента последнего вызова.
    Обновляет сохранённый offset сам.
    """
    offset = queue_manager.get_telegram_update_offset()
    logger.info("Проверяем обновления Telegram (offset: %s)", offset)

    data = _call_get_updates(offset)
    if data is None:
        return []

    posts: list[ChannelPost] = []
    max_update_id = offset

    for update in data.get("result", []):
        max_update_id = max(max_update_id, update["update_id"])

        post = update.get("channel_post")
        if not post:
            continue  # нас интересуют только посты канала

        chat_username = (post.get("chat", {}).get("username") or "").lower()
        if chat_username != config.FOLLOWUP_CHANNEL_USERNAME.lower():
            continue  # пост из другого канала/чата - игнорируем

        text = post.get("text") or post.get("caption") or ""

        image_url = None
        photos = post.get("photo")
        if photos:
            # photo - список размеров одного и того же фото, последний
            # элемент - самое большое разрешение
            biggest = photos[-1]
            image_url = _get_file_url(biggest["file_id"])

        if image_url:
            logger.info("📸 Пост %s: получено настоящее фото через Bot API", post["message_id"])
        elif text:
            logger.info("Пост %s: есть текст, картинки нет", post["message_id"])

        posts.append(ChannelPost(post_id=post["message_id"], text=text, image_url=image_url))

    if max_update_id > offset:
        queue_manager.set_telegram_update_offset(max_update_id)

    result = [p for p in posts if p.text or p.image_url]
    logger.info("После фильтрации: %s постов с текстом или картинкой", len(result))
    return result