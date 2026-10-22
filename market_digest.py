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

import voice_variety
from datetime import datetime, timezone

import requests

import micro_moments

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
_EN_DISCLAIMER = "Not financial advice."
_EN_QUESTIONS_GENERIC = [
    "Which coin surprised you the most today? Tell us in the comments 👇",
    "What would you put on your watchlist for today? 🤔",
    "Do moves like these usually fade or keep going? What do you think?",
    "Are any of these in your portfolio? Share how it's going 👇",
    "Anything here catch your eye?",
    "Quiet tape or just the calm before something? 🤔",
    "Who's buying this, and who's fading it?",
    "What's on your radar today?",
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


def build_digest_text(data: dict, slot_hour_utc: int, rng: random.Random | None = None,
                      compact: bool = False) -> str:
    """compact=True - запасной короткий вариант (BTC + по одному лидеру
    роста/падения): если Square отклонил полный пост (например, из-за числа
    $-тикеров), публикуем его вместо полного."""
    rng = rng or random
    if compact:
        data = {"majors": data["majors"][:1], "gainers": data["gainers"][:1], "losers": data["losers"][:1]}
    en = config.SQUARE_TEMPLATE_LANG == "en"
    style = rng.choice(["list", "inline", "story"]) if en else "list"
    if en:
        heads = voice_variety.MORNING_HEADS if slot_hour_utc < 12 else voice_variety.EVENING_HEADS
        head = voice_variety.strip_emoji_prob(rng, rng.choice(heads))
        suffix = rng.choice([" (24h):", " (last 24h):", ":", ""])
        lines = [f"{head}{suffix}", ""]
    else:
        head = "☀️ Утренний срез рынка" if slot_hour_utc < 12 else "🌙 Вечерний срез рынка"
        lines = [f"{head} за 24 часа:", ""]
    if en and style == "inline":
        lines = [lines[0].rstrip(), ""]
        majors = ", ".join(f"${t} {_fmt_price(price)} ({_fmt_pct(pct)})" for t, price, pct in data["majors"])
        lines.append(majors)
        if data["gainers"]:
            lines.append(rng.choice(["Leading: ", "Up most: ", "Best: "]) +
                         ", ".join(f"${t} {_fmt_pct(c)}" for t, _p, c in data["gainers"]))
        if data["losers"]:
            lines.append(rng.choice(["Lagging: ", "Down most: ", "Worst: "]) +
                         ", ".join(f"${t} {_fmt_pct(c)}" for t, _p, c in data["losers"]))
    elif en and style == "story":
        lines = [lines[0].rstrip(), ""]
        (t0, p0, c0) = data["majors"][0]
        sent = f"${t0} sits at {_fmt_price(p0)} ({_fmt_pct(c0)})"
        if len(data["majors"]) > 1:
            t1, p1, c1 = data["majors"][1]
            sent += f", ${t1} at {_fmt_price(p1)} ({_fmt_pct(c1)})"
        lines.append(sent + ".")
        if data["gainers"]:
            t, _p, c = data["gainers"][0]
            lines.append(rng.choice([f"Strongest move up: ${t} {_fmt_pct(c)}.", f"${t} is the day's leader at {_fmt_pct(c)}."]))
        if data["losers"]:
            t, _p, c = data["losers"][0]
            lines.append(rng.choice([f"Weakest: ${t} {_fmt_pct(c)}.", f"${t} took the biggest hit, {_fmt_pct(c)}."]))
    else:
        for t, price, pct in data["majors"]:
            lines.append(f"${t} {_fmt_price(price)} ({_fmt_pct(pct)})")
        pair = rng.choice([("🚀 Top gainers:", "🔻 Top losers:"), ("Gainers:", "Losers:"),
                           ("🚀 Leaders:", "🔻 Laggards:"), ("Leading:", "Lagging:")]) if en else ("🚀 Лидеры роста:", "🔻 Лидеры падения:")
        if data["gainers"]:
            lines += ["", pair[0]]
            lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["gainers"]]
        if data["losers"]:
            lines += ["", pair[1]]
            lines += [f"${t} {_fmt_pct(c)}" for t, _p, c in data["losers"]]

    options = list(_EN_QUESTIONS_GENERIC if en else _QUESTIONS_GENERIC)
    if data["gainers"]:
        options.append((_EN_QUESTION_GAINER if en else _QUESTION_GAINER) % data["gainers"][0][0])
    if data["losers"]:
        options.append((_EN_QUESTION_LOSER if en else _QUESTION_LOSER) % data["losers"][0][0])
    if not en or rng.random() < 0.8:
        lines += ["", rng.choice(options)]
    if en and not compact and rng.random() < 0.4:
        ctx = micro_moments.context_line(now_utc(), rng)
        if ctx:
            lines += ["", ctx]
    if not compact and rng.random() < config.SQUARE_FOLLOW_LINE_PROBABILITY:
        hours = " and ".join(f"{h:02d}:00" for h in config.MARKET_DIGEST_HOURS_UTC[:2])
        lines += ["", (f"Follow to get the {hours} UTC snapshots daily." if en
                       else f"Подпишитесь, чтобы получать срезы рынка в {hours} UTC каждый день.")]
    lines += ["", voice_variety.disclaimer(rng) if en else post_format.DISCLAIMER]

    topic = rng.choice(["#Crypto", "#Market", "#Altcoins", "#CryptoMarket"])
    first = data["gainers"][0][0] if data["gainers"] else data["majors"][0][0]
    tags = rng.choice([f"#{first} {topic}", f"{topic} #{first}", f"{topic}", f"#{first}"])
    lines += ["", tags]
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
    "🔥 ${t} {pct} за сутки - сейчас одна из самых заметных монет рынка.\n\nЦена: {price} USDT. Это начало движения или уже поздно? Пишите, что думаете 👇",
    "👀 ${t} резко двигается: {pct} за 24 часа.\n\nЦена: {price} USDT. Кто-то уже в позиции или только наблюдаете? 🤔",
]


