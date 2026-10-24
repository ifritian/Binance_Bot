"""Радар перегретых позиций: ставки финансирования бессрочных фьючерсов (кто кому платит).

Публичные данные без ключей: сначала Binance Futures, при недоступности (часть облачных IP блокируется) - OKX.
Пост - только факты и честная сверка прошлого раза; прогнозов нет. Любая ошибка источника = пустой результат.
"""
import logging
import math
import random
from pathlib import Path

import requests

import coin_whitelist

logger = logging.getLogger(__name__)
BINANCE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
OKX_URL = "https://www.okx.com/api/v5/public/funding-rate"
HOT_LONG = 0.0003     # +0.03% за период: заметно выше обычных +0.01%
HOT_SHORT = -0.0002
KEY = "funding_state"
LAST_STATUS = "не запускалось"
MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "SUI", "TON", "DOT", "LTC", "NEAR", "APT",
          "ARB", "OP", "PEPE", "WLD", "INJ", "AAVE", "FIL", "ATOM", "UNI", "TRX", "SEI", "TIA", "ENA", "WIF", "BONK"]


def parse_binance(payload: list, allowed: set) -> dict:
    out = {}
    for r in payload or []:
        s = r.get("symbol", "")
        if s.endswith("USDT") and s[:-4] in allowed:
            try:
                out[s[:-4]] = float(r["lastFundingRate"])
            except (KeyError, ValueError, TypeError):
                continue
    return out


def fetch(allowed: set | None = None, get=requests.get) -> dict:
    """{тикер: ставка за период (доля)}; {} если оба источника недоступны."""
    global LAST_STATUS
    allowed = allowed if allowed is not None else (set(MAJORS) & coin_whitelist.allowed_tickers())
    try:
        r = get(BINANCE_URL, timeout=15)
        r.raise_for_status()
        d = parse_binance(r.json(), allowed)
        if d:
            LAST_STATUS = "Binance: ok"
            return d
    except (requests.RequestException, ValueError) as e:
        LAST_STATUS = f"Binance: {type(e).__name__}"
    out = {}
    for t in sorted(allowed):
        try:
            r = get(OKX_URL, params={"instId": f"{t}-USDT-SWAP"}, timeout=10)
            row = (r.json().get("data") or [None])[0]
            if row:
                out[t] = float(row["fundingRate"])
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            continue
    LAST_STATUS += f"; OKX: {len(out)} монет"
    return out


def pick(rates: dict, pct24: dict) -> dict | None:
    longs = sorted(((t, f) for t, f in rates.items() if f >= HOT_LONG), key=lambda x: -x[1])[:3]
    shorts = sorted(((t, f) for t, f in rates.items() if f <= HOT_SHORT), key=lambda x: x[1])[:3]
    if not longs and not shorts:
        return None
    return {"longs": [(t, f, pct24.get(t)) for t, f in longs], "shorts": [(t, f, pct24.get(t)) for t, f in shorts]}


def _f(x: float) -> str:
    return f"{x * 100:+.3f}%".replace("-", "−")


def _line(t: str, f: float, p, tag: bool) -> str:
    s = f"{'$' if tag else ''}{t} {_f(f)}"
    return s + (f" (24h price {p:+.1f}%)".replace("-", "−") if p is not None else "")


def previous_line(prev: dict | None, price_now: float | None) -> str | None:
    if not prev or not price_now or not prev.get("price"):
        return None
    chg = (price_now / prev["price"] - 1) * 100
    side = "crowded longs" if prev["funding"] > 0 else "crowded shorts"
    return f"Last check: {prev['ticker']} had {_f(prev['funding'])} ({side}). Price since then: {chg:+.1f}%.".replace("-", "−", 1) \
        if chg < 0 else f"Last check: {prev['ticker']} had {_f(prev['funding'])} ({side}). Price since then: {chg:+.1f}%."


def build_text(sel: dict, prev_line: str | None = None, rng: random.Random | None = None) -> str:
    rng = rng or random
    out = [rng.choice(["Funding radar: where traders are crowded", "Who is paying to hold their position", "Funding check on perpetual futures"]), ""]
    first = True
    if sel["longs"]:
        out.append("Longs paying shorts the most:")
        for t, f, p in sel["longs"]:
            out.append(_line(t, f, p, tag=first)); first = False
        out.append("")
    if sel["shorts"]:
        out.append("Shorts paying longs the most:")
        tagged = False
        for t, f, p in sel["shorts"]:
            out.append(_line(t, f, p, tag=not tagged)); tagged = True
        out.append("")
    out.append("Funding is the fee one side pays the other each period. A high positive rate means many longs, a negative one means many shorts. "
               "Crowded positions can unwind fast, but this is context, not a signal.")
    if prev_line:
        out += ["", prev_line]
    out += ["", "Not financial advice.", "", "#Funding"]
    return "\n".join(out)


def card(sel: dict, out: Path) -> Path | None:
    """Карточка: полосы ставок (золото - лонги платят, серый - шорты платят)."""
    try:
        from PIL import Image, ImageDraw
        import video_post as vp
        rows = [(t, f, True) for t, f, _ in sel["longs"]] + [(t, f, False) for t, f, _ in sel["shorts"]]
        W, H = 1200, 150 + 110 * len(rows) + 60
        img = Image.new("RGB", (W, H), vp.BG)
        d = ImageDraw.Draw(img)
        d.text((60, 50), "Funding radar", font=vp._font(44), fill=vp.WHITE)
        d.text((60, 104), "fee paid each period by the crowded side", font=vp._font(24, False), fill=vp.MUTED)
        top = max(abs(f) for _, f, _ in rows) or 1
        y = 170
        for t, f, long_ in rows:
            col = vp.GOLD if long_ else (140, 147, 161)
            d.text((60, y + 18), f"${t}" if False else t, font=vp._font(34), fill=vp.WHITE)
            w = int(620 * abs(f) / top)
            d.rounded_rectangle([260, y + 12, 260 + max(w, 8), y + 56], 8, fill=col)
            d.text((W - 60, y + 14), _f(f), font=vp._font(34), fill=col, anchor="ra")
            y += 110
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out
    except Exception:
        logger.exception("Карточка funding radar не построена")
        return None
