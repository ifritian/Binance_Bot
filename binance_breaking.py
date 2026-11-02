"""Формат "Binance Breaking": срочный пост в Square про действительно важное официальное объявление Binance.

Источник - официальный раздел объявлений Binance (news_radar.fetch_binance), тот же, что читает Telegram-оповещатель.
Публикуем только значимое: делистинг монеты из белого списка (ликвидной) или экстренные новости (остановка
ввода/вывода/торгов, инцидент безопасности и т.п.). Рядовые делистинги мелочи остаются только в Telegram.
Заголовок - официальный, цифры - с биржи, ничего не выдумываем и не предсказываем.
"""
import random
import re

import news_radar
import queue_manager

KEY = "binance_breaking_state"
FRESH_HOURS = 4.0
SEED_HOURS = 96.0
_EMERGENCY_RE = re.compile(r"emergency|security\s+incident|security\s+breach|exploit|hack|abnormal|halt|"
                           r"suspend(?:s|ed|ing)?\s+(?:all\s+)?(?:trading|deposits?|withdrawals?)|"
                           r"resum(?:e|ption)\s+of\s+(?:deposits?|withdrawals?|trading)|outage|compensation|"
                           r"proof\s+of\s+reserves", re.IGNORECASE)


def significance(item: dict, liquid: set) -> str | None:
    """'delist' (монета из ликвидного списка) | 'emergency' | None."""
    if item["kind"] == "delist" and any(t in liquid for t in item["tickers"]):
        return "delist"
    if item["kind"] in ("news", "network", "event") and _EMERGENCY_RE.search(item["title"]):
        return "emergency"
    return None


def build_text(item: dict, sig: str, changes: dict | None = None, rng: random.Random | None = None) -> str:
    rng = rng or random
    changes = changes or {}
    title = news_radar.clean_title(item["title"], 200)
    if sig == "delist":
        coins = ", ".join(f"${t}" for t in item["tickers"][:3])
        head = rng.choice([f"STOP. Binance just announced a delisting: {coins}.", f"BREAKING. Binance will delist {coins}.",
                           f"Wait. {coins} is being delisted from Binance."])
    else:
        head = rng.choice(["STOP. New official notice from Binance.", "BREAKING. Binance just posted this:", "Wait. Read this Binance announcement."])
    lines = [head, "", title]
    moves = [f"${t} {news_radar._signed(changes[t])}" for t in item["tickers"] if t in changes][:3]
    if moves:
        lines += ["", "24h price: " + ", ".join(moves)]
    lines += ["", ("If you hold it, check the official announcement for the exact date and your options."
                    if sig == "delist" else "Source: the official Binance announcement page. Details can change, so check it yourself."),
              "", "Not financial advice.", "", "#Binance"]
    return "\n".join(lines)


def run_once(now_ts: float, liquid: set, fetch=news_radar.fetch_binance, fetch_changes=news_radar.fetch_changes) -> list:
    """-> [(code, text)] самых свежих значимых объявлений (не больше 1 за раз). Состояние обновляет mark_sent."""
    st = queue_manager._get(KEY) or {}
    seen = set(st.get("seen", []))
    if not st.get("seeded"):
        items = fetch(now_ts, SEED_HOURS)
        queue_manager._set(KEY, {"seeded": True, "seen": sorted({i["code"] for i in items})[-300:], "log": []})
        return []
    out = []
    for i in sorted(fetch(now_ts, FRESH_HOURS), key=lambda i: i["ts"]):
        if i["code"] in seen:
            continue
        sig = significance(i, liquid)
        if sig:
            changes = fetch_changes(i["tickers"][:3]) if i["tickers"] else {}
            out.append((i["code"], build_text(i, sig, changes)))
            break
    return out


def mark_seen_all(now_ts: float, fetch=news_radar.fetch_binance) -> None:
    """Помечает прочитанными все свежие объявления (чтобы незначимые не разбирались снова и снова)."""
    st = queue_manager._get(KEY) or {"seeded": True}
    seen = set(st.get("seen", [])) | {i["code"] for i in fetch(now_ts, FRESH_HOURS)}
    st["seen"] = sorted(seen)[-300:]
    queue_manager._set(KEY, st)


def mark_sent(code: str, now_ts: float) -> None:
    st = queue_manager._get(KEY) or {"seeded": True}
    st["seen"] = sorted(set(st.get("seen", [])) | {code})[-300:]
    st["log"] = [t for t in st.get("log", []) if now_ts - t < 86400] + [now_ts]
    queue_manager._set(KEY, st)


def posts_last_day(now_ts: float) -> int:
    return len([t for t in (queue_manager._get(KEY) or {}).get("log", []) if now_ts - t < 86400])
