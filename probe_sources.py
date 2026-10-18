"""Разведка источников новостей/событий (запускается вручную в Actions)."""
import json
import sys
import requests

H = {"User-Agent": "Mozilla/5.0 (compatible; SquareBot/1.0)"}
CMS = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
       "?type=1&catalogId={cid}&pageNo=1&pageSize=5")
CATALOGS = {48: "New Cryptocurrency Listing", 49: "Latest Binance News", 93: "Latest Activities",
            161: "Delisting", 128: "Airdrop?", 51: "Wallet maintenance", 50: "API updates", 157: "Crypto Airdrop"}
FEEDS = ["https://www.coindesk.com/arc/outboundfeeds/rss/",
         "https://cointelegraph.com/rss",
         "https://decrypt.co/feed",
         "https://www.theblock.co/rss.xml",
         "https://cryptopanic.com/news/rss/",
         "https://www.binance.com/en/support/announcement/rss",
         "https://api.coingecko.com/api/v3/search/trending",
         "https://api.coingecko.com/api/v3/global"]


def main():
    out = []
    for cid, name in CATALOGS.items():
        try:
            r = requests.get(CMS.format(cid=cid), headers=H, timeout=15)
            j = r.json()
            cats = j["data"]["catalogs"]
            arts = cats[0]["articles"] if cats else []
            out.append(f"## catalog {cid} ({name}): HTTP {r.status_code}, {len(arts)} articles")
            for a in arts[:5]:
                out.append(f"- {a.get('releaseDate')} | {a.get('code')} | {a.get('title')}")
        except Exception as e:
            out.append(f"## catalog {cid} ({name}): ERROR {str(e)[:150]}")
    for u in FEEDS:
        try:
            r = requests.get(u, headers=H, timeout=15)
            out.append(f"## {u}: HTTP {r.status_code}, {len(r.content)} bytes, ctype={r.headers.get('content-type')}")
            out.append("```\n" + r.text[:600].replace("\n", " ") + "\n```")
        except Exception as e:
            out.append(f"## {u}: ERROR {str(e)[:150]}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
