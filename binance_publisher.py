"""
Публикация постов в Binance Square, включая загрузку изображений.

Формат запросов и сам flow загрузки картинок взяты из официального
открытого репозитория Binance (binance/binance-skills-hub,
skills/binance/square-post/scripts/lib.mjs) - это эталонная реализация
от самого Binance, портирована на Python 1:1.

Flow загрузки картинки:
1. POST /image/presignedUrl {imageName} -> {presignedUrl, fileTicket}
2. PUT presignedUrl - заливаем сами байты картинки прямо в S3
3. POST /image/imageStatus {fileTicket} - опрашиваем, пока status != processing
   (status 1 = готово, 2 = ошибка обработки)
4. Готовый imageUrl передаём в /content/add как imageList

Известные особенности:
- лимит 100 постов/день, 400 загрузок/день на ключ
- сервер сам парсит #хэштеги и $тикеры из текста в кликабельные ссылки
- бывает ответ 504 от /content/add без id/link при фактически успешной
  публикации - это не обязательно ошибка
"""
import logging
import re
import mimetypes
import time
from pathlib import Path
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3
_MAX_POLL_RETRIES = 10


class PublishError(Exception):
    pass


def _headers() -> dict:
    return {
        "X-Square-OpenAPI-Key": config.BINANCE_SQUARE_API_KEY,
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }


