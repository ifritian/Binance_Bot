"""Видео-разбор новости со сценами-визуализациями (вместо "диктор + график цены").

Сцены: map (тёмная карта мира с подсветкой стран, камера с зумом, пульс), route (схема "куда ушли деньги" по шагам),
clip (карточка-вырезка новости: источник, дата, заголовок с жёлтым маркером), compare (две полосы в одном масштабе),
outro. Субтитры, голос, музыка, заставка и шапка - те же, что в news_video.py.

Честность: в сценарий попадают только факты из источника; на карточках всегда источник и дата, а неподтверждённое
помечается плашкой UNCONFIRMED. Карта - Natural Earth (общественное достояние), картинок людей и чужих кадров нет.
"""
import json
import math
import random
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
import news_video as nv
import video_post as vp
from video_post import BG, GOLD, GOLD2, GRID, MUTED, PANEL, RED, WHITE, H, W, _font, _text, ease

FPS = nv.FPS
VOICE_SPEED, GAP_S = 1.28, 0.12          # быстрее диктор и короче паузы: ролик плотнее
_MAP_FILE = Path(__file__).resolve().parent / "data" / "world_lowres.json"
OCEAN, LAND, LAND_EDGE = (7, 12, 20), (17, 29, 46), (42, 66, 100)
EU = ("Germany", "France", "Italy", "Spain", "Poland", "Netherlands", "Belgium", "Austria", "Sweden", "Finland", "Denmark", "Ireland",
      "Portugal", "Greece", "Czechia", "Hungary", "Romania", "Bulgaria", "Croatia", "Slovakia", "Slovenia", "Lithuania", "Latvia",
      "Estonia", "Luxembourg", "Cyprus")
WORLD_CAM = (15.0, 25.0, 4.2)         # lon, lat центра, пикселей на градус
EU_CAM = (12.0, 50.0, 30.0)
_WORLD: dict = {}
_VIGNETTE: list = []


# ---------------- Карта ----------------

def _world() -> dict:
    if not _WORLD:
        data = json.loads(_MAP_FILE.read_text(encoding="utf-8"))
        for c in data["countries"]:
            c["arr"] = [np.asarray(r, dtype=np.float64) for r in c["rings"] if len(r) >= 3]
        _WORLD["c"] = data["countries"]
    return _WORLD


def _vignette() -> Image.Image:
    if not _VIGNETTE:
        a = np.zeros((H, W), dtype=np.float32)
        y = np.arange(H, dtype=np.float32)[:, None]
        a += np.clip((230 - y) / 230, 0, 1) * 0.92
        a += np.clip((y - 860) / 300, 0, 1) * 0.92
        _VIGNETTE.append(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L").resize((W, H)))
    return _VIGNETTE[0]


def _proj(arr: np.ndarray, cam: tuple) -> list:
    lon0, lat0, k = cam
    x = W / 2 + (arr[:, 0] - lon0) * k * math.cos(math.radians(lat0))
    y = 640 - (arr[:, 1] - lat0) * k
    return list(zip(x.tolist(), y.tolist()))


def _lerp_cam(a: tuple, b: tuple, t: float) -> tuple:
    e = ease(t)
    k = math.exp(math.log(a[2]) * (1 - e) + math.log(b[2]) * e)          # зум плавный в логарифмическом масштабе
    return (a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e, k)


def render_map(cam: tuple, highlight: set, T: float, pulse: tuple | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), OCEAN)
    d = ImageDraw.Draw(img)
    lon0, lat0, k = cam
    step = 10 if k >= 12 else 30
    for lon in range(-180, 181, step):                                   # градусная сетка
        x = W / 2 + (lon - lon0) * k * math.cos(math.radians(lat0))
        if -10 <= x <= W + 10:
            d.line([(x, 0), (x, H)], fill=(12, 20, 32), width=1)
    for lat in range(-80, 81, step):
        y = 640 - (lat - lat0) * k
        if -10 <= y <= H + 10:
            d.line([(0, y), (W, y)], fill=(12, 20, 32), width=1)
    hl_mask = Image.new("L", (W, H), 0)
    hd = ImageDraw.Draw(hl_mask)
    hl_lines = []
    for c in _world()["c"]:
        is_hl = c["name"] in highlight
        for arr in c["arr"]:
            pts = _proj(arr, cam)
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(xs) < -20 or min(xs) > W + 20 or max(ys) < -20 or min(ys) > H + 20:
                continue
            d.polygon(pts, fill=LAND, outline=LAND_EDGE)
            if is_hl:
                hd.polygon(pts, fill=255)
                hl_lines.append(pts)
    if highlight:
        glow = hl_mask.filter(ImageFilter.GaussianBlur(14))
        img.paste(Image.new("RGB", (W, H), GOLD), (0, 0), glow.point(lambda v: int(v * 0.4)))
        img.paste(Image.new("RGB", (W, H), (120, 88, 8)), (0, 0), hl_mask.point(lambda v: int(v * 0.45)))
        d = ImageDraw.Draw(img)
        for pts in hl_lines:
            d.line(pts + [pts[0]], fill=GOLD2, width=2)
    if pulse:
        x = W / 2 + (pulse[0] - lon0) * k * math.cos(math.radians(lat0))
        y = 640 - (pulse[1] - lat0) * k
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for j in range(3):
            ph = ((T * 0.8) + j / 3) % 1.0
            r = 14 + ph * 120
            od.ellipse([x - r, y - r, x + r, y + r], outline=(*RED, int(255 * (1 - ph))), width=4)
        od.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(*RED, 255), outline=(255, 255, 255, 255), width=2)
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    dark = Image.new("RGB", (W, H), BG)
    img.paste(dark, (0, 0), _vignette())
    return img


