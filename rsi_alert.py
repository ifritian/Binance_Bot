"""Формат "Экстремальный RSI": монета разогнана или обвалена так, что RSI(14) на часовом графике почти на пределе.

Эффектный график (свечи + панель RSI с подсветкой зоны), без прогноза: экстремум говорит о скорости движения, а не о развороте.
Итог по монете публикуется позже через общий follow-up (mover_followups).
"""
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)
PERIOD = 14
HIGH, LOW = 80.0, 20.0
KEY = "rsi_alert_log"
GOLD, RED, BG, GRID, MUTED, WHITE = "#F0B90B", "#F6465D", "#0B0E11", "#1E2329", "#848E9C", "#EAECEF"


def rsi_series(closes: list, n: int = PERIOD) -> list:
    """RSI по Уайлдеру; список той же длины (первые n значений None)."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def detect(bars: list) -> dict | None:
    """bars: [(t,o,h,l,c,v)] 1h, лучше 200. -> {'rsi','kind','rank_days'} если последнее значение экстремально."""
    closes = [b[4] for b in bars]
    rs = rsi_series(closes)
    cur = rs[-1]
    if cur is None or not (cur >= HIGH or cur <= LOW):
        return None
    kind = "high" if cur >= HIGH else "low"
    vals = [r for r in rs[:-1] if r is not None]
    # сколько часов назад был такой же или более экстремальный RSI
    back = next((i for i, r in enumerate(reversed(vals), 1) if (r >= cur if kind == "high" else r <= cur)), None)
    return {"rsi": cur, "kind": kind, "since_h": back, "hours": len(vals)}


def build_text(t: str, d: dict, price: float, pct24: float, px, rng: random.Random | None = None) -> str:
    rng = rng or random
    word = "overbought" if d["kind"] == "high" else "oversold"
    head = rng.choice([f"${t} RSI just hit {d['rsi']:.0f} on the 1h chart", f"${t}: hourly RSI at {d['rsi']:.0f}, deep in {word} territory"])
    lines = [head, "", f"Price {px(price)} ({pct24:+.1f}% in 24h).".replace("-", "−")]
    if d["since_h"] is None:
        lines.append(f"That is the most extreme hourly reading in the last {d['hours'] // 24} days.")
    elif d["since_h"] >= 24:
        lines.append(f"The last time it was this {'high' if d['kind'] == 'high' else 'low'} was {d['since_h'] // 24} days ago.")
    lines += ["", rng.choice(["RSI measures how fast price moved, not where it goes next. It can stay extreme for a while.",
                             "An extreme RSI means the move was fast. Sometimes that reverses, sometimes it keeps going.",
                             "Speed, not direction: a stretched RSI is not a reversal signal on its own."]),
              "", "I will check back on it later, whatever happened.", "", "Not financial advice.", "", f"#{t} #RSI"]
    return "\n".join(lines)


def chart(t: str, bars_all: list, d: dict, out: Path) -> Path | None:
    """Свечи 72 часа + панель RSI(14) с подсветкой экстремальной зоны. RSI считается по всем переданным свечам."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        rs_all = rsi_series([b[4] for b in bars_all])
        bars, rs = bars_all[-72:], [r if r is not None else 50.0 for r in rs_all[-72:]]
        col_rsi = GOLD if d["kind"] == "high" else RED
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7.5), gridspec_kw={"height_ratios": [3, 1.4], "hspace": 0.08}, facecolor=BG)
        for ax in (a1, a2):
            ax.set_facecolor(BG)
            for s in ax.spines.values():
                s.set_color(GRID)
            ax.tick_params(colors=MUTED, labelsize=10)
            ax.grid(color=GRID, linewidth=0.6)
            ax.set_xlim(-1, len(bars) + 3)
        for i, (_t, o, h, l, c, _v) in enumerate(bars):
            col = GOLD if c >= o else "#8C93A1"
            a1.vlines(i, l, h, color=col, linewidth=1.2)
            a1.add_patch(plt.Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), (h - l) * 0.01), color=col))
        a1.set_title(f"{t}/USDT 1h", loc="left", color=WHITE, fontsize=18, fontweight="bold")
        a1.set_xticklabels([])
        a1.autoscale_view()
        a2.plot(range(len(rs)), rs, color=col_rsi, linewidth=2)
        lo_, hi_ = (HIGH, 100) if d["kind"] == "high" else (0, LOW)
        a2.axhspan(lo_, hi_, color=col_rsi, alpha=0.14)
        for lv in (30, 70):
            a2.axhline(lv, color=MUTED, linewidth=0.8, linestyle="--")
        a2.set_ylim(0, 100)
        a2.text(0.01, 0.92, "RSI(14)", transform=a2.transAxes, color=MUTED, fontsize=11, va="top")
        a2.text(len(rs) - 0.5, rs[-1], f" {rs[-1]:.0f}", color=WHITE, fontsize=16, fontweight="bold", va="center")
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=100, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        return out
    except Exception:
        logger.exception("График RSI не построен")
        return None
