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
_EN_DISCLAIMER = "Informational post, not financial advice."
_EN_QUESTIONS_GENERIC = [
    "Which coin surprised you the most today? Tell us in the comments 👇",
    "What would you put on your watchlist for today? 🤔",
    "Do moves like these usually fade or keep going? What do you think?",
    "Are any of these in your portfolio? Share how it's going 👇",
]
_EN_QUESTION_GAINER = "Will $%s keep climbing or pull back from here? 🤔"
_EN_QUESTION_LOSER = "$%s has dropped hard - a dip worth watching or nothing to catch? 👇"
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
    en = config.SQUARE_TEMPLATE_LANG == "en"
    if en:
        head = "☀️ Morning market snapshot" if slot_hour_utc < 12 else "🌙 Evening market snapshot"
        lines = [f"{head} (24h):", ""]
    else:
        head = "☀️ Утренний срез рынка" if slot_hour_utc < 12 else "🌙 Вечерний срез рынка"
        lines = [f"{head} за 24 часа:", ""]
    for t, price, pct in data["majors"]:
        lines.append(f"${t} {_fmt_price(price)} ({_fmt_pct(pct)})")
    if data["gainers"]:
        lines += ["", "🚀 Top gainers:" if en else "🚀 Лидеры роста:"]
        lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["gainers"]]
    if data["losers"]:
        lines += ["", "🔻 Top losers:" if en else "🔻 Лидеры падения:"]
        lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["losers"]]

    options = list(_EN_QUESTIONS_GENERIC if en else _QUESTIONS_GENERIC)
    if data["gainers"]:
        options.append((_EN_QUESTION_GAINER if en else _QUESTION_GAINER) % data["gainers"][0][0])
    if data["losers"]:
        options.append((_EN_QUESTION_LOSER if en else _QUESTION_LOSER) % data["losers"][0][0])
    lines += ["", rng.choice(options), "", _EN_DISCLAIMER if en else post_format.DISCLAIMER]

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


# --- "Горячая монета": событийный пост, а не по таймеру ---

HOT_MOVE_PCT = 10.0
HOT_MIN_VOLUME_USD = 100_000_000
_EN_HOT_TEMPLATES = [
    "🔥 ${t} is moving sharply: {pct} in 24 hours.\n\nPrice: {price} USDT. The start of a move or already late? Tell us what you think 👇",
    "👀 ${t} is on the move: {pct} in 24 hours.\n\nPrice: {price} USDT. Already in a position, or just watching? 🤔",
]
_HOT_TEMPLATES = [
    "🔥 ${t} {pct} за сутки — сейчас одна из самых заметных монет рынка.\n\nЦена: {price} USDT. Это начало движения или уже поздно? Пишите, что думаете 👇",
    "👀 ${t} резко двигается: {pct} за 24 часа.\n\nЦена: {price} USDT. Кто-то уже в позиции или только наблюдаете? 🤔",
]


def pick_hot_mover(rows: list[dict] | None = None, recent: dict | None = None, now_ts: float = 0.0,
                   cooldown_s: float = 86400.0):
    """Самая сильная монета (|изменение| >= HOT_MOVE_PCT, объём >= HOT_MIN_VOLUME_USD)
    из белого списка, по которой не было поста за cooldown. -> (ticker, price, pct) | None"""
    if rows is None:
        try:
            resp = requests.get(f"{scanner._BASE_URL}/ticker/24hr", timeout=20)
            resp.raise_for_status()
            rows = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Горячая монета: нет данных Binance: %s", e)
            return None
    recent = recent or {}
    allowed = coin_whitelist.allowed_tickers()
    best = None
    for row in rows:
        symbol = row.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        t = symbol.removesuffix("USDT")
        if t not in allowed or now_ts - recent.get(t, 0) < cooldown_s:
            continue
        try:
            price = float(row["lastPrice"]); pct = float(row["priceChangePercent"]); vol = float(row["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if price <= 0 or vol < HOT_MIN_VOLUME_USD or abs(pct) < HOT_MOVE_PCT:
            continue
        if best is None or abs(pct) > abs(best[2]):
            best = (t, price, pct)
    return best


def build_hot_text(ticker: str, price: float, pct: float, rng: random.Random | None = None) -> str:
    rng = rng or random
    en = config.SQUARE_TEMPLATE_LANG == "en"
    tpl = rng.choice(_EN_HOT_TEMPLATES if en else _HOT_TEMPLATES)
    if pct < 0:
        tpl = tpl.replace("🔥", "🔻").replace("Это начало движения или уже поздно?", "Отскок близко или падение продолжится?")
        tpl = tpl.replace("The start of a move or already late?", "A bounce coming or more downside?")
    body = tpl.replace("${t}", f"${ticker}").format(pct=_fmt_pct(pct), price=_fmt_price(price))
    return f"{body}\n\n{_EN_DISCLAIMER if en else post_format.DISCLAIMER}\n\n#{ticker} #Crypto"
