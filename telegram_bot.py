"""Telegram-бот с командами (личные сообщения): уровни, ставки финансирования, настроение рынка, ценовые оповещения.

Работает без сервера: getUpdates опрашивается в тиках и в "быстрой полосе" (ответ обычно за ~минуту). Тот же бот, что публикует в канал.
Только личные чаты, ответы обычным текстом (без разметки), лимит команд на человека. Не даёт прогнозов, это справочный инструмент.
"""
import logging
import re
import time

import requests

import config
import coin_whitelist
import level_check
import queue_manager

logger = logging.getLogger(__name__)
KEY_OFFSET, KEY_ALERTS, KEY_RATE = "tgbot_offset", "tgbot_alerts", "tgbot_rate"
MAX_ALERTS_PER_USER, MAX_ALERTS_TOTAL, MAX_CMDS_PER_HOUR = 5, 300, 30
_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
_TICKER = re.compile(r"^\$?([A-Za-z0-9]{2,10})$")

HELP = ("Commands:\n/levels BTC: price and the last 24h high and low\n/funding: where futures traders are crowded\n"
        "/mood: Fear & Greed index\n/alert BTC 90000: message me when the price crosses a level\n/alerts: your alerts\n/stop: remove all your alerts")


def parse(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return "", []
    return parts[0][1:].split("@")[0].lower(), parts[1:]


def ticker_arg(args: list[str], default: str = "BTC") -> str | None:
    if not args:
        return default
    m = _TICKER.match(args[0])
    return m.group(1).upper() if m else None


def _px(x: float) -> str:
    import mover_setups
    return mover_setups._px(x)


def rate_ok(uid: int, now: float, store: dict) -> bool:
    hits = [t for t in store.get(str(uid), []) if now - t < 3600]
    if len(hits) >= MAX_CMDS_PER_HOUR:
        store[str(uid)] = hits
        return False
    store[str(uid)] = hits + [now]
    return True


def add_alert(alerts: list, uid: int, ticker: str, level: float, price_now: float) -> str:
    mine = [a for a in alerts if a["uid"] == uid]
    if len(mine) >= MAX_ALERTS_PER_USER:
        return f"You already have {MAX_ALERTS_PER_USER} alerts. Remove them with /stop first."
    if len(alerts) >= MAX_ALERTS_TOTAL:
        return "Alerts are full right now, try again later."
    alerts.append({"uid": uid, "t": ticker, "level": level, "dir": "up" if level > price_now else "down", "ts": time.time()})
    return f"Done. I will message you when {ticker} crosses {_px(level)} (now {_px(price_now)})."


def due_alerts(alerts: list, prices: dict) -> tuple[list, list]:
    """-> (сработавшие, оставшиеся)."""
    hit, keep = [], []
    for a in alerts:
        p = prices.get(a["t"])
        fired = p is not None and ((a["dir"] == "up" and p >= a["level"]) or (a["dir"] == "down" and p <= a["level"]))
        (hit if fired else keep).append((a, p) if fired else a)
    return hit, keep


def answer(cmd: str, args: list[str], uid: int, alerts: list, fetch: dict) -> str | None:
    """fetch: {'klines': f(t), 'price': f(t), 'funding': f(), 'fng': f()}: подставляются в тестах."""
    if cmd in ("start", "help"):
        ch = str(config.TELEGRAM_PUBLISH_CHANNEL or "")
        tail = f"\n\nDaily market posts: https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else ""
        return f"Hi! I am the {config.PROJECT_NAME} bot. I show data, I do not give advice.\n\n{HELP}{tail}"
    if cmd == "levels":
        t = ticker_arg(args)
        if not t or not coin_whitelist.is_allowed(t):
            return "Give me a major coin, for example /levels BTC"
        lv = level_check.compute(fetch["klines"](t))
        if lv is None:
            return "No data for that coin right now."
        return f"{t}: {_px(lv['price'])}\n24h high {_px(lv['hi'])}\n24h low {_px(lv['lo'])}\nThese are edges of the last day, not a forecast."
    if cmd == "funding":
        sel = fetch["funding"]()
        if not sel:
            return "No extreme funding right now, or the data is unavailable."
        rows = [f"{t} {f * 100:+.3f}%".replace("-", "−") for t, f, _ in sel["longs"] + sel["shorts"]]
        return "Crowded positions (funding per period):\n" + "\n".join(rows) + "\nPositive = longs pay shorts. Context, not a signal."
    if cmd == "mood":
        f = fetch["fng"]()
        return f"Fear & Greed: {f['value']} ({f['label']})" if f else "No data right now."
    if cmd == "alert":
        t = ticker_arg(args[:1])
        try:
            level = float(args[1].replace(",", ".")) if len(args) > 1 else None
        except ValueError:
            level = None
        if not t or level is None or level <= 0 or not coin_whitelist.is_allowed(t):
            return "Usage: /alert BTC 90000"
        p = fetch["price"](t)
        if p is None:
            return "No price for that coin right now."
        return add_alert(alerts, uid, t, level, p)
    if cmd == "alerts":
        mine = [a for a in alerts if a["uid"] == uid]
        return "\n".join(f"{a['t']} {a['dir']} {_px(a['level'])}" for a in mine) or "You have no alerts."
    if cmd == "stop":
        n = len([a for a in alerts if a["uid"] == uid])
        alerts[:] = [a for a in alerts if a["uid"] != uid]
        return f"Removed {n} alert(s)."
    return None


def send(chat_id: int, text: str) -> None:
    requests.post(f"{_API}/sendMessage", data={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}, timeout=20)


def poll(fetch: dict, now: float | None = None, get_updates=None, send_fn=send) -> int:
    """Обрабатывает новые сообщения. -> число ответов. Любая сетевая ошибка = 0."""
    if not config.TELEGRAM_BOT_TOKEN:
        return 0
    now = now or time.time()
    off = queue_manager._get(KEY_OFFSET) or 0
    try:
        if get_updates:
            ups = get_updates(off)
        else:
            r = requests.get(f"{_API}/getUpdates", params={"offset": off, "timeout": 0, "allowed_updates": '["message"]'}, timeout=20)
            ups = r.json().get("result", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return 0
    if not ups:
        return 0
    alerts = queue_manager._get(KEY_ALERTS) or []
    rates = queue_manager._get(KEY_RATE) or {}
    n = 0
    for u in ups:
        off = max(off, u["update_id"] + 1)
        m = u.get("message") or {}
        chat = m.get("chat") or {}
        if chat.get("type") != "private" or not m.get("text"):
            continue
        cmd, args = parse(m["text"])
        if not cmd or not rate_ok(chat["id"], now, rates):
            continue
        try:
            reply = answer(cmd, args, chat["id"], alerts, fetch)
            if reply:
                send_fn(chat["id"], reply); n += 1
        except Exception:
            logger.exception("Команда /%s не обработана", cmd)
    queue_manager._set(KEY_OFFSET, off)
    queue_manager._set(KEY_ALERTS, alerts)
    queue_manager._set(KEY_RATE, {k: v for k, v in rates.items() if v and now - v[-1] < 3600})
    return n


def check_alerts(price_of, send_fn=send) -> int:
    alerts = queue_manager._get(KEY_ALERTS) or []
    if not alerts:
        return 0
    prices = {t: price_of(t) for t in {a["t"] for a in alerts}}
    hit, keep = due_alerts(alerts, prices)
    for a, p in hit:
        try:
            send_fn(a["uid"], f"{a['t']} crossed {_px(a['level'])}: now {_px(p)}.")
        except Exception:
            logger.exception("Оповещение не отправлено")
    if hit:
        queue_manager._set(KEY_ALERTS, keep)
    return len(hit)


def username() -> str | None:
    """Имя бота (getMe), кэшируется в состоянии."""
    u = queue_manager._get("tgbot_username")
    if u or not config.TELEGRAM_BOT_TOKEN:
        return u
    try:
        r = requests.get(f"{_API}/getMe", timeout=15).json()
        u = (r.get("result") or {}).get("username")
    except (requests.RequestException, ValueError):
        return None
    if u:
        queue_manager._set("tgbot_username", u)
    return u
