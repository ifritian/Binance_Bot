import requests
from bs4 import BeautifulSoup

import config

url = config.TELEGRAM_PREVIEW_URL.format(channel=config.FOLLOWUP_CHANNEL_USERNAME)
resp = requests.get(url, timeout=15)
soup = BeautifulSoup(resp.text, "html.parser")

messages = soup.find_all("div", class_="tgme_widget_message")
print(f"Всего постов на странице: {len(messages)}\n")

# Берём последний пост (самый свежий) и печатаем его HTML целиком
last_msg = messages[-1]
print("=== HTML последнего поста ===\n")
print(last_msg.prettify()[:3000])  # первые 3000 символов, чтобы не было слишком много