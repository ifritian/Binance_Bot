"""
Новости + события Binance (без LLM, всё из источников кодом).

Два формата Square:
1. "Binance Radar" - сводка официальных объявлений Binance (делистинги,
   обновления сетей, события/конкурсы, новые продукты) с карточкой.
2. "News + price check" - свежий заголовок крупного СМИ про монету, рядом
   её движение за 24ч и (если есть) объявление Binance по ней. Причинную
   связь пост НЕ утверждает: только факты рядом, вывод делает читатель.

Источники внешние: любая ошибка сети или формата = пустой результат,
бот не падает.
"""
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

import coin_whitelist

logger = logging.getLogger(__name__)

CMS_URL = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
           "?type=1&catalogId={cid}&pageNo=1&pageSize=10")
CATALOGS = {49: "news", 93: "event", 161: "delist", 157: "network", 48: "listing"}
FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SquareBot/1.0)"}
LABELS = {"delist": "Delisting", "network": "Network", "listing": "Listing", "event": "Event", "news": "Update"}
PRIORITY = {"delist": 0, "network": 1, "listing": 2, "event": 3, "news": 4}
LAST_STATUS = "не запускалось"

COIN_NAMES = {
    "Bitcoin": "BTC", "Ethereum": "ETH", "Ether": "ETH", "Solana": "SOL", "XRP": "XRP", "Ripple": "XRP",
    "BNB": "BNB", "Dogecoin": "DOGE", "Cardano": "ADA", "Avalanche": "AVAX", "Chainlink": "LINK",
    "Polkadot": "DOT", "Litecoin": "LTC", "Tron": "TRX", "Toncoin": "TON", "Sui": "SUI", "Aptos": "APT",
    "Polygon": "POL", "Near": "NEAR", "Arbitrum": "ARB", "Optimism": "OP", "Uniswap": "UNI",
    "Shiba Inu": "SHIB", "Pepe": "PEPE", "Filecoin": "FIL", "Stellar": "XLM", "Cosmos": "ATOM",
}
_NAME_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, COIN_NAMES), key=len, reverse=True)) + r")\b")
_PAREN_RE = re.compile(r"\(([A-Z0-9]{2,10})\)")
_DELIST_RE = re.compile(r"(?:Delist|Remove)\s+([A-Z0-9][A-Z0-9, ]{1,80}?)(?:\s+on\s|\s*\(|$)")
_DATE_TAIL_RE = re.compile(r"\s*(?:[-–]\s*)?\(?\d{4}-\d{2}-\d{2}\)?\s*$")
_LISTING_RE = re.compile(r"will\s+list\s", re.IGNORECASE)


# ---------------- Источники ----------------

def parse_cms(payload: dict, kind: str, now_ts: float, max_age_h: float) -> list[dict]:
    try:
        articles = payload["data"]["catalogs"][0]["articles"]
    except (KeyError, IndexError, TypeError):
        return []
    out = []
    for a in articles:
        try:
            ts = float(a["releaseDate"]) / 1000.0
        except (KeyError, ValueError, TypeError):
            continue
        title = str(a.get("title") or "").strip()
        if not title or now_ts - ts > max_age_h * 3600 or ts > now_ts + 600:
            continue
        if kind == "listing" and _LISTING_RE.search(title):
            continue  # новые листинги уже есть в формате "Анонс Binance"
        out.append({"code": str(a.get("code") or title), "title": title, "ts": ts, "kind": kind,
                    "tickers": extract_tickers(title)})
    return out


def fetch_binance(now_ts: float, max_age_h: float = 96.0) -> list[dict]:
    global LAST_STATUS
    items, ok = [], 0
    for cid, kind in CATALOGS.items():
        try:
            r = requests.get(CMS_URL.format(cid=cid), headers=HEADERS, timeout=15)
            r.raise_for_status()
            items += parse_cms(r.json(), kind, now_ts, max_age_h)
            ok += 1
        except (requests.RequestException, ValueError) as e:
            logger.warning("Binance radar: раздел %s недоступен: %s", cid, e)
    LAST_STATUS = f"ок: разделов {ok}/{len(CATALOGS)}, свежих объявлений {len(items)}"
    return items


