import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

import telegram_listener

posts = telegram_listener.fetch_new_channel_posts()

print(f"\nВсего распознано постов: {len(posts)}")
for p in posts:
    print(f"id={p.post_id} | текст={'да' if p.text else 'нет'} | картинка={p.image_url}")