def _win(minutes: int | None) -> str:
    m = int(minutes or 60)
    return "hour" if 50 <= m <= 70 else f"{m} min"


SNAP_KEEP_S = 5400          # храним снимки цен ~90 минут
FAST_MIN_AGE_S = 25 * 60    # быстрое изменение считаем не по слишком свежему снимку


def fetch_ticker_rows() -> list[dict] | None:
    try:
        resp = requests.get(f"{scanner._BASE_URL}/ticker/24hr", timeout=20)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Горячая монета: нет данных Binance: %s", e)
        return None


def update_snapshots(rows: list[dict], now_ts: float, snaps: list | None) -> list:
    """Добавляет снимок цен (белый список, объём >= HOT_MIN_VOLUME_USD), чистит старые.
    Нужен для "быстрого" движения: рост/падение за последние ~час, а не за сутки."""
    allowed = coin_whitelist.allowed_tickers()
    cur = {}
    for row in rows:
        symbol = row.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        t = symbol.removesuffix("USDT")
        try:
            price = float(row["lastPrice"]); vol = float(row["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if t in allowed and price > 0 and vol >= HOT_MIN_VOLUME_USD:
            cur[t] = price
    out = [s for s in (snaps or []) if now_ts - s[0] < SNAP_KEEP_S and s[0] <= now_ts]
    out.append([now_ts, cur])
    return out


def fast_changes(rows: list[dict], snaps: list | None, now_ts: float) -> dict:
    """{ticker: (pct, минут)} - изменение цены относительно снимка возрастом ~60 минут
    (самого старого не старше 80 минут, но не моложе FAST_MIN_AGE_S)."""
    ref = None
    for ts, prices in snaps or []:
        age = now_ts - ts
        if FAST_MIN_AGE_S <= age <= 4800 and (ref is None or abs(age - 3600) < abs(now_ts - ref[0] - 3600)):
            ref = (ts, prices)
    if ref is None:
        return {}
    mins = round((now_ts - ref[0]) / 60)
    out = {}
    for row in rows:
        symbol = row.get("symbol", "")
        t = symbol.removesuffix("USDT") if symbol.endswith("USDT") else None
        old = ref[1].get(t) if t else None
        try:
            price = float(row["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
        if old and price > 0:
            out[t] = ((price / old - 1) * 100, mins)
    return out


def pick_hot_event(rows: list[dict], recent: dict | None = None, now_ts: float = 0.0,
                   cooldown_s: float = 86400.0, min_pct: float | None = None,
                   fast: dict | None = None, fast_pct: float | None = None):
    """Самая сильная монета из белого списка (объём >= HOT_MIN_VOLUME_USD, не было поста за
    cooldown): либо |24ч| >= min_pct, либо быстрое движение |за ~час| >= fast_pct.
    -> {"ticker","price","pct24","fast_pct","fast_min"} | None"""
    recent = recent or {}
    fast = fast or {}
    thr24 = HOT_MOVE_PCT if min_pct is None else min_pct
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
        if price <= 0 or vol < HOT_MIN_VOLUME_USD:
            continue
        fp, fm = fast.get(t, (0.0, 0))
        is_fast = fast_pct is not None and abs(fp) >= fast_pct
        if abs(pct) < thr24 and not is_fast:
            continue
        score = max(abs(pct), 3 * abs(fp))
        if best is None or score > best[0]:
            best = (score, {"ticker": t, "price": price, "pct24": pct,
                            "fast_pct": fp if is_fast else None, "fast_min": fm if is_fast else None})
    return best[1] if best else None


def pick_hot_mover(rows: list[dict] | None = None, recent: dict | None = None, now_ts: float = 0.0,
                   cooldown_s: float = 86400.0, min_pct: float | None = None):
    """Самая сильная монета (|изменение 24ч| >= HOT_MOVE_PCT, объём >= HOT_MIN_VOLUME_USD)
    из белого списка, по которой не было поста за cooldown. -> (ticker, price, pct) | None"""
    if rows is None:
        rows = fetch_ticker_rows()
        if rows is None:
            return None
    ev = pick_hot_event(rows, recent, now_ts, cooldown_s, min_pct)
    return (ev["ticker"], ev["price"], ev["pct24"]) if ev else None


def build_hot_text(ticker: str, price: float, pct: float, rng: random.Random | None = None,
                   fast_pct: float | None = None, fast_min: int | None = None) -> str:
    rng = rng or random
    en = config.SQUARE_TEMPLATE_LANG == "en"
    tpl = rng.choice(_EN_HOT_TEMPLATES if en else _HOT_TEMPLATES)
    if fast_pct is not None:
        pct = fast_pct
        tpl = tpl.replace("in 24 hours", f"in the last {_win(fast_min)}").replace("за сутки", f"за {_win(fast_min)}").replace("за 24 часа", f"за {_win(fast_min)}")
    if pct < 0:
        tpl = tpl.replace("🔥", "🔻").replace("Это начало движения или уже поздно?", "Отскок близко или падение продолжится?")
        tpl = tpl.replace("The start of a move or already late?", "A bounce coming or more downside?")
    body = tpl.replace("${t}", f"${ticker}").format(pct=_fmt_pct(pct), price=_fmt_price(price))
    return f"{body}\n\n{_EN_DISCLAIMER if en else post_format.DISCLAIMER}\n\n#{ticker} #Crypto"


def fetch_price(ticker: str) -> float | None:
    try:
        resp = requests.get(f"{scanner._BASE_URL}/ticker/price", params={"symbol": f"{ticker}USDT"}, timeout=15)
        resp.raise_for_status()
        p = float(resp.json()["price"])
        return p if p > 0 else None
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Цена %s: %s", ticker, e)
        return None