# ---------------- Сцены ----------------

def _pill(d, xy, text, color, font, fill=None):
    x, y = xy
    w = int(font.getlength(text)) + 36
    d.rounded_rectangle([x, y, x + w, y + 46], radius=23, outline=color, width=3, fill=fill)
    d.text((x + 18, y + 23), text, font=font, fill=color, anchor="lm")
    return w


def _scene_map(img, d, T, st, seg, ctx):
    cam = _lerp_cam(seg.get("cam_from", WORLD_CAM), seg.get("cam", EU_CAM), st / 2.2)
    base = render_map(cam, set(seg.get("hl", ())), T, seg.get("pulse") if st > 1.6 else None)
    img.paste(base)
    d = ImageDraw.Draw(img)
    return d


def _map_overlay(d, st, seg):
    big = seg.get("badge")
    if big and st > 0.4:
        a = ease((st - 0.4) / 0.5)
        y = 215 + int((1 - a) * -30)
        d.rounded_rectangle([48, y, 420, y + 118], radius=14, fill=(8, 10, 13), outline=GOLD, width=3)
        _text(d, (72, y + 14), seg.get("badge_label", ""), _font(21), MUTED, alpha=a)
        _text(d, (72, y + 78), big, _font(62), GOLD2, "lm", a)
        if seg.get("tag"):
            _pill(d, (48, y + 132), seg["tag"], RED, _font(22), fill=(8, 10, 13))
    if seg.get("map_label") and st > 2.0:
        _text(d, (W // 2, 905), seg["map_label"], _font(26), WHITE, "mm", ease((st - 2.0) / 0.5))


def _scene_route(img, d, T, st, seg):
    nodes = seg["nodes"]
    n = seg["reveal"]
    top, gap, h = 235, 160, 112
    x0, w = 48, W - 96
    for i in range(n):
        y = top + i * gap
        a = ease((st - 0.15) / 0.55) if i == n - 1 else 1.0
        if a <= 0:
            continue
        yy = y + int((1 - a) * 26)
        if i > 0:                                                       # линия-связь с предыдущим узлом
            prog = ease((st - 0.0) / 0.5) if i == n - 1 else 1.0
            ya, yb = top + (i - 1) * gap + h, yy
            d.line([(x0 + 54, ya), (x0 + 54, ya + (yb - ya) * prog)], fill=GOLD, width=4)
            if i == n - 1 and prog < 1:
                py = ya + (yb - ya) * prog
                d.ellipse([x0 + 54 - 9, py - 9, x0 + 54 + 9, py + 9], fill=GOLD2)
        col = nodes[i].get("color", GOLD)
        d.rounded_rectangle([x0, yy, x0 + w, yy + h], radius=18, fill=PANEL, outline=col if i == n - 1 else GRID, width=3 if i == n - 1 else 2)
        d.ellipse([x0 + 28, yy + h // 2 - 26, x0 + 80, yy + h // 2 + 26], fill=col)
        _text(d, (x0 + 54, yy + h // 2), str(i + 1), _font(28), (11, 14, 17), "mm")
        _text(d, (x0 + 108, yy + 36), nodes[i]["title"], _font(32), WHITE, "lm")
        _text(d, (x0 + 108, yy + 78), nodes[i]["sub"], _font(23, False), MUTED, "lm")
    _text(d, (48, 200), seg.get("route_title", "WHERE THE MONEY WENT"), _font(24), GOLD)


_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"


def _serif(size: int):
    try:
        return ImageFont.truetype(_SERIF, size)
    except OSError:
        return _font(size)


def _scene_clip(img, d, T, st, seg):
    f = _serif(38)
    cw = 620
    lines = nv._wrap(seg["headline"], f, cw - 70)
    words = [(i, w) for i, ln in enumerate(lines) for w in ln.split()]
    mark = set(seg.get("mark", "").split())
    ch = 150 + len(lines) * 54 + (70 if seg.get("note") else 0)
    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=14, fill=(244, 243, 238, 255))
    cd.rounded_rectangle([0, 0, cw - 1, 64], radius=14, fill=(26, 30, 38, 255))
    cd.rectangle([0, 40, cw - 1, 64], fill=(26, 30, 38, 255))
    cd.text((26, 33), seg["source"].upper(), font=_font(23), fill=GOLD2, anchor="lm")
    cd.text((cw - 26, 33), seg["date"], font=_font(20, False), fill=(190, 196, 206), anchor="rm")
    if seg.get("kicker"):
        cd.text((35, 92), seg["kicker"], font=_font(20), fill=(150, 30, 40), anchor="lm")
    y0 = 118
    sweep = ease((st - 0.9) / 0.8)
    for li, ln in enumerate(lines):
        y = y0 + li * 54
        x = 35
        for w in ln.split():
            wlen = f.getlength(w)
            if w.strip(".,") in mark and sweep > 0:
                cd.rectangle([x - 4, y + 4, x - 4 + (wlen + 8) * sweep, y + 48], fill=(252, 213, 53, 255))
            cd.text((x, y), w, font=f, fill=(20, 22, 28))
            x += f.getlength(w + " ")
    if seg.get("note"):
        cd.line([(35, ch - 78), (cw - 35, ch - 78)], fill=(205, 205, 198), width=2)
        cd.text((35, ch - 40), seg["note"], font=_font(22, False), fill=(90, 96, 108), anchor="lm")
    a = ease(st / 0.55)
    ang = -2.4 + (1 - a) * 6
    card = card.rotate(ang, resample=Image.BICUBIC, expand=True)
    zoom = 1.0 + 0.012 * min(st, 6.0)                                     # медленный наезд: кадр не стоит на месте
    card = card.resize((int(card.width * zoom), int(card.height * zoom)), Image.BICUBIC)
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (0, 0), card.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    px, py = (W - card.width) // 2, 250 + int((1 - a) * 90)
    base = img.convert("RGBA")
    base.alpha_composite(shadow, (px + 6, py + 16))
    alpha = card.split()[3].point(lambda v: int(v * a))
    card.putalpha(alpha)
    base.alpha_composite(card, (px, py))
    img.paste(base.convert("RGB"))
    if seg.get("tag") and st > 1.2:
        dd = ImageDraw.Draw(img)
        _pill(dd, (W // 2 - 110, py + card.height + 24), seg["tag"], RED if seg.get("tag_red", True) else GOLD, _font(24))


def _scene_compare(img, d, T, st, seg):
    _text(d, (48, 215), seg.get("title", "SCALE"), _font(24), GOLD)
    items = seg["items"]
    top = max(i["value"] for i in items)
    for j, it in enumerate(items):
        y = 310 + j * 270
        show = seg.get("show", len(items))
        a = 1.0 if j < show - 1 else (ease(st / 0.6) if j == show - 1 else 0.0)
        if a <= 0:
            continue
        _text(d, (48, y), it["label"], _font(30), WHITE, "lm", a)
        _text(d, (48, y + 40), it["sub"], _font(22, False), MUTED, "lm", a)
        d.rounded_rectangle([48, y + 80, W - 48, y + 150], radius=10, fill=PANEL)
        wbar = max(6, int((W - 96) * it["value"] / top * a))
        d.rounded_rectangle([48, y + 80, 48 + wbar, y + 150], radius=10, fill=it.get("color", GOLD))
        _text(d, (W - 60 if wbar > 260 else 48 + wbar + 14, y + 115), it["text"], _font(34), (11, 14, 17) if wbar > 260 else WHITE,
              "rm" if wbar > 260 else "lm", a)
    if seg.get("footnote"):
        _text(d, (48, 880), seg["footnote"], _font(22, False), MUTED, alpha=ease((st - 0.4) / 0.5))


# ---------------- Кадр ----------------

def frame(T: float, tl: list, ts: datetime, total: float) -> Image.Image:
    seg = next((s for s in tl if s["start"] <= T < s["end"]), tl[-1] if T >= tl[-1]["end"] else tl[0])
    first = next(s for s in tl if s["scene"] == seg["scene"] and s.get("scene_id", 0) == seg.get("scene_id", 0))
    st = max(0.0, T - first["start"])
    seg_t = max(0.0, T - seg["start"])
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    sc = seg["scene"]
    if sc == "map":
        d = _scene_map(img, d, T, st, seg, None)
    else:
        off = int(T * 12) % 60
        for k in range(-1, 22):
            d.line([(0, 190 + k * 60 + off), (W, 190 + k * 60 + off)], fill=(16, 20, 25), width=1)
    nv._chrome(img, d, T, ts, None)
    if sc == "map":
        _map_overlay(d, st, seg)
    elif sc == "route":
        _scene_route(img, d, T, seg_t, seg)
    elif sc == "clip":
        _scene_clip(img, d, T, seg_t, seg)
        d = ImageDraw.Draw(img)
    elif sc == "compare":
        _scene_compare(img, d, T, seg_t, seg)
    elif sc == "outro":
        nv._scene_outro(d, st)
    if st < 0.3 and T > nv.LEAD_S and sc != "map":
        x = int(W * ease(st / 0.3))
        d.rectangle([x - 40, 190, x, 1160], fill=GOLD)
    sub = nv._subtitle_state(tl, T) if sc != "outro" else None
    if sub:
        nv._subtitle(img, d, sub)
    d.rectangle([0, H - 8, W, H], fill=GRID)
    d.rectangle([0, H - 8, int(W * min(T / total, 1.0)), H], fill=GOLD)
    return vp._sting(img, T)


def build_script(story: dict) -> list:
    """Сценарий: story["scenes"] уже размечены (пока вручную/нейросетью); добавляем say и заключение канала."""
    segs = [dict(s) for s in story["scenes"]]
    for s in segs:
        s.setdefault("say", nv._say(s["text"]))
        s["gap"] = GAP_S
    return segs


def render(story: dict, out: Path, ts: datetime | None = None, tts="auto") -> tuple:
    """-> (mp4, poster, длительность, есть_ли_голос)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден")
    ts = ts or datetime.now(timezone.utc)
    segs = build_script(story)
    engine = nv.ensure_tts() if tts == "auto" else tts
    voice, durs = nv.synth(segs, engine, speed=VOICE_SPEED, gap_s=GAP_S)
    tl = nv.timeline(segs, durs)
    total = tl[-1]["end"] + nv.TAIL_S
    audio = nv.mix(voice, nv.music(total), nv.LEAD_S)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    nv.write_wav(wav, audio)
    poster = out.with_suffix(".png")
    frame(tl[1]["start"] + 3.4, tl, ts, total).save(poster)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
           "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(int(total * FPS)):
            proc.stdin.write(frame(i / FPS, tl, ts, total).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
        wav.unlink(missing_ok=True)
    return out, poster, total, voice is not None
