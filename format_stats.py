"""Таблица "формат -> просмотры/лайки/комментарии/клики" по постам Square.

Данные просмотров в Square API недоступны, поэтому берём их из Creator Center (скриншоты)
и вносим в metrics/square_metrics.csv: колонки post_id,views,likes,comments,clicks
(clicks/комиссия - если есть, иначе пусто). Журнал публикаций (формат, время, картинка) берётся
из bot_state.db - ключ square_post_log.

  python format_stats.py --template   # печатает CSV-заготовку по последним постам (заполнить views)
  python format_stats.py              # печатает сводку по форматам и по наличию картинки
"""
import argparse
import csv
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

METRICS = "metrics/square_metrics.csv"
TZ = timezone(timedelta(hours=3))


def load_log(db: str = "bot_state.db") -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute("SELECT value FROM state WHERE key='square_post_log'").fetchone()
    return json.loads(row[0]) if row else []


def load_metrics(path: str = METRICS) -> dict[str, dict]:
    out = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("post_id") and (r.get("views") or "").strip():
                    out[r["post_id"].strip()] = {k: _num(r.get(k)) for k in ("views", "likes", "comments", "clicks")}
    except FileNotFoundError:
        pass
    return out


def _num(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def summarize(log: list[dict], metrics: dict[str, dict]) -> list[dict]:
    """Строки: по формату и по картинке. medianviews устойчивее к одному вирусному посту."""
    groups = defaultdict(list)
    for e in log:
        m = metrics.get(str(e.get("id")))
        if not m or m["views"] is None:
            continue
        groups[("fmt:" + (e.get("fmt") or "?"))].append(m)
        groups["image" if e.get("img") else "no image"].append(m)
    rows = []
    for k, ms in sorted(groups.items()):
        v = [m["views"] for m in ms]
        rows.append({"group": k, "posts": len(ms), "median_views": statistics.median(v), "mean_views": sum(v) / len(v),
                     "likes": sum(m["likes"] or 0 for m in ms), "comments": sum(m["comments"] or 0 for m in ms),
                     "clicks": sum(m["clicks"] or 0 for m in ms)})
    return sorted(rows, key=lambda r: -r["median_views"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="store_true")
    a = ap.parse_args()
    log = load_log()
    if a.template:
        print("post_id,views,likes,comments,clicks,time,fmt,head")
        for e in log:
            t = datetime.fromtimestamp(e["ts"], TZ).strftime("%d.%m %H:%M")
            print(f'{e.get("id")},,,,,{t},{e.get("fmt") or ""},"{e["head"][:60].replace(chr(34), "")}"')
        return
    rows = summarize(log, load_metrics())
    if not rows:
        print("Нет данных просмотров: заполните metrics/square_metrics.csv (см. --template).")
        return
    print(f'{"group":<22}{"posts":>6}{"median":>9}{"mean":>9}{"likes":>7}{"comm":>6}{"clicks":>7}')
    for r in rows:
        print(f'{r["group"]:<22}{r["posts"]:>6}{r["median_views"]:>9.0f}{r["mean_views"]:>9.1f}{r["likes"]:>7.0f}{r["comments"]:>6.0f}{r["clicks"]:>7.0f}')


if __name__ == "__main__":
    main()