def _api_v2(endpoint: str, body: dict) -> dict:
    url = f"{config.BINANCE_SQUARE_BASE_V2}{endpoint}"
    try:
        resp = requests.post(url, json=body, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        raise PublishError(f"Сетевая ошибка ({endpoint}): {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise PublishError(f"Не удалось разобрать ответ {endpoint}: {resp.text}") from None

    if data.get("code") != "000000":
        raise PublishError(f"Ошибка {endpoint}: {data.get('code')} {data.get('message')}")
    return data.get("data", {})


def _upload_to_s3(presigned_url: str, image_path: Path, content_type: str) -> None:
    with open(image_path, "rb") as f:
        file_bytes = f.read()
    resp = requests.put(presigned_url, data=file_bytes, headers={"Content-Type": content_type}, timeout=60)
    if not resp.ok:
        raise PublishError(f"Загрузка в S3 не удалась: {resp.status_code} {resp.text}")


def _poll_image_status(file_ticket: str) -> dict:
    for attempt in range(_MAX_POLL_RETRIES):
        data = _api_v2("/image/imageStatus", {"fileTicket": file_ticket})
        if data.get("status") == 1:
            return data
        if data.get("status") == 2:
            raise PublishError(f"Обработка картинки не удалась: {data.get('failedReason')}")
        logger.info("Картинка обрабатывается на сервере... (%s/%s)", attempt + 1, _MAX_POLL_RETRIES)
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise PublishError("Превышено время ожидания обработки картинки")


def upload_image(image_path: Path) -> str:
    """Загружает картинку в Binance Square, возвращает её итоговый imageUrl."""
    image_path = Path(image_path)
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"

    logger.info("Загружаю картинку: %s", image_path.name)
    presign_data = _api_v2("/image/presignedUrl", {"imageName": image_path.name})
    presigned_url = presign_data["presignedUrl"]
    file_ticket = presign_data["fileTicket"]

    _upload_to_s3(presigned_url, image_path, content_type)
    logger.info("Картинка загружена в S3, ждём обработки...")

    status = _poll_image_status(file_ticket)
    logger.info("Картинка готова: %s", status["imageUrl"])
    return status["imageUrl"]


def publish_post(text: str, image_paths: Optional[list[Path]] = None) -> dict:
    """
    Публикует пост. Если передан image_paths (до 4 файлов) - сначала
    загружает каждую картинку, затем публикует пост с imageList.
    Если изображений нет - публикует обычный текстовый пост.

    Результат включает ключ "image_urls" (список уже загруженных
    публичных URL картинок, [] если картинок не было) - историческое
    поле не используется кросспостом в Bluesky (там картинка грузится
    как сырые байты с диска, см. main._crosspost_to_bluesky), но
    оставлено на случай, если понадобится публичная ссылка на картинку
    где-то ещё.
    """
    global _NEXT_FORMAT
    dup = _recent_duplicate(text)
    if dup:
        logger.warning("Square: пост пропущен как дубль (%s): %s", dup, " ".join(text.split())[:60])
        _NEXT_FORMAT = None
        return {"id": None, "status": "duplicate_skipped", "image_urls": []}

    body: dict = {"contentType": 1, "bodyTextOnly": text}

    image_urls: list[str] = []
    if image_paths:
        if len(image_paths) > 4:
            raise PublishError("Максимум 4 картинки на пост")
        image_urls = [upload_image(p) for p in image_paths]
        body["imageList"] = image_urls

    try:
        result = _send_content(body)
    except PublishError as e:
        # Square ограничивает число $-тикеров в посте (ошибка 220095). Вместо
        # потерянного поста повторяем с первыми MAX_CASHTAGS_RETRY тикерами,
        # остальные становятся обычным текстом.
        if "220095" not in str(e) or limit_cashtags(text, MAX_CASHTAGS_RETRY) == text:
            raise
        logger.warning("Square: слишком много $-тикеров (220095) - повторяю с %d", MAX_CASHTAGS_RETRY)
        text = limit_cashtags(text, MAX_CASHTAGS_RETRY)
        body["bodyTextOnly"] = text
        result = _send_content(body)
    result["image_urls"] = image_urls
    _log_post(text, result, bool(image_urls))
    return result


MAX_CASHTAGS_RETRY = 3
_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b")


def limit_cashtags(text: str, keep: int) -> str:
    """Оставляет $ только у первых `keep` тикеров, у остальных убирает знак $."""
    count = 0

    def repl(m):
        nonlocal count
        count += 1
        return m.group(0) if count <= keep else m.group(1)

    return _CASHTAG_RE.sub(repl, text)


_POST_LOG_MAX = 300
_NEXT_FORMAT: str | None = None


def tag_format(name: str | None) -> None:
    """Название формата для ближайшей записи журнала (см. main._post). Нужно,
    чтобы сопоставлять просмотры и реакции из приложения с форматом поста."""
    global _NEXT_FORMAT
    _NEXT_FORMAT = name


def _log_post(text: str, result: dict, has_image: bool) -> None:
    """Журнал публикаций Square (время, начало текста, id ответа Binance) -
    чтобы сопоставлять просмотры/лайки из приложения с форматом поста.
    Сбой журнала никогда не должен ломать публикацию."""
    try:
        import queue_manager
        post_id = result.get("id") if isinstance(result, dict) else None
        log = queue_manager._get("square_post_log", [])
        global _NEXT_FORMAT
        fmt, _NEXT_FORMAT = _NEXT_FORMAT, None
        log.append({
            "ts": int(time.time()),
            "fmt": fmt,
            "id": str(post_id) if post_id is not None else None,
            "head": " ".join(text.split())[:90],
            "len": len(text),
            "img": has_image,
        })
        queue_manager._set("square_post_log", log[-_POST_LOG_MAX:])
    except Exception:
        logger.warning("Не удалось записать журнал публикации Square", exc_info=True)


def publish_article(title: str, text: str, cover_path: Optional[Path] = None) -> dict:
    """
    Публикует длинную статью (contentType=2) с заголовком и опциональной
    обложкой - формат "Article" в Binance Square, отдельная вкладка от
    обычных постов. Обложка - ровно одна картинка (не список).

    Результат включает ключ "cover_url" (публичный URL уже загруженной
    обложки, None если её не было) - историческое поле, кросспост в
    Bluesky использует локальный cover_path напрямую (см.
    main.try_publish_article_post), а не этот URL.
    """
    body: dict = {"contentType": 2, "bodyTextOnly": text, "title": title}

    cover_url = None
    if cover_path is not None:
        cover_url = upload_image(cover_path)
        body["cover"] = cover_url

    result = _send_content(body)
    result["cover_url"] = cover_url
    return result


def _outcome_unknown(e: Exception) -> bool:
    """True, если запрос мог дойти до Square (тогда повтор создаст дубль)."""
    if isinstance(e, (requests.ConnectTimeout,)):
        return False
    if isinstance(e, (requests.ReadTimeout, requests.exceptions.ChunkedEncodingError)):
        return True
    msg = str(e)
    return isinstance(e, requests.ConnectionError) and any(
        k in msg for k in ("Connection aborted", "RemoteDisconnected", "Read timed out", "Connection reset"))


def _recent_duplicate(text: str) -> str | None:
    """Причина, по которой пост считаем дублем недавнего, иначе None. Смотрит журнал
    публикаций: тот же формат слишком рано (config.SAME_FORMAT_MIN_GAP_HOURS) или тот же
    текст за последние 6 часов."""
    try:
        import queue_manager
        log = queue_manager._get("square_post_log", [])
        now = time.time()
        fmt = _NEXT_FORMAT
        head = " ".join(text.split())[:90]
        gap = config.SAME_FORMAT_MIN_GAP_HOURS.get(fmt or "", 0) * 3600
        for e in reversed(log):
            age = now - e.get("ts", 0)
            if age > max(gap, 6 * 3600):
                break
            if fmt and gap and e.get("fmt") == fmt and age < gap:
                return f"формат {fmt} уже выходил {age / 3600:.1f}ч назад"
            if e.get("head") == head and age < 6 * 3600:
                return "точно такой же текст уже опубликован"
    except Exception:
        logger.warning("Проверка дублей не сработала", exc_info=True)
    return None


def _send_content(body: dict) -> dict:
    try:
        resp = requests.post(config.BINANCE_SQUARE_ENDPOINT, json=body, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        if _outcome_unknown(e):
            # Запрос ушёл, ответ не получили: пост мог создаться. Повтор дал бы
            # дубль, поэтому считаем публикацию состоявшейся (как и 504 ниже).
            logger.warning("Square: ответа нет (%s). Пост мог опубликоваться - повтор не делаю.", e)
            return {"id": None, "status": "unknown_network"}
        raise PublishError(f"Сетевая ошибка при публикации: {e}") from e

    if resp.status_code == 504:
        logger.warning(
            "Получен 504 от Binance Square. Пост может быть опубликован, "
            "но id/link недоступны. Проверь вручную в приложении."
        )
        return {"id": None, "status": "unknown_504"}

    try:
        data = resp.json()
    except ValueError:
        raise PublishError(f"Не удалось разобрать ответ Binance Square: {resp.text}") from None

    if data.get("code") != "000000":
        raise PublishError(f"Binance Square вернул ошибку: {data.get('code')} {data.get('message')}")

    post_id = data.get("data", {}).get("id")
    logger.info("Пост успешно опубликован, id=%s", post_id)
    return {"id": post_id, "status": "ok"}