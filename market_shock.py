"""Формат "Market Shock": резкий обвал или рывок ВСЕГО рынка (а не одной монеты).

Срабатывает по времени события, а не по расписанию: BTC быстро (за ~час) или за сутки ушёл на
заметную величину, либо подавляющее большинство ликвидных монет одновременно на одной стороне.
Пост - факты (цифры, широта движения, лидеры/аутсайдеры), без прогноза и без громких слов.
"""
import logging
import random
import statistics
from pathlib import Path

import config

logger = logging.getLogger(__name__)
KEY = "market_shock_log"
GOLD, RED, BG, GRID, MUTED, WHITE = "#F0B90B", "#F6465D", "#0B0E11", "#1E2329", "#848E9C", "#EAECEF"
_STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "XUSD", "EUR", "PAXG", "XAUT", "BFUSD", "RLUSD", "AEUR", "EURI", "UST"}


def collect(rows: list, allowed: set, min_volume: float) -> list:
    """[(ticker, price, pct24, quote_volume)] по ликвидным монетам белого списка, по убыванию оборота."""
    out = []
    for r in rows or []:
        s = r.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        t = s[:-4]
        if t in _STABLE or (allowed and t not in allowed and t != "BTC"):
            continue
        try:
            price, pct, vol = float(r["lastPrice"]), float(r["priceChangePercent"]), float(r["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if vol >= min_volume and price > 0:
            out.append((t, price, pct, vol))
    out.sort(key=lambda x: -x[3])
    return out


def detect(coins: list, btc_fast: float | None) -> dict | None:
    """coins - результат collect(); btc_fast - % BTC за ~час (или None). -> событие или None."""
    btc = next((c for c in coins if c[0] == "BTC"), None)
    if btc is None or len(coins) < 8:
        return None
    pcts = [c[2] for c in coins]
    med = statistics.median(pcts)
    down = sum(1 for p in pcts if p < 0) / len(pcts)
    up = 1 - down
    b24 = btc[2]
    fast = btc_fast if btc_fast is not None else 0.0
    crash = fast <= -config.SHOCK_BTC_FAST_PCT or b24 <= -config.SHOCK_BTC_DAY_PCT or (med <= -config.SHOCK_MEDIAN_PCT and down >= 0.8)
    rally = fast >= config.SHOCK_BTC_FAST_PCT or b24 >= config.SHOCK_BTC_DAY_PCT or (med >= config.SHOCK_MEDIAN_PCT and up >= 0.8)
    if not (crash or rally) or (crash and rally):
        return None
    kind = "crash" if crash else "rally"
    ranked = sorted(coins[:40], key=lambda c: c[2])
    top = ranked[:3] if kind == "crash" else ranked[::-1][:3]
    return {"kind": kind, "btc_price": btc[1], "btc24": b24, "btc_fast": btc_fast, "median": med,
            "breadth": down if kind == "crash" else up, "n": len(coins), "leaders": top}


def build_text(ev: dict, px, rng: random.Random | None = None) -> str:
    rng = rng or random
    crash = ev["kind"] == "crash"
    b24, fast = ev["btc24"], ev["btc_fast"]
    if crash:
        head = rng.choice([f"The whole market is red: $BTC {b24:+.1f}% in 24h", f"Broad sell-off right now. $BTC {b24:+.1f}% on the day",
                           f"Crypto is falling together: $BTC {b24:+.1f}% in 24h"])
    else:
        head = rng.choice([f"The whole market is green: $BTC {b24:+.1f}% in 24h", f"Broad rally right now. $BTC {b24:+.1f}% on the day",
                           f"Crypto is rising together: $BTC {b24:+.1f}% in 24h"])
    lines = [head, "", f"$BTC {px(ev['btc_price'])}" + (f", {fast:+.1f}% in the last hour." if fast is not None and abs(fast) >= 0.5 else ".")]
    lines.append(f"{round(ev['breadth'] * ev['n'])} of {ev['n']} liquid coins are {'down' if crash else 'up'}; the median move is {ev['median']:+.1f}%.")
    lead = ", ".join(f"${t} {p:+.1f}%" for t, _pr, p, _v in ev["leaders"])
    lines.append(f"{'Hardest hit' if crash else 'Strongest'}: {lead}.")
    lines += ["", rng.choice(["Moves this wide are usually about the whole market, not one coin. I am not calling a bottom or a top.",
                             "When everything moves together, it is the market, not a single story. No prediction here.",
                             "A move this broad says little about what comes next. I will check back on it."]),
              "", "Not financial advice.", "", "#Bitcoin #Crypto"]
    return "\n".join(l.replace(" -", " −") for l in lines)


def chart(ev: dict, coins: list, out: Path) -> Path | None:
    """Горизонтальные столбики: изменение за 24ч у 12 самых ликвидных монет, BTC подсвечен."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        rows = coins[:12][::-1]
        fig, ax = plt.subplots(figsize=(10, 7), facecolor=BG)
        ax.set_facecolor(BG)
        cols = [(GOLD if p >= 0 else RED) for _t, _px, p, _v in rows]
        ys = range(len(rows))
        ax.barh(list(ys), [p for _t, _px, p, _v in rows], color=cols, height=0.62)
        ax.set_yticks(list(ys))
        ax.set_yticklabels([t for t, *_ in rows], color=WHITE, fontsize=13, fontweight="bold")
        for y, (_t, _p, pct, _v) in zip(ys, rows):
            ax.text(pct + (0.15 if pct >= 0 else -0.15), y, f"{pct:+.1f}%".replace("-", "−"), color=WHITE, fontsize=12,
                    va="center", ha="left" if pct >= 0 else "right")
        ax.axvline(0, color=MUTED, linewidth=1)
        ps = [p for _t, _px, p, _v in rows]
        ax.set_xlim(min(0, min(ps)) * 1.3 - 0.3, max(0, max(ps)) * 1.3 + 0.3)
        ax.tick_params(colors=MUTED)
        ax.grid(axis="x", color=GRID, linewidth=0.6)
        for s in ax.spines.values():
            s.set_visible(False)
        title = "Market-wide sell-off" if ev["kind"] == "crash" else "Market-wide rally"
        fig.suptitle(title, x=0.05, ha="left", color=WHITE, fontsize=20, fontweight="bold")
        ax.set_title(f"24h change, 12 most traded coins  |  median of {ev['n']} coins: {ev['median']:+.1f}%".replace("-", "−"),
                     loc="left", color=MUTED, fontsize=12)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=100, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        return out
    except Exception:
        logger.exception("График Market Shock не построен")
        return None
