"""
Формат "Срез рынка" - ТОЛЬКО Binance Square, без LLM.

Зачем: сигнал по одной монете интересен узкому кругу, а "что сегодня
происходит на рынке" интересно почти всем. Пост содержит сразу несколько
$CASHTAG известных монет (лидеры роста/падения за сутки среди белого
списка) - каждая привязывает пост к своей ленте и даёт читателю
повод нажать на монету (именно такие клики приносят комиссию), а в конце -
живой вопрос, на который легко ответить комментарием.

Все числа берутся из /ticker/24hr и форматируются кодом, текст собирается
по шаблону - нечему "галлюцинировать" и нечего валидировать.
"""
import logging
import random
from datetime import datetime, timezone

import requests

import coin_whitelist
import config
import post_format
import scanner

logger = logging.getLogger(__name__)

MIN_QUOTE_VOLUME_USD = 20_000_000  # отсекаем "мёртвые" пары - движение на них ничего не значит
TOP_N = 3
_MAJORS = ("BTC", "ETH")

_QUESTIONS_GENERIC = [
    "Какая монета сегодня удивила вас больше всех? Пишите в комментариях 👇",
    "Что из этого вы бы взяли в наблюдение на сегодня? 🤔",
    "Рост на таких движениях обычно сдувается или продолжается? Как думаете?",
    "Кто-то из списка уже у вас в портфеле? Расскажите, как идёт 👇",
]
_QUESTION_GAINER = "Как думаете, $%s продолжит рост или дальше откат? 🤔"
_QUESTION_LOSER = "$%s уже сильно просел - это возможность или ловить нечего? 👇"


def fetch_movers(rows: list[dict] | None = None) -> dict | None:
    """Возвращает {"majors": [(ticker, price, pct)], "gainers": [...], "losers": [...]}
    или None, если данных нет. rows - для тестов (сырой ответ /ticker/24hr)."""
    if rows is None:
        try:
            resp = requests.get(f"{scanner._BASE_URL}/ticker/24hr", timeout=20)
            resp.raise_for_status()
            rows = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Срез рынка: не удалось получить данные Binance: %s", e)
            return None

    allowed = coin_whitelist.allowed_tickers()
    coins: dict[str, tuple[float, float]] = {}
    for row in rows:
        symbol = row.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        ticker = symbol.removesuffix("USDT")
        if ticker not in allowed:
            continue
        try:
            price = float(row["lastPrice"])
            pct = float(row["priceChangePercent"])
            volume = float(row["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if price <= 0 or volume < MIN_QUOTE_VOLUME_USD:
            continue
        coins[ticker] = (price, pct)

    majors = [(t, *coins[t]) for t in _MAJORS if t in coins]
    others = [(t, p, c) for t, (p, c) in coins.items() if t not in _MAJORS]
    if len(majors) < 1 or len(others) < 2 * TOP_N:
        return None
    others.sort(key=lambda x: x[2], reverse=True)
    gainers = [x for x in others[:TOP_N] if x[2] > 0]
    losers = [x for x in reversed(others[-TOP_N:]) if x[2] < 0]
    if not gainers and not losers:
        return None
    return {"majors": majors, "gainers": gainers, "losers": losers}


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}".replace(",", " ")
    if price >= 1:
        return f"{price:.2f}"
    if price >= 0.01:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _fmt_pct(pct: float) -> str:
    return f"{pct:+.1f}%".replace("-", "−")


def build_digest_text(data: dict, slot_hour_utc: int, rng: random.Random | None = None) -> str:
    rng = rng or random
    head = "☀️ Утренний срез рынка" if slot_hour_utc < 12 else "🌙 Вечерний срез рынка"
    lines = [f"{head} за 24 часа:", ""]
    for t, price, pct in data["majors"]:
        lines.append(f"${t} {_fmt_price(price)} ({_fmt_pct(pct)})")
    if data["gainers"]:
        lines += ["", "🚀 Лидеры роста:"]
        lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["gainers"]]
    if data["losers"]:
        lines += ["", "🔻 Лидеры падения:"]
        lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["losers"]]

    options = list(_QUESTIONS_GENERIC)
    if data["gainers"]:
        options.append(_QUESTION_GAINER % data["gainers"][0][0])
    if data["losers"]:
        options.append(_QUESTION_LOSER % data["losers"][0][0])
    lines += ["", rng.choice(options), "", post_format.DISCLAIMER]

    topic = rng.choice(["#Crypto", "#Market", "#Altcoins"])
    first = data["gainers"][0][0] if data["gainers"] else data["majors"][0][0]
    lines += ["", f"#{first} {topic}"]
    return "\n".join(lines)


def due_slot(now: datetime, hours: list[int], done: str | None) -> int | None:
    """Самый поздний уже наступивший сегодня слот, который ещё не
    опубликован. done - ключ "YYYY-MM-DD:HH" последнего опубликованного."""
    due = [h for h in sorted(hours) if now.hour >= h]
    if not due:
        return None
    slot = due[-1]
    if done == f"{now.strftime('%Y-%m-%d')}:{slot:02d}":
        return None
    # слот "протух" - не публикуем утренний срез в 22:00
    if now.hour - slot > config.MARKET_DIGEST_MAX_LATE_HOURS:
        return None
    return slot


def slot_key(now: datetime, slot: int) -> str:
    return f"{now.strftime('%Y-%m-%d')}:{slot:02d}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
