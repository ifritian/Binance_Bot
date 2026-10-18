"""Разведка: можно ли получить просмотры/лайки своих постов Square без ключей."""
import json
import re
import sqlite3
import requests

H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
     "Accept-Language": "en-US,en;q=0.9"}
KEYS = r'"(viewCount|likeCount|commentCount|shareCount|quoteCount|repostCount|totalCommentCount)"\s*:\s*(\d+)'


def main():
    c = sqlite3.connect("bot_state.db")
    log = json.loads(c.execute("select value from state where key='square_post_log'").fetchone()[0])
    ids = [e["id"] for e in log[-8:]]
    print("ids:", ids)
    pid = ids[-1]
    base = "https://www.binance.com/bapi/composite/v1/public/pgc/content/detail"
    tests = [f"{base}?contentId={pid}", f"{base}?id={pid}", f"{base}?postId={pid}", f"{base}?contentIds={pid}",
             f"https://www.binance.com/bapi/composite/v2/public/pgc/content/detail?id={pid}",
             f"https://www.binance.com/bapi/composite/v1/public/pgc/content/queryByIds?ids={pid}",
             f"https://www.binance.com/bapi/composite/v1/public/pgc/content/detail/{pid}",
             f"https://www.binance.com/bapi/composite/v3/friendly/pgc/content/detail?id={pid}"]
    for u in tests:
        try:
            r = requests.get(u, headers=H, timeout=20)
            print(f"GET {u.split('binance.com')[1]} -> {r.status_code} {r.text[:400]!r}")
        except Exception as e:
            print(u, "ERR", str(e)[:100])
    for u in tests[:3]:
        try:
            r = requests.post(base, headers={**H, "Content-Type": "application/json"}, json={"contentId": pid, "id": pid}, timeout=20)
            print(f"POST {base.split('binance.com')[1]} -> {r.status_code} {r.text[:400]!r}")
            break
        except Exception as e:
            print("POST ERR", str(e)[:100])


if __name__ == "__main__":
    main()
