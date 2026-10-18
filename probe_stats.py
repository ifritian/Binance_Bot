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
    for pid in ids:
        for url in (f"https://www.binance.com/en/square/post/{pid}",
                    f"https://www.binance.com/bapi/composite/v1/public/pgc/content/detail?contentId={pid}",
                    f"https://www.binance.com/bapi/composite/v1/friendly/pgc/content/detail?contentId={pid}"):
            try:
                r = requests.get(url, headers=H, timeout=20)
                found = re.findall(KEYS, r.text)
                print(f"{pid} {url.split('binance.com')[1][:60]} HTTP {r.status_code} len={len(r.text)} counts={found[:8]}")
                if "bapi" in url and r.status_code == 200:
                    print("   body:", r.text[:300].replace("\n", " "))
            except Exception as e:
                print(pid, url, "ERR", str(e)[:100])


if __name__ == "__main__":
    main()
