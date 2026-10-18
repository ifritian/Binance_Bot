"""Читает журнал публикаций Square из bot_state.db (только чтение).
Запуск: py posts_report.py  - печатает последние посты: время (Бухарест), id, начало текста."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

con = sqlite3.connect("file:bot_state.db?mode=ro", uri=True)
row = con.execute("SELECT value FROM state WHERE key='square_post_log'").fetchone()
log = json.loads(row[0]) if row else []
tz = timezone(timedelta(hours=3))
print(f"Всего записей: {len(log)}")
for e in log[-40:]:
    t = datetime.fromtimestamp(e["ts"], tz).strftime("%d.%m %H:%M")
    print(f"{t} | id={e.get('id')} | {(e.get('fmt') or '?'):<14} | {'📷' if e.get('img') else '  '} | {e['head']}")
