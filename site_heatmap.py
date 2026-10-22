"""Тепловая карта рынка для сайта: плитки монет, размер - объём торгов за 24ч, цвет - изменение за 24ч.

Раскладка (treemap, алгоритм squarified) считается при сборке страницы и выводится обычным HTML/CSS
с процентными координатами: работает без JS и на телефоне. Маленький скрипт раз в минуту обновляет цвета и
проценты по публичному API Binance; если запрос не удался, остаётся картинка момента сборки.
Цвета: рост - золото, падение - красный Binance, знак дублируется стрелкой (не только цвет).
"""
import html
import json

MAX_TILES = 40
CAP_PCT = 8.0   # на этом изменении цвет достигает полной насыщенности


def squarify(items: list[tuple[str, float]], x: float, y: float, w: float, h: float) -> list[tuple[str, float, float, float, float]]:
    """items - [(ключ, вес)] по убыванию веса -> [(ключ, x, y, w, h)] в тех же единицах."""
    items = [(k, v) for k, v in items if v > 0]
    total = sum(v for _, v in items)
    if not items or total <= 0:
        return []
    scale = w * h / total
    areas = [(k, v * scale) for k, v in items]
    out = []

    def worst(row, side):
        s = sum(a for _, a in row)
        return max(max(side * side * a / (s * s), (s * s) / (side * side * a)) for _, a in row)

    def layout(row, x, y, w, h):
        s = sum(a for _, a in row)
        if w >= h:   # колонка слева
            cw = s / h
            yy = y
            for k, a in row:
                ch = a / cw
                out.append((k, x, yy, cw, ch)); yy += ch
            return x + cw, y, w - cw, h
        rh = s / w
        xx = x
        for k, a in row:
            cw = a / rh
            out.append((k, xx, y, cw, rh)); xx += cw
        return x, y + rh, w, h - rh

    row = []
    rest = list(areas)
    while rest:
        side = min(w, h)
        nxt = rest[0]
        if not row or worst(row + [nxt], side) <= worst(row, side):
            row.append(nxt); rest.pop(0)
        else:
            x, y, w, h = layout(row, x, y, w, h); row = []
    if row:
        layout(row, x, y, w, h)
    return out


def tile_style(pct: float) -> tuple[str, str]:
    """(фон, цвет текста) по изменению за 24ч."""
    t = min(abs(pct) / CAP_PCT, 1.0)
    if abs(pct) < 0.05:
        return "#1b2027", "#eaecef"
    if pct > 0:   # от тёмного золота к яркому
        lo, hi = (58, 47, 8), (252, 213, 53)
        txt = "#0b0e11" if t > 0.45 else "#eaecef"
    else:         # от тёмно-красного к красному Binance
        lo, hi = (59, 18, 24), (246, 70, 93)
        txt = "#0b0e11" if t > 0.8 else "#eaecef"
    c = tuple(round(lo[i] + (hi[i] - lo[i]) * t) for i in range(3))
    return f"rgb({c[0]},{c[1]},{c[2]})", txt


def top_rows(rows: list[dict], allowed: set | None = None, n: int = MAX_TILES) -> list[dict]:
    """Строки /ticker/24hr -> [{sym, price, pct, vol}] по объёму, только USDT и (если задан) белый список."""
    out = []
    for r in rows or []:
        s = r.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        t = s[:-4]
        if allowed is not None and t not in allowed:
            continue
        try:
            out.append({"sym": t, "price": float(r["lastPrice"]), "pct": float(r["priceChangePercent"]),
                        "vol": float(r["quoteVolume"])})
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda d: -d["vol"])
    return out[:n]


def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 1:
        return f"{p:,.2f}"
    return f"{p:.4g}"


