"""
Публичная страница проекта: живой счёт, а не обещания.

Собирает один самодостаточный index.html (без внешних скриптов и шрифтов) из
состояния бота: корзина альткоинов против BTC/ETH, ВСЕ закрытые идеи (и
плюсовые, и минусовые), открытые идеи, отчёты по листингам Binance, последние
посты Square со ссылками. Любые числа берутся из базы, ничего не
подкрашивается.

Запуск: python site_builder.py --out site   (workflow site.yml публикует в gh-pages)
"""
import argparse
import html
import re
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import queue_manager

PROJECT = config.PROJECT_NAME
SQUARE_POST_URL = "https://www.binance.com/en/square/post/{id}"
FMT_LABELS = {"digest": "Market snapshot", "fear_greed": "Fear & Greed", "breakout": "Trend idea",
              "hot_mover": "Hot mover", "mover_followup": "Follow-up", "video": "Video", "binance_radar": "Binance Radar", "news_move": "News + price",
              "listing": "New listing", "listing_report": "Listing report", "altcoin_basket": "Altcoin basket", "weekly_brief": "Weekly review",
              "question": "Question", "opinion": "Opinion"}


def collect_live(now_ts: float) -> dict:
    """Живые данные для витрины (сеть): любой сбой - просто пустой блок."""
    out = {"binance": [], "fng": None, "moves": {}, "heat": []}
    try:
        import coin_whitelist, market_digest, site_heatmap
        rows = market_digest.fetch_ticker_rows()
        out["heat"] = site_heatmap.top_rows(rows, coin_whitelist.allowed_tickers()) if rows else []
    except Exception:
        pass
    try:
        import news_radar
        items = news_radar.fetch_binance(now_ts, 120)
        items.sort(key=lambda i: (news_radar.PRIORITY[i["kind"]] > 2, -i["ts"]))
        out["binance"] = items[:6]
        out["moves"] = news_radar.fetch_changes(["BTC", "ETH"])
    except Exception:
        pass
    try:
        import event_posts
        out["fng"] = event_posts.fetch_fng()
    except Exception:
        pass
    return out


def links() -> list:
    """Где подписаться: только то, что настроено."""
    out = []
    if getattr(config, "SQUARE_PROFILE_URL", ""):
        out.append(("Binance Square", config.SQUARE_PROFILE_URL))
    ch = str(config.TELEGRAM_PUBLISH_CHANNEL or "")
    if ch.startswith("@"):
        out.append(("Telegram", f"https://t.me/{ch.lstrip('@')}"))
    if config.BLUESKY_HANDLE:
        out.append(("Bluesky", f"https://bsky.app/profile/{config.BLUESKY_HANDLE}"))
    return out


def collect() -> dict:
    g = queue_manager._get
    tb = g("trend_breakout_state") or {}
    lt = g("listing_tracker_state") or {}
    return {
        "snapshots": __import__("basket_tracker").series(),
        "closed": tb.get("closed", []),
        "open": tb.get("positions", {}),
        "listings": lt.get("history", []),
        "listings_waiting": [e for e in (lt.get("tracked") or {}).values() if e.get("stage") in ("d1", "d7")],
        "posts": g("square_post_log") or [],
    }


def _e(x) -> str:
    return html.escape(str(x))


def _pct(p: float | None) -> str:
    return "n/a" if p is None else f"{p:+.1f}%".replace("-", "−")


def _cls(p: float | None) -> str:
    return "" if p is None or p == 0 else ("up" if p > 0 else "down")


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%b %d")


def stats_ideas(closed: list) -> dict:
    n = len(closed)
    if not n:
        return {"n": 0, "wins": 0, "avg": 0.0, "win_rate": 0.0}
    wins = sum(1 for c in closed if c["pnl"] > 0)
    return {"n": n, "wins": wins, "avg": sum(c["pnl"] for c in closed) / n, "win_rate": 100 * wins / n}


