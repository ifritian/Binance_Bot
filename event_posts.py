"""
Событийные форматы Square без LLM (все числа и названия берутся из
источников кодом, текст по шаблону):

1. "Индекс страха и жадности" - раз в сутки в заданный час UTC
   (api.alternative.me/fng) + вопрос читателям.
2. "Анонс Binance" - новый листинг монеты (раздел New Cryptocurrency
   Listing на binance.com): повод для поста сразу после анонса, пока это
   обсуждают.

Источники неофициальные/внешние - любая ошибка сети или неожиданный формат
ответа = тихий пропуск (None), а не падение бота.
"""
import logging
import random
import re
from datetime import datetime, timezone

import requests

import config
import post_format

logger = logging.getLogger(__name__)

FNG_URL = "https://api.alternative.me/fng/?limit=2"
ANNOUNCEMENTS_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&catalogId=48&pageNo=1&pageSize=10"
)
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SquareBot/1.0)"}
ANNOUNCEMENT_MAX_AGE_HOURS = 6.0

_FNG_RU = {
    "Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально",
    "Greed": "жадность", "Extreme Greed": "крайняя жадность",
}


# ---------------- Индекс страха и жадности ----------------

def fetch_fng() -> dict | None:
    """-> {"value": int, "label": str, "prev": int | None} или None."""
    try:
        resp = requests.get(FNG_URL, timeout=15, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        rows = resp.json()["data"]
        value = int(rows[0]["value"])
        label = str(rows[0]["value_classification"])
        prev = int(rows[1]["value"]) if len(rows) > 1 else None
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Индекс страха и жадности: не удалось получить данные: %s", e)
        return None
    if not 0 <= value <= 100:
        return None
    return {"value": value, "label": label, "prev": prev}


def _fng_question(value: int, en: bool, rng) -> str:
    if value <= 40:
        opts = (["Fear like this is usually where people sell, or where they start buying? 🤔",
                 "Is $BTC a buy when everyone is scared, or does the pain continue?"] if en else
                ["Страх на рынке: это момент, когда продают, или когда начинают присматриваться? 🤔",
                 "Когда все боятся, $BTC стоит покупать или боль ещё впереди?"])
    elif value >= 60:
        opts = (["When everyone is greedy, are you taking profit or holding on? 🤔",
                 "Is $BTC overheated here, or is there room to run?"] if en else
                ["Когда все жадничают, вы фиксируете прибыль или держите? 🤔",
                 "$BTC здесь перегрет или запас хода ещё есть?"])
    else:
        opts = (["The market can't decide. Which way breaks first? 👇"] if en else
                ["Рынок не может определиться. В какую сторону сломается первым? 👇"])
    return rng.choice(opts)


def build_fng_text(data: dict, rng=None) -> str:
    rng = rng or random
    en = config.SQUARE_TEMPLATE_LANG == "en"
    v, label, prev = data["value"], data["label"], data["prev"]
    if en:
        lines = [f"😱 Crypto Fear & Greed Index: {v} ({label})"]
        if prev is not None:
            diff = v - prev
            lines.append(f"Yesterday: {prev}" + (f" ({diff:+d})" if diff else " (no change)"))
        disclaimer = "Not financial advice."
    else:
        lines = [f"😱 Индекс страха и жадности: {v} ({_FNG_RU.get(label, label).lower()})"]
        if prev is not None:
            diff = v - prev
            lines.append(f"Вчера: {prev}" + (f" ({diff:+d})" if diff else " (без изменений)"))
        disclaimer = post_format.DISCLAIMER
    return "\n".join(lines) + "\n\n" + _fng_question(v, en, rng) + "\n\n" + disclaimer + "\n\n#BTC #Sentiment"


# ---------------- Анонсы Binance ----------------

_LISTING_RE = re.compile(r"will\s+list\s+(.+?)\s*\(([A-Z0-9]{2,12})\)", re.IGNORECASE)


def parse_listing_title(title: str) -> tuple[str, str] | None:
    """'Binance Will List Foo (FOO) with Seed Tag Applied' -> ('Foo', 'FOO')."""
    m = _LISTING_RE.search(title or "")
    if not m:
        return None
    return m.group(1).strip(), m.group(2).upper()


def fetch_listing_announcements(now_ts: float, max_age_hours: float = ANNOUNCEMENT_MAX_AGE_HOURS,
                                payload: dict | None = None) -> list[dict]:
    """Свежие анонсы листингов: [{"code", "name", "ticker", "ts"}] (новые первыми).
    payload - для тестов (готовый JSON ответа)."""
    if payload is None:
        try:
            resp = requests.get(ANNOUNCEMENTS_URL, timeout=15, headers=_HTTP_HEADERS)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Анонсы Binance: не удалось получить данные: %s", e)
            return []
    try:
        articles = payload["data"]["catalogs"][0]["articles"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Анонсы Binance: неожиданный формат ответа")
        return []
    out = []
    for a in articles:
        parsed = parse_listing_title(a.get("title", ""))
        if not parsed:
            continue
        try:
            ts = float(a["releaseDate"]) / 1000.0
        except (KeyError, ValueError, TypeError):
            continue
        if now_ts - ts > max_age_hours * 3600 or ts > now_ts + 600:
            continue
        out.append({"code": str(a.get("code") or a.get("id") or a.get("title")), "name": parsed[0],
                    "ticker": parsed[1], "ts": ts})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def build_announcement_text(item: dict, rng=None) -> str:
    rng = rng or random
    en = config.SQUARE_TEMPLATE_LANG == "en"
    name, t = item["name"], item["ticker"]
    if en:
        head = f"🆕 Binance just announced it will list {name} (${t})."
        q = rng.choice([f"New listings move fast. Are you watching ${t} or skipping it? 👇",
                        f"Would you touch ${t} in the first hours, or wait for the dust to settle? 🤔"])
        disclaimer = "Not financial advice."
    else:
        head = f"🆕 Binance анонсировала листинг {name} (${t})."
        q = rng.choice([f"Новые листинги двигаются быстро. Следите за ${t} или пропускаете? 👇",
                        f"${t} в первые часы трогали бы или подождали, пока уляжется? 🤔"])
        disclaimer = post_format.DISCLAIMER
    return f"{head}\n\n{q}\n\n{disclaimer}\n\n#{t} #Binance"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