def parse_rss(xml_text: str, source: str, now_ts: float, max_age_h: float) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            continue
        if not title or now_ts - ts > max_age_h * 3600 or ts > now_ts + 600:
            continue
        out.append({"id": link or title, "title": title, "ts": ts, "source": source})
    return out


def fetch_news(now_ts: float, max_age_h: float = 10.0) -> list[dict]:
    items = []
    for source, url in FEEDS.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            items += parse_rss(r.text, source, now_ts, max_age_h)
        except requests.RequestException as e:
            logger.warning("Новости: %s недоступен: %s", source, e)
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def fetch_changes(tickers: list[str]) -> dict[str, float]:
    """{тикер: изменение за 24ч, %}. Монет без пары USDT просто нет в ответе."""
    import scanner
    out = {}
    for t in dict.fromkeys(tickers):
        try:
            r = requests.get(f"{scanner._BASE_URL}/ticker/24hr", params={"symbol": f"{t}USDT"}, timeout=10)
            if r.status_code == 200:
                out[t] = float(r.json()["priceChangePercent"])
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
    return out


# ---------------- Разбор ----------------

def extract_tickers(title: str) -> list[str]:
    found = _PAREN_RE.findall(title)
    m = _DELIST_RE.search(title)
    if m:
        found += [t.strip() for t in m.group(1).split(",") if re.fullmatch(r"[A-Z0-9]{2,10}", t.strip())]
    return list(dict.fromkeys(found))


def coin_in_headline(title: str) -> str | None:
    """Тикер первой упомянутой в заголовке известной монеты."""
    m = _NAME_RE.search(title)
    return COIN_NAMES[m.group(1)] if m else None


def coin_is_subject(title: str, ticker: str) -> bool:
    """Монета - герой заголовка (в первых ~45 знаках), а не упомянута вскользь
    ("...SkyAI launches on Solana")."""
    for m in _NAME_RE.finditer(title):
        if COIN_NAMES[m.group(1)] == ticker and m.start() <= 45:
            return not re.search(r"\b(?:on|via|for|with|to|from|in)\s+$", title[:m.start()].lower() + " ")
    return False


def clean_title(title: str, limit: int = 120) -> str:
    t = _DATE_TAIL_RE.sub("", title).strip()
    t = re.sub(r"\s+(?:on|at|from)$", "", t)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def pick_radar_items(items: list[dict], seen: set[str], limit: int = 5) -> list[dict]:
    """Непоказанные объявления: сначала важное (делистинг, сети), затем свежее."""
    fresh = [i for i in items if i["code"] not in seen]
    fresh.sort(key=lambda i: (PRIORITY[i["kind"]], -i["ts"]))
    return fresh[:limit]


def radar_worth_posting(picked: list[dict]) -> bool:
    return len(picked) >= 2 or any(i["kind"] == "delist" for i in picked)


def pick_news_move(news: list[dict], changes: dict[str, float], seen: set[str],
                   min_pct: float) -> tuple[dict, str, float] | None:
    best = None
    for n in news:
        if n["id"] in seen:
            continue
        t = coin_in_headline(n["title"])
        if not t or t not in changes or abs(changes[t]) < min_pct or not coin_is_subject(n["title"], t):
            continue
        if best is None or abs(changes[t]) > abs(best[2]):
            best = (n, t, changes[t])
    return best


_MARKET_RE = re.compile(r"\b(hack(?:ed|s)?|exploit(?:ed)?|stolen|ETF|SEC|CFTC|lawsuit|sues?|ban(?:s|ned)?|approv(?:es|ed|al)|bankrupt\w*|"
                        r"liquidat\w+|Fed|rate (?:cut|hike)|tariffs?|stablecoin|regulat\w+|halt(?:s|ed)?|outage|reserve)\b", re.IGNORECASE)


def pick_market_news(news: list[dict], seen: set[str]) -> dict | None:
    """Свежий заголовок про рынок в целом (хак, ETF, регулятор, ФРС) без конкретной монеты-героя."""
    for n in news:   # news уже отсортированы от свежих к старым
        if n["id"] not in seen and _MARKET_RE.search(n["title"]):
            return n
    return None


