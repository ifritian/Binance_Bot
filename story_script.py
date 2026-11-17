"""Сценарий видео-разбора новости: из текста источников -> сцены story_video.

Нейросеть (Groq) только вытаскивает факты в JSON; сборка сцен и все числа проверяются кодом. Любой факт, число или
собственное имя, которых нет в тексте источников, отбрасывается. Если проверенных фактов мало (меньше 4 сцен), сценарий не
строится, и вызывающий код делает обычное видео-новость.

Тексты источников используются только как материал для фактов; на экране - короткие формулировки, заголовок с атрибуцией
(издание, дата) и пометка UNCONFIRMED, если события не подтверждены официально."""
import json
import logging
import re
from datetime import datetime, timezone

import requests

import news_radar
import story_video as sv

logger = logging.getLogger(__name__)

MAX_WORDS = 15
MAX_FACTS_CHARS = 5000
_TAG = re.compile(r"<[^>]+>")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]+")

PROMPT = """You turn crypto news source text into a short visual explainer plan. Use ONLY facts stated in the SOURCE TEXT.
Never add numbers, names, causes or predictions that are not in the text. If something is not stated, use null.
Return ONLY JSON with this shape:
{
 "amount": {"text": "$106M+", "label": "REPORTED LOSS"} or null,      // one headline number from the text
 "regions": ["European Union"] or [],                                  // countries/regions the event is tied to (names as in the text)
 "map_line": "<=14 words: what happened and where/when" or null,
 "steps": [{"title": "<=5 words", "sub": "<=6 words", "say": "<=12 words"}] up to 5, or [],   // ONLY if the text describes a sequence (money flow, process)
 "statement": {"who": "Name", "label": "WHAT NAME SAYS", "headline": "<=14 words, close to the text", "say": "<=14 words", "note": "<=5 words"} or null,
 "compare": {"a": {"label": "...", "sub": "...", "value": 6300, "text": "$6.3B"}, "b": {"label": "...", "sub": "...", "value": 106, "text": "$106M+"}, "say_a": "<=14 words", "say_b": "<=14 words"} or null,
 "key_phrase": "2-4 consecutive words copied EXACTLY from the first headline",
 "intro": "<=18 words news-bulletin opener using only facts from the headline",
 "confirmed": true or false   // false if the text says unconfirmed / may / reportedly / not independently confirmed
}
Short plain English. No hype."""


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def fetch_article_text(link: str, timeout: int = 12) -> str:
    """Первые абзацы статьи как материал для фактов; "" при любой проблеме (платная стена, сеть)."""
    if not link.startswith("http"):
        return ""
    try:
        r = requests.get(link, headers=news_radar.HEADERS, timeout=timeout)
        if r.status_code != 200:
            return ""
        paras = re.findall(r"<p[^>]*>(.*?)</p>", r.text, flags=re.S | re.I)
        text = " ".join(strip_html(p) for p in paras if len(strip_html(p)) > 40)
        return text[:3000]
    except requests.RequestException:
        return ""


def gather_facts(story: dict, fetch=fetch_article_text) -> str:
    """Заголовки и описания всех изданий сюжета + начало статей. -> один текст-источник."""
    parts = []
    for it in story.get("items", [])[:4]:
        parts.append(f"[{it['source']}, {datetime.fromtimestamp(it['ts'], timezone.utc):%b %d %Y}] {it['title']}")
        if it.get("summary"):
            parts.append(strip_html(it["summary"]))
        body = fetch(it.get("link", "")) if it.get("link") else ""
        if body:
            parts.append(body)
    return " ".join(parts)[:MAX_FACTS_CHARS]


def _tokens(text: str) -> set:
    return {w.lower().strip("'’-") for w in _WORD.findall(text)}


def supported(line: str, source: str) -> bool:
    """Все числа строки есть в источнике, все слова с заглавной (кроме первого слова) тоже."""
    if not line:
        return False
    src_nums = {n.replace(",", "") for n in _NUM.findall(source)}
    if any(n.replace(",", "") not in src_nums for n in _NUM.findall(line)):
        return False
    src_tok = _tokens(source)
    words = _WORD.findall(line)
    for i, w in enumerate(words):
        if (i > 0 and w[0].isupper() and not w.isupper()) or (w.isupper() and len(w) > 2):
            if w.lower().strip("'’-") not in src_tok:
                return False
    return True


def _words_ok(line: str, limit: int = MAX_WORDS) -> bool:
    return bool(line) and len(line.split()) <= limit


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def ask_llm(source: str) -> dict | None:
    from groq_client import call_groq
    try:
        return parse_json(call_groq(PROMPT, "SOURCE TEXT:\n" + source, max_tokens=900, temperature=0.2))
    except Exception as e:
        logger.warning("Видео-разбор: нейросеть недоступна: %s", e)
        return None


_ALIASES = {"european union": sv.EU, "eu": sv.EU, "europe": sv.EU, "us": ("United States of America",), "u.s.": ("United States of America",),
            "usa": ("United States of America",), "united states": ("United States of America",), "uk": ("United Kingdom",),
            "south korea": ("South Korea",), "north korea": ("North Korea",)}


def resolve_regions(names: list, source: str) -> list:
    """Названия -> имена стран из набора карты; берём только упомянутые в источнике."""
    known = {c["name"] for c in sv._world()["c"]}
    low = source.lower()
    out = []
    for n in names or []:
        key = str(n).strip().lower()
        if key not in low and key not in ("european union",):
            continue
        out += [c for c in _ALIASES.get(key, (str(n).strip(),)) if c in known]
    return list(dict.fromkeys(out))


