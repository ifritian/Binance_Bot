"""Формат "Volume alert": резкий всплеск (или, у крупных монет, затишье) торговых объёмов за последний час.

Мем-коины - главная тема для разговоров на Square, поэтому для них порог ниже. Объём сравнивается с обычным часовым темпом
этой же монеты за предыдущие ~40 часов (медиана часовых блоков по 15-минутным свечам), цена - с началом последнего часа.
Пост - факты (во сколько раз, сколько долларов, куда пошла цена) и честная оговорка: объём показывает внимание, а не направление.
"""
import logging
import random
import statistics
from pathlib import Path

import trade_idea

logger = logging.getLogger(__name__)
KEY = "volume_alert_log"
MAJORS = {"BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"}
SPIKE_X, SPIKE_X_MEME = 6.0, 4.0
MOVE_MIN, MOVE_MIN_MEME = 1.0, 1.5
DROUGHT_X = 0.2
GOLD, RED, BG, GRID, MUTED, WHITE = "#F0B90B", "#F6465D", "#0B0E11", "#1E2329", "#848E9C", "#EAECEF"


def _usd(bars: list) -> list:
    return [b[5] * b[4] for b in bars]


def detect(ticker: str, bars: list) -> dict | None:
    """bars - 15-минутные свечи (t,o,h,l,c,v), лучше 192. -> {'kind','ratio','now_usd','base_usd','move_pct'} или None."""
    if len(bars) < 120:
        return None
    meme = trade_idea.is_meme(ticker)
    usd = _usd(bars)
    now = sum(usd[-4:])
    prior = usd[:-4]
    blocks = [sum(prior[i:i + 4]) for i in range(len(prior) - 4 * 40, len(prior) - 3, 4)] if len(prior) >= 160 else \
        [sum(prior[i:i + 4]) for i in range(0, len(prior) - 3, 4)]
    base = statistics.median(blocks) if blocks else 0.0
    if base <= 0:
        return None
    ratio = now / base
    move = (bars[-1][4] / bars[-4][1] - 1) * 100
    if ratio >= (SPIKE_X_MEME if meme else SPIKE_X) and abs(move) >= (MOVE_MIN_MEME if meme else MOVE_MIN):
        return {"kind": "spike", "ratio": ratio, "now_usd": now, "base_usd": base, "move_pct": move}
    if ticker in MAJORS:
        now3 = sum(usd[-12:])
        base3 = base * 3
        if base3 > 0 and now3 / base3 <= DROUGHT_X:
            return {"kind": "drought", "ratio": now3 / base3, "now_usd": now3, "base_usd": base3, "move_pct": move}
    return None


def _money(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    return f"${x / 1e3:.0f}K"


def build_text(t: str, d: dict, price: float, pct24: float, px, tone: str | None = None, rng=None, levels: list | None = None) -> str:
    rng = rng or random
    meme = trade_idea.is_meme(t)
    mv = f"{d['move_pct']:+.1f}%"
    if d["kind"] == "drought":
        head = rng.choice([f"${t} went quiet: volume is at {d['ratio'] * 100:.0f}% of its usual pace over the last 3 hours.",
                           f"Nobody is trading ${t}. Volume is {d['ratio'] * 100:.0f}% of normal for the last 3 hours.",
                           f"${t} volume dried up: {d['ratio'] * 100:.0f}% of its usual pace."])
        lines = [head, "", f"Price {px(price)} ({pct24:+.1f}% in 24h).", ""]
        lines.append(rng.choice(["Quiet markets can stay quiet, or break out fast. No prediction here.", "Low volume means low interest, not low risk."]))
    else:
        r = d["ratio"]
        tone = tone or ("explosive" if meme or r >= 10 else rng.choice(["formal", "wry"]))
        if tone == "explosive":
            head = rng.choice([f"🚨 ${t} volume just jumped {r:.1f}x. Price {mv} in the last hour.",
                               (f"🐸 ${t} is waking up: volume {r:.1f}x its normal pace, price {mv} in an hour." if meme else
                                f"⚡ ${t} trading volume is {r:.1f}x normal. Price {mv} in the last hour."),
                               f"👀 Something is happening on ${t}: {r:.1f}x volume, {mv} in an hour."])
        elif tone == "wry":
            head = rng.choice([f"${t} volume {r:.1f}x. Someone is doing something, and the chart is the only witness.",
                               f"${t} trades {r:.1f}x its normal volume. The group chats have noticed. The price ({mv}) already did.",
                               f"${t}: {r:.1f}x volume, {mv} in an hour. Quiet it was not."])
        else:
            head = f"${t} volume is running at {r:.1f}x its usual hourly pace, with price {mv} in the last hour."
        lines = [head, "", f"Last hour: about {_money(d['now_usd'])} traded. A typical hour: about {_money(d['base_usd'])}.",
                 f"Price {px(price)} ({pct24:+.1f}% in 24h).", "",
                 rng.choice(["A volume spike shows attention, not direction. It can come before a bigger move either way, or just fade.",
                             "Volume says people are paying attention. It does not say which way it goes next.",
                             "Spikes like this are loud. What follows is not predictable."])]
    if levels:
        lines += [""] + levels
    lines += ["", rng.choice(["Pump, dump, or just noise?", "Which side is this?", "Are you watching this one?", ""]) or "I will check back on it.",
              "", "Not financial advice.", "", f"#{t} #Crypto"]
    return "\n".join(l.replace(" -", " −") for l in lines)


def chart(t: str, bars: list, d: dict, out: Path) -> Path | None:
    """Цена (линия) и объёмы по 15-минутным свечам за ~48 ч; последний час подсвечен."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        n = len(bars)
        xs = list(range(n))
        closes = [b[4] for b in bars]
        vols = _usd(bars)
        up = d["move_pct"] >= 0
        col = GOLD if up else RED
        fig, (ax, av) = plt.subplots(2, 1, figsize=(10, 7), facecolor=BG, gridspec_kw={"height_ratios": [3, 1.6], "hspace": 0.08}, sharex=True)
        for a in (ax, av):
            a.set_facecolor(BG)
            a.tick_params(colors=MUTED)
            a.grid(color=GRID, linewidth=0.6)
            for s in a.spines.values():
                s.set_visible(False)
        ax.plot(xs, closes, color=WHITE, linewidth=1.8)
        ax.plot(xs[-4:], closes[-4:], color=col, linewidth=3.2)
        ax.scatter([n - 1], [closes[-1]], color=col, s=60, zorder=5)
        av.bar(xs, vols, color=[col if i >= n - 4 else "#3A414B" for i in xs], width=0.8)
        av.set_yticks([])
        ax.set_xticks([])
        title = f"${t} volume " + ("spike" if d["kind"] == "spike" else "drought")
        fig.suptitle(title, x=0.07, ha="left", color=WHITE, fontsize=20, fontweight="bold", y=0.96)
        ax.set_title(f"{d['ratio']:.1f}x normal hourly pace  |  last hour {d['move_pct']:+.1f}%".replace("-", "−"), loc="left", color=MUTED, fontsize=12)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=100, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        return out
    except Exception:
        logger.exception("График volume alert не построен")
        return None