def build_market_news_text(news: dict, btc_pct: float, rng=None) -> str:
    import random as _r
    rng = rng or _r
    lead = rng.choice(["🚨 ", "📰 ", "🗞 "]) if abs(btc_pct) >= 2 else rng.choice(["📰 ", "🗞 ", ""])
    return (f"{lead}{news['title']} ({news['source']})\n\n$BTC is {_signed(btc_pct)} over the last 24h.\n\n"
            f"{rng.choice(['How do you read this one?', 'Does this change anything for you?', 'Noise or something that matters?'])}"
            "\n\nNot financial advice.\n\n#CryptoNews")


_STOP = {"with", "from", "that", "this", "their", "after", "into", "over", "will", "have", "says", "said", "amid", "more", "than",
         "about", "crypto", "bitcoin", "market", "price", "news", "what", "could", "year", "week", "today", "gains", "falls"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", re.sub(r"[’']s\b", "", text.lower()))
    return {w for w in words if len(w) >= 4 and w not in _STOP}


def is_repeat(title: str, recent_texts: list[str]) -> bool:
    """Та же история уже была в наших постах (другой источник, другие слова): >=3 общих значимых слов или половина слов заголовка."""
    a = _tokens(title)
    if not a:
        return False
    for r in recent_texts:
        b = _tokens(r)
        common = a & b
        if len(common) >= 3 or (len(a) >= 4 and len(common) / len(a) >= 0.5):
            return True
    return False


# ---------------- Тексты ----------------

def _signed(p: float) -> str:
    return f"{p:+.1f}%".replace("-", "−")


def build_radar_text(items: list[dict], changes: dict[str, float] | None = None) -> str:
    changes = changes or {}
    cash, lines = 0, []
    for i in items:
        line = f"• {LABELS[i['kind']]}: {clean_title(i['title'])}"
        t = next((x for x in i["tickers"] if x in changes), None)
        if t:
            mark = f"${t}" if cash < 3 and coin_whitelist.is_allowed(t) else t
            cash += mark.startswith("$")
            line += f" ({mark} {_signed(changes[t])} in 24h)"
        lines.append(line)
    body = ("Binance Radar: what changed on the exchange\n\n" + "\n".join(lines) +
            "\n\nOfficial Binance announcements, shown as published. Not financial advice."
            "\nWhich of these affects you most?")
    return body + ("\n\n#Binance #Crypto" if "$" in body else "\n\n$BNB #Binance #Crypto")


def build_news_text(news: dict, ticker: str, pct: float, binance_item: dict | None = None, rng=None) -> str:
    import random as _r
    rng = rng or _r
    title, src, mv = news["title"], news["source"], _signed(pct)
    # 🚨 только при заметном движении цены: "тревога" у спокойной новости выглядит шаблонно и фальшиво
    lead = rng.choice(["🚨 ", "🚨 ", "📰 "]) if abs(pct) >= 5 else rng.choice(["📰 ", "📰 ", "🗞 ", "", "", ""])
    layout = rng.choice(["a", "b", "c", "d"])
    if layout == "a":
        body = f"{lead}{title} ({src})\n\n${ticker} is {mv} over the last 24h."
    elif layout == "b":
        body = f"{lead}{title}\n\nVia {src}. ${ticker} {mv} in 24h."
    elif layout == "c":
        body = f"{lead}${ticker}: {title}\n\nPrice so far: {mv} (24h). Source: {src}."
    else:
        body = f"{lead}{title} - {src}\n\nMeanwhile ${ticker} trades {mv} on the day."
    body += rng.choice([" Tap the tag for the live price.", " Live price one tap away.", "", ""])
    if binance_item:
        body += f"\nOn Binance: {clean_title(binance_item['title'])}"
    caveat = rng.choice(["The headline and the move may or may not be related.",
                         "No claim that one caused the other.",
                         "Correlation here is not proof of cause."])
    ask = rng.choice(["Priced in, or more to come?", "Bullish, bearish or noise?",
                      "Does this change anything for you?", "Is this the start of something?", ""])
    tags = rng.choice([f"#{ticker} #Crypto", f"#{ticker}", f"#{ticker} #CryptoNews", f"#{ticker} #Crypto #Binance"])
    body += "\n\n" + caveat + (("\n" + ask) if ask else "") + "\n\n" + tags
    return body