def cam_for(countries: list) -> tuple:
    """(камера, точка пульса) под выбранные страны: по самому крупному контуру каждой."""
    if set(countries) >= set(sv.EU) - {"Cyprus"}:
        return sv.EU_CAM, (10.0, 50.0)
    boxes = []
    for c in sv._world()["c"]:
        if c["name"] in countries and c["arr"]:
            ring = max(c["arr"], key=lambda a: np_area(a))
            boxes.append((ring[:, 0].min(), ring[:, 0].max(), ring[:, 1].min(), ring[:, 1].max()))
    if not boxes:
        return sv.WORLD_CAM, None
    x0, x1 = min(b[0] for b in boxes), max(b[1] for b in boxes)
    y0, y1 = min(b[2] for b in boxes), max(b[3] for b in boxes)
    lon0, lat0 = (x0 + x1) / 2, (y0 + y1) / 2
    import math
    k = min(600 / max((x1 - x0) * math.cos(math.radians(lat0)), 1), 560 / max(y1 - y0, 1))
    return (lon0, lat0, max(4.0, min(k, 34.0))), (lon0, lat0)


def np_area(a) -> float:
    return float((a[:, 0].max() - a[:, 0].min()) * (a[:, 1].max() - a[:, 1].min()))


def amount_value(text: str) -> float | None:
    """"$6.3B" / "106 million" -> число (для честной шкалы полос); None, если не разобрать."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(trillion|billion|million|thousand|[TBMK])?\b", str(text).replace(",", ""), flags=re.I)
    if not m:
        return None
    mult = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3, "trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3}
    return float(m.group(1)) * mult.get((m.group(2) or "").lower(), 1.0)


def _date(ts: float) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f"{d:%b} {d.day}, {d.year}"


def assemble(story: dict, plan: dict, source: str, brand: str) -> dict | None:
    """-> {"scenes": [...]} или None, если проверенных сцен меньше четырёх."""
    items = story.get("items") or []
    if not items or not plan:
        return None
    first = items[0]
    head = news_radar.clean_title(first["title"], 150).rstrip(".")
    unconfirmed = plan.get("confirmed") is False
    scenes = []
    intro = plan.get("intro")
    intro = intro if (_words_ok(intro, 20) and supported(intro, source)) else "A developing story."
    kp = str(plan.get("key_phrase") or "")
    scenes.append({"scene": "clip", "text": f"Crypto news. {intro}", "source": first["source"], "date": _date(first["ts"]),
                   "headline": head, "mark": kp if kp and kp in head else "", **({"tag": "UNCONFIRMED"} if unconfirmed else {})})
    amount = plan.get("amount") if isinstance(plan.get("amount"), dict) else None
    regions = resolve_regions(plan.get("regions") or [], source)
    line = plan.get("map_line")
    if amount and regions and _words_ok(line, 14) and supported(line, source) and supported(str(amount.get("text", "")), source):
        cam, pulse = cam_for(regions)
        scenes.append({"scene": "map", "text": line, "hl": tuple(regions), "cam_from": sv.WORLD_CAM, "cam": cam, "pulse": pulse,
                       "badge": str(amount["text"])[:12], "badge_label": str(amount.get("label", "REPORTED"))[:24].upper(),
                       "map_label": regions[0].upper() if len(regions) < 3 else "REGION",
                       **({"tag": "UNCONFIRMED"} if unconfirmed else {})})
    steps = [s for s in (plan.get("steps") or []) if isinstance(s, dict) and _words_ok(s.get("say", ""), 12)
             and all(supported(str(s.get(k, "")), source) for k in ("title", "sub", "say"))][:5]
    if len(steps) >= 3:
        nodes = [{"title": s["title"], "sub": s["sub"], "color": sv.GOLD} for s in steps]
        nodes[0]["color"] = sv.RED
        for n in range(2, len(steps) + 1):
            scenes.append({"scene": "route", "reveal": n, "nodes": nodes, "text": steps[n - 1]["say"]})
    st = plan.get("statement") if isinstance(plan.get("statement"), dict) else None
    if st and _words_ok(st.get("say", ""), 16) and supported(str(st.get("say", "")), source) and supported(str(st.get("headline", "")), source):
        scenes.append({"scene": "clip", "scene_id": 1, "text": st["say"], "source": items[min(1, len(items) - 1)]["source"],
                       "date": _date(items[min(1, len(items) - 1)]["ts"]), "kicker": str(st.get("label", ""))[:30].upper(),
                       "headline": str(st["headline"]), "mark": "", "note": str(st.get("note", ""))[:40]})
    cp = plan.get("compare") if isinstance(plan.get("compare"), dict) else None
    try:
        if cp and all(supported(str(cp[k].get(f)), source) for k in ("a", "b") for f in ("label", "sub", "text")) \
                and _words_ok(cp.get("say_a"), 16) and _words_ok(cp.get("say_b"), 16) \
                and supported(cp["say_a"], source) and supported(cp["say_b"], source):
            its = [{"label": cp[k]["label"][:34], "sub": cp[k]["sub"][:44], "value": amount_value(cp[k]["text"]) or 0.0, "text": str(cp[k]["text"])[:10],
                    "color": c} for k, c in (("a", sv.RED), ("b", sv.GOLD))]
            if all(i["value"] > 0 for i in its):
                for show, key in ((1, "say_a"), (2, "say_b")):
                    scenes.append({"scene": "compare", "show": show, "text": cp[key], "title": "SCALE, SAME AXIS", "items": its,
                                   "footnote": "Bars share one linear scale."})
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    if len(scenes) < 4:
        return None
    scenes.append({"scene": "outro", "text": f"That is the story. This is {brand}."})
    return {"scenes": scenes}


def build(story: dict, brand: str, fetch=fetch_article_text, llm=ask_llm) -> dict | None:
    source = gather_facts(story, fetch)
    if len(source) < 200:
        return None
    return assemble(story, llm(source), source, brand)
