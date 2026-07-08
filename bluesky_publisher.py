"""
Кросспостинг постов (тех же, что уходят на Binance Square) в Bluesky
(AT Protocol) - config.BLUESKY_HANDLE / config.BLUESKY_APP_PASSWORD.

Как и telegram_publisher.py, это ОПЦИОНАЛЬНЫЙ и НЕЗАВИСИМЫЙ кросспост -
если Bluesky не настроен, main.py просто пропускает публикацию сюда, а
если запрос упал - логирует предупреждение и идёт дальше (см.
main._crosspost_to_bluesky).

Почему Bluesky вместо Threads: AT Protocol не требует Developer-портала,
App Review или добавления себя тестером - только "App password", который
создаётся в самом Bluesky (Settings -> App passwords) за 30 секунд.

AT Protocol flow:
1. POST /xrpc/com.atproto.server.createSession {identifier, password}
   -> accessJwt (токен на сессию) + did (постоянный ID аккаунта).
   Сессия НЕ кешируется между запусками бота - процесс python стартует
   заново на каждый тик (GitHub Actions job), кешировать по сути негде
   и не нужно при текущей частоте постов.
2. (если есть картинка) POST /xrpc/com.atproto.repo.uploadBlob - СЫРЫЕ
   БАЙТЫ картинки (не URL, как было у Threads!) -> blob-ссылка для embed.
3. POST /xrpc/com.atproto.repo.createRecord - сам пост (text, createdAt,
   facets для кликабельных ссылок, embed с картинкой, если есть).

Лимит текста Bluesky - 300 символов (жёстче, чем было у Threads/Square) -
обрезка делается в post_format.build_bluesky_post, сюда приходит уже
готовый текст.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_PDS_BASE = "https://bsky.social/xrpc"


class BlueskyPublishError(Exception):
    pass


def is_configured() -> bool:
    """True, если кросспостинг в Bluesky настроен (handle + app password
    заданы). Вызывающий код (main.py) должен тихо пропускать кросспост,
    если это False - НЕ считать это ошибкой, раз кросспостинг опционален."""
    return bool(config.BLUESKY_HANDLE and config.BLUESKY_APP_PASSWORD)


def _parse(resp, step: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise BlueskyPublishError(f"Не удалось разобрать ответ Bluesky ({step}): {resp.text}") from None
    if not resp.ok:
        raise BlueskyPublishError(f"Bluesky вернул ошибку ({step}): {data.get('message', data)}")
    return data


def _create_session() -> dict:
    try:
        resp = requests.post(
            f"{_PDS_BASE}/com.atproto.server.createSession",
            json={"identifier": config.BLUESKY_HANDLE, "password": config.BLUESKY_APP_PASSWORD},
            timeout=30,
        )
    except requests.RequestException as e:
        raise BlueskyPublishError(f"Сетевая ошибка при авторизации в Bluesky: {e}") from e

    data = _parse(resp, "createSession")
    if "accessJwt" not in data or "did" not in data:
        raise BlueskyPublishError(f"Bluesky не вернул accessJwt/did: {data}")
    return data


def _upload_image(access_jwt: str, image_bytes: bytes, content_type: str) -> dict:
    try:
        resp = requests.post(
            f"{_PDS_BASE}/com.atproto.repo.uploadBlob",
            data=image_bytes,
            headers={"Authorization": f"Bearer {access_jwt}", "Content-Type": content_type},
            timeout=60,
        )
    except requests.RequestException as e:
        raise BlueskyPublishError(f"Сетевая ошибка при загрузке картинки: {e}") from e

    data = _parse(resp, "uploadBlob")
    blob = data.get("blob")
    if not blob:
        raise BlueskyPublishError(f"Bluesky не вернул blob картинки: {data}")
    return blob


def _byte_facets(text: str, links: list) -> list:
    """Строит facets (кликабельные ссылки) для указанных подстрок.

    links - список пар (подстрока_в_тексте, url). AT Protocol считает
    смещения в БАЙТАХ UTF-8, а не в символах Python, поэтому кодируем
    текст целиком и ищем байтовые индексы подстроки, а не str.find по
    символам - иначе ссылки на кириллице/эмодзи в тексте сдвинули бы
    диапазон и facet указывал бы не на ту часть поста.

    Подстрока, которая не нашлась (например, обрезана при укладке в
    лимит 300 символов - см. build_bluesky_post) просто пропускается:
    лучше пост без кликабельной ссылки, чем сломанный facet."""
    encoded = text.encode("utf-8")
    facets = []
    for substring, url in links:
        sub_bytes = substring.encode("utf-8")
        start = encoded.find(sub_bytes)
        if start == -1:
            continue
        end = start + len(sub_bytes)
        facets.append(
            {
                "index": {"byteStart": start, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
    return facets


def publish_post(
    text: str,
    image_bytes: Optional[bytes] = None,
    image_content_type: str = "image/png",
    link_facets: Optional[list] = None,
) -> dict:
    """
    Публикует пост в Bluesky.

    image_bytes - СЫРЫЕ БАЙТЫ картинки (не URL) - AT Protocol требует
    именно загрузку блоба через uploadBlob, ссылкой передать нельзя (в
    отличие от Threads, где было наоборот). Вызывающий код (main.py)
    должен прочитать локальный файл картинки и передать его содержимое.

    link_facets - список пар (подстрока, url) для кликабельных ссылок -
    см. post_format.build_bluesky_post, который возвращает этот список
    готовым, синхронизированным с текстом ссылок в самом посте.
    """
    session = _create_session()
    access_jwt = session["accessJwt"]
    did = session["did"]

    record: dict = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    facets = _byte_facets(text, link_facets or [])
    if facets:
        record["facets"] = facets

    if image_bytes:
        blob = _upload_image(access_jwt, image_bytes, image_content_type)
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": [{"image": blob, "alt": "chart"}],
        }

    try:
        resp = requests.post(
            f"{_PDS_BASE}/com.atproto.repo.createRecord",
            json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            headers={"Authorization": f"Bearer {access_jwt}"},
            timeout=30,
        )
    except requests.RequestException as e:
        raise BlueskyPublishError(f"Сетевая ошибка при публикации: {e}") from e

    data = _parse(resp, "createRecord")
    logger.info("Опубликовано в Bluesky: %s", data.get("uri"))
    return data
