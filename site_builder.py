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
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import queue_manager

PROJECT = config.PROJECT_NAME
SQUARE_POST_URL = "https://www.binance.com/en/square/post/{id}"
FMT_LABELS = {"digest": "Market snapshot", "fear_greed": "Fear & Greed", "breakout": "Trend idea",
              "hot_mover": "Hot mover", "binance_radar": "Binance Radar", "news_move": "News + price",
              "listing": "New listing", "listing_report": "Listing report", "altcoin_basket": "Altcoin basket", "weekly_brief": "Weekly review",
              "question": "Question", "opinion": "Opinion"}


def collect() -> dict:
    g = queue_manager._get
    tb = g("trend_breakout_state") or {}
    lt = g("listing_tracker_state") or {}
    return {
        "snapshots": g("treasury_snapshots") or [],
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
    series = [("Altcoin basket", "index_value", "var(--accent)"), ("BTC", "btc_value", "var(--btc)"),
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


def render(d: dict, now_ts: float | None = None) -> str:
    now_ts = now_ts or time.time()
    ideas = stats_ideas(d["closed"])
    lst = stats_listings(d["listings"])
    snaps = d["snapshots"]
    gap = (snaps[-1]["index_value"] - snaps[-1]["btc_value"]) if snaps else None

    kpis = [
        ("Trend ideas closed", str(ideas["n"]), f"{ideas['wins']} in profit" if ideas["n"] else "counting since launch", ""),
        ("Average per idea", _pct(ideas["avg"]) if ideas["n"] else "n/a", "after 0.2% fees", _cls(ideas["avg"]) if ideas["n"] else ""),
        ("Basket vs BTC", ("±0.0 pts" if abs(gap) < 0.05 else _pct(gap).replace("%", " pts")) if gap is not None else "n/a",
         "since tracking started", "" if gap is None or abs(gap) < 0.05 else _cls(gap)),
        ("Listings tracked", str(lst["n"]) if lst else "0", f"avg first day {_pct(lst['avg'])}" if lst else "reports start after the first listing", ""),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="k">{_e(k)}</div><div class="v {c}">{_e(v)}</div><div class="s">{_e(s)}</div></div>'
                       for k, v, s, c in kpis)

    open_rows = [f"<tr><td>${_e(t)}</td><td>{p['entry']:.6g}</td><td>{p['stop']:.6g}</td><td>{p['held'] / 6:.1f} d</td></tr>"
                 for t, p in sorted(d["open"].items())]
    closed_rows = [f"<tr><td>${_e(c['ticker'])}</td><td>{_date(c['entry_t'] / 1000)}</td>"
                   f"<td class=\"{_cls(c['pnl'])}\">{_pct(c['pnl'])}</td><td>{'stop' if c['reason'] == 'stop' else 'time limit'}</td>"
                   f"<td>{c['held'] / 6:.1f} d</td></tr>" for c in reversed(d["closed"][-25:])]
    list_rows = [f"<tr><td>${_e(x['ticker'])}</td><td>{_date(x['listing_t'])}</td><td class=\"{_cls(x.get('pct_d1'))}\">{_pct(x.get('pct_d1'))}</td>"
                 f"<td class=\"{_cls(x.get('pct_d7'))}\">{_pct(x.get('pct_d7'))}</td></tr>" for x in reversed(d["listings"][-20:])]
    post_rows = []
    shown = [p for p in d["posts"] if p.get("fmt") in FMT_LABELS]  # только новые форматы с меткой
    for p in reversed(shown[-15:]):
        label = FMT_LABELS[p["fmt"]]
        head = p["head"].strip() + ("…" if len(p["head"].strip()) >= 85 else "")
        post_rows.append(f'<li><span class="tag">{_e(label)}</span> <a href="{SQUARE_POST_URL.format(id=_e(p["id"]))}" rel="noopener">{_e(head)}</a>'
                         f' <span class="muted">{_date(p["ts"])}</span></li>')

    upd = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{PROJECT}: live record</title>
<meta name="description" content="An automated crypto market desk with a public, unedited track record.">
<style>
:root{{--bg:#0b0e11;--panel:#14181d;--line:#232a31;--text:#eaecef;--muted:#8b949e;--accent:#2bb5e8;--btc:#f0b90b;--eth:#9b8cff;--up:#2bb5e8;--down:#ef4f9a}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f9;--panel:#fff;--line:#e3e6ea;--text:#14181d;--muted:#5b6670;--accent:#0b7fb0;--btc:#a77b00;--eth:#5a49d6;--up:#0b7fb0;--down:#c01f78}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:32px 16px 64px}}h1{{font-size:clamp(28px,5vw,40px);margin:0 0 6px;letter-spacing:-.02em}}
h2{{font-size:20px;margin:40px 0 12px}}.lead{{color:var(--muted);margin:0 0 4px;max-width:60ch}}.muted{{color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:24px}}
.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.k{{color:var(--muted);font-size:13px}}.v{{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;margin:2px 0}}.s{{color:var(--muted);font-size:13px}}
.up{{color:var(--up)}}.down{{color:var(--down)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
svg{{width:100%;height:auto;display:block}}.grid{{stroke:var(--line);stroke-width:1}}.axis{{fill:var(--muted);font-size:13px}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:14px}}.key i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-weight:600;font-size:13px}}
ul{{list-style:none;padding:0;margin:0}}li{{padding:9px 0;border-bottom:1px solid var(--line)}}li a{{color:var(--text);text-decoration:none;overflow-wrap:anywhere}}li a:hover{{text-decoration:underline}}
.tag{{display:inline-block;background:var(--line);border-radius:6px;padding:1px 8px;font-size:12px;margin-right:6px}}
.note{{border-left:3px solid var(--accent);padding:4px 0 4px 14px;color:var(--muted);max-width:68ch}}
footer{{margin-top:48px;color:var(--muted);font-size:13px}}
</style></head><body><main>
<h1>{PROJECT}</h1>
<p class="lead">An automated crypto market desk. Everything below comes straight from the bot's own records. Losing trades are listed too.</p>
<p class="muted">Updated {upd}</p>
<div class="kpis">{kpi_html}</div>

<h2>Altcoin basket vs BTC and ETH</h2>
<div class="card">{basket_chart(snaps)}<p class="muted" style="margin:8px 0 0">All lines start at 100. The basket is an equal-weight set of large altcoins.</p></div>

<h2>Trend ideas: closed</h2>
<p class="note">Method: a 20-day breakout in an uptrend, trailing stop, long only. In historical tests the edge was small and statistically weak, so treat it as unproven. This table is the live proof either way.</p>
{_table(["Coin", "Entry", "Net result", "Exit", "Held"], closed_rows)}

<h2>Trend ideas: open now</h2>
{_table(["Coin", "Entry price", "Current stop", "Held"], open_rows)}

<h2>Binance listings after launch</h2>
{_table(["Coin", "Listed", "After 24h", "After 7 days"], list_rows)}

<h2>Latest posts</h2>
<ul>{"".join(post_rows) or '<li class="muted">No posts yet.</li>'}</ul>

<footer>Not financial advice. Past results do not predict future results. Results are shown after estimated trading fees; real execution can differ.</footer>
</main></body></html>"""


def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = collect()
    (out_dir / "index.html").write_text(render(d), encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps({k: d[k] for k in ("closed", "listings", "snapshots")}, ensure_ascii=False), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")
    return out_dir / "index.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    print(build(Path(ap.parse_args().out)))