def _heat_block(rows: list[dict], height: float, cls: str) -> str:
    layout = squarify([(r["sym"], r["vol"] ** 0.6) for r in rows], 0, 0, 100, height)   # корень сглаживает разрыв BTC и остальных
    by = {r["sym"]: r for r in rows}
    k = height / 100
    tiles = []
    for sym, x, y, w, h in layout:
        r = by[sym]
        bg, fg = tile_style(r["pct"])
        arrow = "▲" if r["pct"] > 0.05 else ("▼" if r["pct"] < -0.05 else "•")
        area = w * h * (62 / height)   # площадь в "десктопных" единицах, чтобы пороги не зависели от формы блока
        tcls = "tile big" if area > 160 else ("tile mid" if area > 55 else ("tile xs" if area < 24 else "tile"))
        tiles.append(
            f'<a class="{tcls}" data-s="{html.escape(sym)}" href="https://www.binance.com/en/trade/{html.escape(sym)}_USDT" '
            f'rel="noopener" style="left:{x:.2f}%;top:{y / k:.2f}%;width:{w:.2f}%;height:{h / k:.2f}%;background:{bg};color:{fg}" '
            f'title="{html.escape(sym)} {_fmt_price(r["price"])} USDT">'
            f'<b>{html.escape(sym)}</b><span class="pc"><i class="ar">{arrow}</i> <em>{abs(r["pct"]):.1f}%</em></span>'
            f'<small class="px">{_fmt_price(r["price"])}</small></a>')
    return (f'<div class="heat {cls}" role="img" aria-label="Heat map of the most traded coins on Binance by 24 hour change">'
            f'{"".join(tiles)}</div>')


def heatmap_html(rows: list[dict]) -> str:
    """Два блока: широкий для экрана компьютера (все монеты) и высокий для телефона (самые крупные)."""
    if len(rows) < 6:
        return '<p class="empty">The live map could not be loaded just now. It refreshes every hour.</p>'
    return _heat_block(rows, 62, "hd") + _heat_block(rows[:20], 95, "hm")


SCRIPT = """
(function(){
  var tiles=[].slice.call(document.querySelectorAll('.heat .tile'));
  if(!tiles.length||!window.fetch) return;
  var seen={};var syms=tiles.map(function(t){return t.dataset.s+'USDT'}).filter(function(x){return seen[x]?false:(seen[x]=1)});
  var CAP=__CAP__;
  function color(p){var t=Math.min(Math.abs(p)/CAP,1),lo,hi,txt;
    if(Math.abs(p)<0.05) return ['#1b2027','#eaecef'];
    if(p>0){lo=[58,47,8];hi=[252,213,53];txt=t>0.45?'#0b0e11':'#eaecef'}else{lo=[59,18,24];hi=[246,70,93];txt=t>0.8?'#0b0e11':'#eaecef'}
    var c=lo.map(function(v,i){return Math.round(v+(hi[i]-v)*t)});return ['rgb('+c.join(',')+')',txt]}
  function fmt(p){return p>=1000?Math.round(p).toLocaleString('en-US').replace(/,/g,' '):p>=1?p.toFixed(2):Number(p.toPrecision(4)).toString()}
  function run(){
    fetch('https://data-api.binance.vision/api/v3/ticker/24hr?symbols='+encodeURIComponent(JSON.stringify(syms)))
      .then(function(r){return r.json()}).then(function(rows){
        if(!rows||!rows.length) return;
        var m={};rows.forEach(function(r){m[r.symbol]=r});
        tiles.forEach(function(t){var r=m[t.dataset.s+'USDT'];if(!r) return;
          var p=parseFloat(r.priceChangePercent),c=color(p);
          t.style.background=c[0];t.style.color=c[1];
          t.querySelector('.ar').textContent=p>0.05?'\\u25B2':(p<-0.05?'\\u25BC':'\\u2022');
          t.querySelector('.pc em').textContent=Math.abs(p).toFixed(1)+'%';
          t.querySelector('.px').textContent=fmt(parseFloat(r.lastPrice));});
        var s=document.getElementById('heat-stamp');
        if(s){var d=new Date();s.textContent='live, updated '+d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}
      }).catch(function(){});
  }
  run();setInterval(run,60000);
})();
""".replace("__CAP__", str(CAP_PCT))


def movers(rows: list[dict], k: int = 4) -> tuple[list[dict], list[dict]]:
    s = sorted(rows, key=lambda d: d["pct"], reverse=True)
    return [r for r in s[:k] if r["pct"] > 0], [r for r in s[::-1][:k] if r["pct"] < 0]