def stats_listings(listings: list) -> dict | None:
    v = [x["pct_d1"] for x in listings if x.get("pct_d1") is not None]
    if not v:
        return None
    v.sort()
    return {"n": len(v), "avg": sum(v) / len(v), "median": v[len(v) // 2], "up": sum(1 for x in v if x > 0)}


def basket_chart(snaps: list) -> str:
    if len(snaps) < 2:
        return '<p class="muted">The chart appears after a few snapshots of the basket.</p>'
    w, h, pl, pr, pt, pb = 760, 260, 44, 12, 14, 26
    series = [("Altcoin basket", "index_value", "var(--cyan)"), ("BTC", "btc_value", "var(--btc)"),
              ("ETH", "eth_value", "var(--eth)")]
    ys = [s[k] for s in snaps for _, k, _ in series if s.get(k) is not None]
    lo, hi = min(ys), max(ys)
    pad = max((hi - lo) * 0.12, 0.5)
    lo, hi = lo - pad, hi + pad
    t0, t1 = snaps[0]["timestamp"], snaps[-1]["timestamp"]
    X = lambda t: pl + (t - t0) / max(t1 - t0, 1) * (w - pl - pr)
    Y = lambda v: pt + (hi - v) / (hi - lo) * (h - pt - pb)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Altcoin basket versus BTC and ETH, base 100">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                   f'<text x="{pl - 6}" y="{Y(v) + 4:.1f}" class="axis" text-anchor="end">{v:.1f}</text>')
    out.append(f'<text x="{pl}" y="{h - 6}" class="axis">{_date(t0)}</text>'
               f'<text x="{w - pr}" y="{h - 6}" class="axis" text-anchor="end">{_date(t1)}</text>')
    for name, key, color in series:
        pts = " ".join(f"{X(s['timestamp']):.1f},{Y(s[key]):.1f}" for s in snaps if s.get(key) is not None)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>')
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{c}"></i>{n}: {snaps[-1][k]:.1f}</span>'
                     for n, k, c in series if snaps[-1].get(k) is not None)
    return "".join(out) + f'<div class="legend">{legend}</div>'


def _table(head: list, rows: list) -> str:
    if not rows:
        return '<p class="muted">Nothing here yet.</p>'
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


CSS = """
:root{--bg:#0b0e11;--panel:#12171d;--ink:#eaecef;--muted:#848e9c;--line:#1e2329;--cyan:#f0b90b;--magenta:#f6465d;--amber:#fcd535;--btc:#ffffff;--eth:#8c93a1}

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.62 "Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
a{color:inherit}
a:focus-visible,button:focus-visible{outline:3px solid var(--cyan);outline-offset:3px;border-radius:4px}
.wrap{max-width:960px;margin:0 auto;padding:0 20px}
h1,h2,h3,.brand,.big{font-family:"Bricolage Grotesque","Public Sans",system-ui,sans-serif;letter-spacing:-.02em}
header.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px 16px;padding:22px 0}
.brand{font-weight:700;font-size:20px;text-decoration:none;white-space:nowrap}
.brand img{width:34px;height:34px;border-radius:50%;vertical-align:middle;margin-right:10px}
.brand i{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--cyan);margin-right:10px;box-shadow:0 0 0 4px color-mix(in srgb,var(--cyan) 22%,transparent)}
nav.follow{display:flex;gap:18px;flex-wrap:wrap;font-size:15px}
nav.follow a{text-decoration:none;border-bottom:1px solid var(--line)}
nav.follow a:hover{border-color:var(--ink)}
.hero{padding:40px 0 20px;display:grid;grid-template-columns:1.1fr 1fr;gap:40px;align-items:start}
h1{font-size:clamp(36px,6.4vw,62px);line-height:1.02;margin:0 0 18px;font-weight:700}
.lead{font-size:19px;color:var(--muted);max-width:34ch;margin:0 0 26px}
.cta{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-block;padding:11px 17px;border-radius:10px;font-weight:600;text-decoration:none;border:1.5px solid var(--ink)}
.btn.main{background:var(--ink);color:var(--bg)}
.btn.ref{border-color:var(--cyan);color:var(--cyan)}
.btn:hover{transform:translateY(-1px)}
.now{margin-top:26px;color:var(--muted);font-size:15px;display:flex;flex-wrap:wrap;gap:6px 22px}
.now b{color:var(--ink);font-weight:600}
.stamp{margin:14px 0 0;color:var(--muted);font-size:13px}
.up{color:var(--cyan)}.down{color:var(--magenta)}
.wire{border-left:2px solid var(--ink);padding-left:20px}
.wire h2{font-size:15px;font-weight:600;margin:0 0 4px;display:flex;align-items:center;gap:8px;font-family:"Public Sans",sans-serif;letter-spacing:0}
.live{width:8px;height:8px;border-radius:50%;background:var(--amber);animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.35}}
.wire ol{list-style:none;margin:0;padding:0}
.wire li{padding:12px 0;border-bottom:1px solid var(--line);opacity:0;animation:rise .5s ease forwards}
.wire li:nth-child(1){animation-delay:.05s}.wire li:nth-child(2){animation-delay:.15s}.wire li:nth-child(3){animation-delay:.25s}.wire li:nth-child(4){animation-delay:.35s}.wire li:nth-child(5){animation-delay:.45s}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.wire .meta{font-size:13px;color:var(--muted)}
.wire a{text-decoration:none;display:block;overflow-wrap:anywhere}
.wire a:hover{text-decoration:underline}
.hero2{padding:22px 0 14px}
.hero2 h1{max-width:20ch;margin:0 0 12px;font-size:clamp(32px,5vw,50px)}
.hero2 .lead{max-width:52ch}
.eyebrow{display:flex;align-items:center;gap:10px;margin:0 0 14px;color:var(--cyan);font-weight:600;font-size:14px;letter-spacing:.06em;text-transform:uppercase}
.radar{width:22px;height:22px;border-radius:50%;border:1.5px solid var(--cyan);position:relative;overflow:hidden;display:inline-block}
.radar::before{content:"";position:absolute;inset:0;background:conic-gradient(from 0deg,transparent 0 300deg,rgba(240,185,11,.75) 360deg);animation:sweep 3s linear infinite}
.radar::after{content:"";position:absolute;left:50%;top:50%;width:3px;height:3px;margin:-1.5px;border-radius:50%;background:var(--cyan)}
@keyframes sweep{to{transform:rotate(360deg)}}
.board{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:22px;padding:6px 0 10px;align-items:start}
.heathead{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px 14px;margin-bottom:10px}
.heathead h2{font-size:22px;margin:0;font-family:"Bricolage Grotesque","Public Sans",sans-serif}
.muted2{color:var(--muted);font-size:13px}
.heat{position:relative;width:100%;aspect-ratio:100/62;background:var(--panel);border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.tile{position:absolute;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;text-decoration:none;border:1px solid var(--bg);overflow:hidden;line-height:1.15;transition:filter .15s}
.tile:hover{filter:brightness(1.15)}
.tile b{font-size:clamp(9px,1.5vw,13px);font-family:"Bricolage Grotesque","Public Sans",sans-serif}
.tile .pc{font-size:clamp(8px,1.2vw,12px);font-weight:600}
.tile .pc em{font-style:normal}.tile .ar{font-style:normal;font-size:.8em}
.tile .px{display:none;font-size:11px;opacity:.75}
.tile.xs .ar,.tile.xs .px{display:none}.tile.xs b{font-size:clamp(7px,1.1vw,10px)}.tile.xs .pc{font-size:clamp(7px,1vw,9px)}
.tile.mid b{font-size:clamp(10px,1.8vw,15px)}
.tile.big b{font-size:clamp(14px,3vw,26px)}.tile.big .pc{font-size:clamp(11px,1.9vw,17px)}.tile.big .px{display:block}
.scale{display:flex;align-items:center;gap:8px;margin-top:8px;color:var(--muted);font-size:12px}
.scale i{flex:1;height:6px;border-radius:3px;background:linear-gradient(90deg,#f6465d,#3b1218)}
.scale i.g{background:linear-gradient(90deg,#3a2f08,#fcd535)}
.side{display:flex;flex-direction:column;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.mood{display:grid;grid-template-columns:auto 1fr;align-items:baseline;gap:0 10px}
.mood span{color:var(--muted);font-size:14px;grid-column:1/3}
.mood .big{font-size:40px}.mood em{font-style:normal;color:var(--muted)}
.mood .bar{grid-column:1/3;height:8px;border-radius:4px;margin-top:8px;position:relative;background:linear-gradient(90deg,#f6465d,#f0b90b 50%,#fcd535)}
.mood .bar i{position:absolute;top:-4px;width:4px;height:16px;border-radius:2px;background:#fff;transform:translateX(-2px)}
.barlab{grid-column:1/3;display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:4px}
.chips{display:flex;gap:28px;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.chips span{display:block;color:var(--muted);font-size:13px}.chips b{font-size:18px}
.movers{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.movers h3{font-size:13px;color:var(--muted);font-weight:500;margin:0 0 6px}
.movers ul{list-style:none;margin:0;padding:0}.movers li{display:flex;justify-content:space-between;padding:3px 0}
.movers a{text-decoration:none;font-weight:600}
.wire{border-left:0;padding-left:0}
.wire h3{font-size:14px;margin:0 0 4px;display:flex;align-items:center;gap:8px;font-family:"Public Sans",sans-serif}
@media (max-width:860px){.board{grid-template-columns:1fr}}
.heat.hm{display:none;aspect-ratio:100/95}
@media (max-width:640px){.heat.hd{display:none}.heat.hm{display:block}.muted2{font-size:12px}}
section{padding:46px 0 6px}
h2.s{font-size:28px;line-height:1.15;margin:0 0 10px;font-weight:700}
.sub{color:var(--muted);max-width:62ch;margin:0 0 22px}
.offer{display:grid;grid-template-columns:1fr 1fr;gap:26px 40px}
.offer h3{font-size:19px;margin:0 0 4px}
.offer p{margin:0;color:var(--muted);font-size:16px}
.offer a{color:var(--ink);font-size:15px}
.fig{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 18px 12px}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}.axis{fill:var(--muted);font-size:13px}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px;font-size:15px}
.key i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:7px}
.statline{display:flex;flex-wrap:wrap;gap:8px 36px;margin:0 0 22px}
.statline div{min-width:130px}
.big{font-size:34px;font-weight:700;line-height:1.1}
.statline span{display:block;color:var(--muted);font-size:14px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 12px 9px 0;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:14px}
.empty{color:var(--muted);border-left:3px solid var(--line);padding:2px 0 2px 14px;max-width:64ch}
.note{border-left:3px solid var(--cyan);padding:2px 0 2px 14px;color:var(--muted);max-width:66ch;margin:0 0 18px}
ul.plain{list-style:none;margin:0;padding:0}
ul.plain li{padding:11px 0;border-bottom:1px solid var(--line)}
ul.plain .when{display:inline-block;min-width:64px;color:var(--muted);font-size:14px}
ul.plain .kind{font-weight:600;margin-right:8px}
.faq details{border-bottom:1px solid var(--line);padding:14px 0}
.faq summary{cursor:pointer;font-weight:600;list-style:none}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";float:right;color:var(--muted)}
.faq details[open] summary::after{content:"\\2212"}
.faq p{margin:8px 0 0;color:var(--muted);max-width:64ch}
footer{margin:60px 0 40px;padding-top:22px;border-top:1px solid var(--line);color:var(--muted);font-size:14px;max-width:70ch}
@media (max-width:600px){.axis{font-size:22px}}
@media (max-width:760px){.hero{grid-template-columns:1fr;gap:30px;padding-top:20px}.offer{grid-template-columns:1fr}.wire{border-left:0;padding-left:0;border-top:2px solid var(--ink);padding-top:14px}}
@media (prefers-reduced-motion: reduce){.radar::before{animation:none}.wire li{animation:none;opacity:1}.live{animation:none}.btn:hover{transform:none}}
"""

OFFER = [
    ("Market snapshots", "Twice a day: how Bitcoin, Ethereum and the biggest movers did over the last 24 hours, with the Fear & Greed mood.", "digest"),
    ("Binance Radar", "Delistings, wallet maintenance, hard forks and events, taken from Binance's own announcements and posted as they appear.", "binance_radar"),
    ("Listing reports", "What a coin did 24 hours and 7 days after Binance listed it, plus a running average across all listings we followed.", "listing_report"),
    ("Trend ideas", "Breakout ideas with an entry, a stop and an exit. Every idea is logged here, wins and losses, after fees.", "breakout"),
]

FAQ = [
    ("Is this financial advice?", "No. These are market observations and records of an automated method. Crypto prices swing hard and you can lose money. Decide for yourself."),
    ("Who writes the posts?", "An automated system builds them from Binance market data and public announcements. Numbers come from code, not from a person typing them. The owner reads and answers comments."),
    ("Are the trend ideas profitable?", "Unproven. In historical tests the method's edge was small and statistically weak, so this page exists to measure it in real time instead of promising anything. The table above is the answer, whatever it turns out to be."),
    ("What about fees?", "Results are shown after an estimated 0.2% fee per round trip. Real fills can differ."),
    ("Is anything paid or promoted?", "No coin promotions and no paid placements. The project can earn in two ways: Binance Square pays a commission when readers click a coin tag and trade, and the \"Open a Binance account\" button and some post links are referral links, which pay a share of trading fees if you sign up through them. It costs you nothing extra."),
]


def _links_html(ls: list) -> str:
    return "".join(f'<a href="{_e(u)}" rel="noopener">{_e(n)}</a>' for n, u in ls)


def _clean_head(head: str, limit: int = 78) -> str:
    """Подпись поста для ленты: без ведущих эмодзи, обрезка по границе слова."""
    t = re.sub(r"^[^\w$#]+", "", head.strip())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:-")
    return cut + "…"


def _ago(ts: float, now_ts: float) -> str:
    m = max(int((now_ts - ts) / 60), 0)
    if m < 2:
        return "just now"
    if m < 90:
        return f"{m} min ago"
    h = m // 60
    if h < 36:
        return f"{h} h ago"
    return f"{h // 24} days ago"


def _avatar_uri() -> str:
    import base64
    p = Path(__file__).parent / "assets" / "avatar.png"
    try:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    except OSError:
        return ""


def _momentum_section() -> str:
    """Бумажный недельный трекер импульса: честно как эксперимент, без обещаний."""
    try:
        import xs_momentum
        st = xs_momentum._state()
        s = xs_momentum.stats()
    except Exception:
        return ""
    pos, closed = st.get("open"), st.get("closed", [])
    if not pos and not closed:
        return ""
    tick = lambda syms: ", ".join("$" + _e(x["sym"].removesuffix("USDT")) for x in syms)
    parts = []
    if pos:
        day = min(int((time.time() - pos["t0"]) / 86400) + 1, 7)
        parts.append(f'<p class="sub">This week\'s paper basket (day {day} of 7). Strongest 28-day coins held long: '
                     f'{tick(pos["long"])}. Weakest held short: {tick(pos["short"])}.</p>')
    rows = [f"<tr><td>{_date(c['t0'])}</td><td class=\"{_cls(c['long_pct'])}\">{_pct(c['long_pct'])}</td>"
            f"<td class=\"{_cls(c['short_pct'])}\">{_pct(c['short_pct'])}</td>"
            f"<td class=\"{_cls(c['net'])}\">{_pct(c['net'])}</td></tr>" for c in reversed(closed[-12:])]
    table = (_table(["Week of", "Long side", "Short side", "Net after costs"], rows) if rows else
             '<p class="empty">No week has closed yet. The first result lands here when the first basket closes, win or lose.</p>')
    line = ""
    if s["n"]:
        line = (f'<div class="statline"><div><span>Weeks closed</span><b class="big">{s["n"]}</b></div>'
                f'<div><span>In profit</span><b class="big">{s["wins"]}</b></div>'
                f'<div><span>Average per week</span><b class="big {_cls(s["avg"])}">{_pct(s["avg"])}</b></div></div>')
    return ('<section><h2 class="s">Weekly momentum test</h2>'
            '<p class="note">A paper experiment, no real money. Every week the strongest and weakest coins of the last 28 days are '
            'tracked for seven days. Back-tests were mildly positive but statistically weak, so this is a live test, not a promise.</p>'
            f'{"".join(parts)}{line}{table}</section>')


def _wire(posts: list, now_ts: float | None = None, n: int = 3) -> str:
    shown = [p for p in posts if p.get("fmt") in FMT_LABELS][-n:]
    if not shown:
        return '<p class="empty">The first posts will appear here as soon as they go out.</p>'
    rows = []
    for p in reversed(shown):
        head = _clean_head(p["head"])
        absolute = datetime.fromtimestamp(p["ts"], timezone.utc).strftime("%b %d, %H:%M UTC")
        when = _ago(p["ts"], now_ts or time.time())
        rows.append(f'<li><div class="meta" title="{absolute}">{when} · {_e(FMT_LABELS[p["fmt"]])}</div>'
                    f'<a href="{SQUARE_POST_URL.format(id=_e(p["id"]))}" rel="noopener">{_e(head)}</a></li>')
    return f'<ol>{"".join(rows)}</ol>'


def _now_line(live: dict) -> str:
    bits = []
    for sym in ("BTC", "ETH"):
        v = (live.get("moves") or {}).get(sym)
        if v is not None:
            bits.append(f'<span>{sym} <b class="{_cls(v)}">{_pct(v)}</b> today</span>')
    f = live.get("fng")
    if f:
        bits.append(f'<span>Fear &amp; Greed <b>{f["value"]}</b> ({_e(f["label"])})</span>')
    return f'<div class="now">{"".join(bits)}</div>' if bits else ""


def _offer(posts: list) -> str:
    latest = {}
    for p in posts:
        if p.get("fmt"):
            latest[p["fmt"]] = p
    out = []
    for title, text, fmt in OFFER:
        p = latest.get(fmt)
        ex = (f'<a href="{SQUARE_POST_URL.format(id=_e(p["id"]))}" rel="noopener">See the latest</a>' if p else "")
        out.append(f"<div><h3>{_e(title)}</h3><p>{_e(text)} {ex}</p></div>")
    return "".join(out)


def _binance_list(items: list) -> str:
    if not items:
        return '<p class="empty">Binance announcements could not be loaded right now. They are loaded again every few hours.</p>'
    import news_radar
    rows = []
    for i in items:
        url = f"https://www.binance.com/en/support/announcement/detail/{_e(i['code'])}"
        rows.append(f'<li><span class="when">{_date(i["ts"])}</span><span class="kind">{_e(news_radar.LABELS[i["kind"]])}</span>'
                    f'<a href="{url}" rel="noopener">{_e(news_radar.clean_title(i["title"], 140))}</a></li>')
    return f'<ul class="plain">{"".join(rows)}</ul>'


def _mood_html(live: dict) -> str:
    f = live.get("fng")
    moves = live.get("moves") or {}
    if not f and not moves:
        return ""
    chips = "".join(f'<div><span>{sym}</span><b class="{_cls(v)}">{_pct(v)}</b></div>' for sym, v in moves.items() if v is not None)
    gauge = ""
    if f:
        gauge = (f'<div class="mood"><span>Fear &amp; Greed</span><b class="big">{f["value"]}</b><em>{_e(f["label"])}</em>'
                 f'<div class="bar" role="img" aria-label="Index {f["value"]} out of 100"><i style="left:{f["value"]}%"></i></div>'
                 f'<div class="barlab"><span>Fear</span><span>Greed</span></div></div>')
    return f'<div class="card">{gauge}<div class="chips">{chips}</div></div>' if (gauge or chips) else ""


def _movers_html(heat: list) -> str:
    if not heat:
        return ""
    import site_heatmap
    up, down = site_heatmap.movers(heat)
    def lst(title, items, cls):
        if not items:
            return ""
        li = "".join(f'<li><a href="https://www.binance.com/en/trade/{_e(r["sym"])}_USDT" rel="noopener">{_e(r["sym"])}</a>'
                     f'<b class="{cls}">{_pct(r["pct"])}</b></li>' for r in items)
        return f'<div><h3>{title}</h3><ul>{li}</ul></div>'
    body = lst("Leading today", up, "up") + lst("Lagging today", down, "down")
    return f'<div class="card movers">{body}</div>' if body else ""


def render(d: dict, now_ts: float | None = None, live: dict | None = None, ls: list | None = None) -> str:
    now_ts = now_ts or time.time()
    live = live or {}
    ls = links() if ls is None else ls
    ideas = stats_ideas(d["closed"])
    lst = stats_listings(d["listings"])
    snaps = d["snapshots"]
    gap = (snaps[-1]["index_value"] - snaps[-1]["btc_value"]) if snaps else None

    primary = ls[0] if ls else None
    cta = (f'<a class="btn main" href="{_e(primary[1])}" rel="noopener">Follow on {_e(primary[0])}</a>' if primary else "")
    cta += "".join(f'<a class="btn" href="{_e(u)}" rel="noopener">{_e(n)}</a>' for n, u in ls[1:])
    import post_format
    cta += f'<a class="btn ref" href="{_e(post_format.REFERRAL_LINK)}" rel="noopener sponsored">Open a Binance account</a>'

    idea_stats = (
        f'<div class="statline"><div><span>Closed</span><b class="big">{ideas["n"]}</b></div>'
        f'<div><span>In profit</span><b class="big">{ideas["wins"]}</b></div>'
        f'<div><span>Average per idea</span><b class="big {_cls(ideas["avg"])}">{_pct(ideas["avg"])}</b></div>'
        f'<div><span>Open now</span><b class="big">{len(d["open"])}</b></div></div>'
    ) if ideas["n"] or d["open"] else ""

    open_rows = [f"<tr><td>${_e(t)}</td><td>{p['entry']:.6g}</td><td>{p['stop']:.6g}</td><td>{p['held'] / 6:.1f} days</td></tr>"
                 for t, p in sorted(d["open"].items())]
    closed_rows = [f"<tr><td>${_e(c['ticker'])}</td><td>{_date(c['entry_t'] / 1000)}</td>"
                   f"<td class=\"{_cls(c['pnl'])}\">{_pct(c['pnl'])}</td><td>{'Stop hit' if c['reason'] == 'stop' else 'Time limit'}</td>"
                   f"<td>{c['held'] / 6:.1f} days</td></tr>" for c in reversed(d["closed"][-25:])]
    closed_html = (_table(["Coin", "Entered", "Result after fees", "Exit", "Held"], closed_rows) if closed_rows else
                   '<p class="empty">No idea has closed yet. The first one lands here the moment it closes, win or lose.</p>')
    open_html = (_table(["Coin", "Entry price", "Current stop", "Held"], open_rows) if open_rows else
                 '<p class="empty">Nothing open right now. The method needs a 20-day high while Bitcoin is trending up, so it often waits.</p>')
    list_rows = [f"<tr><td>${_e(x['ticker'])}</td><td>{_date(x['listing_t'])}</td><td class=\"{_cls(x.get('pct_d1'))}\">{_pct(x.get('pct_d1'))}</td>"
                 f"<td class=\"{_cls(x.get('pct_d7'))}\">{_pct(x.get('pct_d7'))}</td></tr>" for x in reversed(d["listings"][-20:])]
    list_html = (_table(["Coin", "Listed", "After 24 hours", "After 7 days"], list_rows) if list_rows else
                 '<p class="empty">No listing has been followed through yet. When Binance lists a coin, its 24-hour and 7-day results appear here.</p>')
    gap_txt = ""
    if gap is not None:
        word = "level with" if abs(gap) < 0.05 else (f"{abs(gap):.1f} points ahead of" if gap > 0 else f"{abs(gap):.1f} points behind")
        gap_txt = f'<p class="sub">Since tracking began, the basket is {word} Bitcoin.</p>'

    upd_short = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%b %d, %H:%M UTC")
    upd = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%b %d, %Y, %H:%M UTC")
    faq = "".join(f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>" for q, a in FAQ)
    import site_heatmap
    counter = (f'<script data-goatcounter="https://{_e(config.GOATCOUNTER_CODE)}.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>'
               if config.GOATCOUNTER_CODE else "")
    heat = live.get("heat") or []
    heat_html = site_heatmap.heatmap_html(heat)
    mood_html = _mood_html(live)
    movers_html = _movers_html(heat)
    av = _avatar_uri()
    avatar_img = f'<img src="{av}" alt="" width="34" height="34">' if av else "<i></i>"
    favicon = f'<link rel="icon" href="{av}">' if av else ""
    follow_nav = f'<nav class="follow" aria-label="Follow">{_links_html(ls)}</nav>' if ls else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(PROJECT)}: live crypto market map with a public record</title>
<meta name="description" content="Daily crypto market snapshots, Binance announcements and trend ideas, with every result published, losses included.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
{favicon}<meta property="og:title" content="{_e(PROJECT)}"><meta property="og:description" content="Crypto market desk with a public record: wins and losses published.">
<style>{CSS}</style></head><body>
<div class="wrap">
<header class="top"><a class="brand" href="#">{avatar_img}{_e(PROJECT)}</a>{follow_nav}</header>

<div class="hero2">
  <p class="eyebrow"><span class="radar" aria-hidden="true"></span>Live market radar</p>
  <h1>See what is moving in crypto, at a glance.</h1>
  <p class="lead">A live map of the most traded coins on Binance, the market mood, and a public record of every call we make, wins and losses.</p>
  <div class="cta">{cta}</div>
</div>

<section class="board" aria-label="Live market">
  <div class="heatwrap">
    <div class="heathead"><h2>Market map</h2><span class="muted2">Top coins by trading volume. Colour shows the 24 hour change. <span id="heat-stamp">Updated {upd_short}</span></span></div>
    {heat_html}
    <div class="scale" aria-hidden="true"><span>−8%</span><i></i><span>0</span><i class="g"></i><span>+8%</span></div>
  </div>
  <aside class="side">
    {mood_html}
    {movers_html}
    <div class="wire" aria-label="Latest posts"><h3><span class="live" aria-hidden="true"></span>Latest posts</h3>{_wire(d["posts"], now_ts)}</div>
  </aside>
</section>

<section><h2 class="s">What you get</h2>
<p class="sub">Four kinds of posts. Each one is built from Binance data and official announcements, not from rumors.</p>
<div class="offer">{_offer(d["posts"])}</div></section>

<section><h2 class="s">The record</h2>
<p class="sub">Trend ideas are breakouts in an uptrend with a trailing stop. We publish the outcome of every one.</p>
{idea_stats}
<p class="note">In historical tests this method had only a small edge, and it was statistically weak. Treat it as unproven. This table is the live test.</p>
<h3>Closed ideas</h3>{closed_html}
<h3 style="margin-top:28px">Open now</h3>{open_html}</section>

{_momentum_section()}

<section><h2 class="s">Altcoin basket against Bitcoin and Ethereum</h2>
{gap_txt}
<div class="fig">{basket_chart(snaps)}<p class="sub" style="margin:6px 0 4px;font-size:14px">An equal-weight basket of large altcoins. All lines start at 100.</p></div></section>

<section><h2 class="s">After a Binance listing</h2>
<p class="sub">{("So far " + str(lst["n"]) + " listings followed; on average " + _pct(lst["avg"]) + " after 24 hours. A small sample, not a forecast.") if lst else "What a new coin does in its first day and first week on Binance."}</p>
{list_html}</section>

<section><h2 class="s">Latest from Binance</h2>
<p class="sub">Official announcements, most important first: delistings, network work, listings and events.</p>
{_binance_list(live.get("binance") or [])}</section>

<section class="faq"><h2 class="s">Questions</h2>{faq}</section>

<footer>Not financial advice. Past results do not predict future results. Market data from Binance, sentiment index from alternative.me. Some links to Binance are referral links; the project may earn a commission if you sign up through them, at no cost to you. Page rebuilt {upd}.</footer>
</div><script>{site_heatmap.SCRIPT}</script>{counter}</body></html>"""



def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = collect()
    (out_dir / "index.html").write_text(render(d, live=collect_live(time.time())), encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps({k: d[k] for k in ("closed", "listings", "snapshots")}, ensure_ascii=False), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")
    return out_dir / "index.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    print(build(Path(ap.parse_args().out)))
