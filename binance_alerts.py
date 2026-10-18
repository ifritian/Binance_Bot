"""
Мгновенные оповещения Telegram о важных объявлениях Binance: делистинги и
работы с сетями (хард-форки, обслуживание кошельков, обмен токенов).
Без LLM: заголовок официальный, остальное собирает код. Ссылка ведёт на
оригинальное объявление Binance, чтобы любой мог проверить.

При первом запуске все уже опубликованные объявления помечаются прочитанными
(иначе канал получит поток старых новостей).
"""
import re

import news_radar
import queue_manager

STATE_KEY = "binance_alerts_state"
FRESH_HOURS = 6.0
SEED_HOURS = 96.0
MAX_PER_TICK = 3
ANNOUNCEMENT_URL = "https://www.binance.com/en/support/announcement/detail/{code}"
_NETWORK_RE = re.compile(r"hard\s*fork|network\s+upgrade|wallet\s+maintenance|token\s+swap|token\s+merge|"
                         r"contract\s+swap|redenomination|suspend|rebrand", re.IGNORECASE)


def is_alert_worthy(item: dict) -> bool:
    if item["kind"] == "delist":
        return True
    return item["kind"] == "network" and bool(_NETWORK_RE.search(item["title"]))


def pick(items: list, seen: set, limit: int = MAX_PER_TICK) -> list:
    fresh = [i for i in items if is_alert_worthy(i) and i["code"] not in seen]
    fresh.sort(key=lambda i: i["ts"])
    return fresh[:limit]


def build_text(item: dict, changes: dict | None = None) -> str:
    changes = changes or {}
    icon, label, advice = (("⚠️", "Delisting notice", "If you hold this coin, read the official announcement for the exact date and your options.")
                           if item["kind"] == "delist" else
                           ("🔧", "Network notice", "Deposits and withdrawals for the coin can be paused around this date. Check the announcement for the exact window."))
    lines = [f"{icon} Binance {label}", "", news_radar.clean_title(item["title"], 200)]
    moves = [f"{t} {news_radar._signed(changes[t])}" for t in item["tickers"] if t in changes][:3]
    if moves:
        lines += ["", "24h price: " + ", ".join(moves)]
    lines += ["", advice, "", "Official announcement: " + ANNOUNCEMENT_URL.format(code=item["code"])]
    return "\n".join(lines)


def run_once(now_ts: float, fetch=news_radar.fetch_binance, fetch_changes=news_radar.fetch_changes) -> list:
    """Возвращает [(код, текст)] для публикации; состояние обновляет вызывающий через mark_sent."""
    st = queue_manager._get(STATE_KEY) or {}
    seen = set(st.get("seen", []))
    if not st.get("seeded"):
        items = fetch(now_ts, SEED_HOURS)
        queue_manager._set(STATE_KEY, {"seeded": True, "seen": sorted({i["code"] for i in items})[-300:]})
        return []
    items = fetch(now_ts, FRESH_HOURS)
    picked = pick(items, seen)
    if not picked:
        return []
    tickers = [t for i in picked for t in i["tickers"]][:6]
    changes = fetch_changes(tickers) if tickers else {}
    return [(i["code"], build_text(i, changes)) for i in picked]


def mark_sent(code: str) -> None:
    st = queue_manager._get(STATE_KEY) or {"seeded": True}
    seen = set(st.get("seen", []))
    seen.add(code)
    st["seen"] = sorted(seen)[-300:]
    queue_manager._set(STATE_KEY, st)
