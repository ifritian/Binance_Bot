"""
Все настройки бота читаются из .env файла (см. .env.example).
Никаких ключей в коде - только через переменные окружения.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# --- Telegram (Bot API - бот добавлен админом в канал) ---
# Username канала без @, например: resultrsi
FOLLOWUP_CHANNEL_USERNAME = os.environ.get("FOLLOWUP_CHANNEL_USERNAME", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Канал для КРОССПОСТИНГА наших же постов (текст с Binance Square) -
# ОТДЕЛЬНЫЙ от FOLLOWUP_CHANNEL_USERNAME выше: тот канал бот только
# читает (источник дайджестов), а сюда сам ПУБЛИКУЕТ. Тот же бот
# (тот же TELEGRAM_BOT_TOKEN) должен быть добавлен админом и в этот
# канал тоже, с правом Post Messages.
# Формат: "@my_channel" (с собакой, для публичных) либо числовой
# chat_id вида "-1001234567890" (для приватных каналов).
TELEGRAM_PUBLISH_CHANNEL = os.environ.get("TELEGRAM_PUBLISH_CHANNEL", "")

# --- Bluesky (AT Protocol) - кросспостинг постов, как и в Telegram ---
# Оба значения ОПЦИОНАЛЬНЫ (как TELEGRAM_PUBLISH_CHANNEL выше) - если не
# заполнены, bluesky_publisher.is_configured() вернёт False и main.py
# молча пропустит кросспост в Bluesky, не считая это ошибкой.
#
# BLUESKY_HANDLE - твой handle в Bluesky (например "alexei.bsky.social").
# BLUESKY_APP_PASSWORD - App Password, НЕ основной пароль от аккаунта -
# создаётся в самом Bluesky: Settings -> App passwords -> Add App
# Password. Никакого Developer-портала или App Review не нужно.
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")
# Формат "Тизер-график" - доля ОБЫЧНЫХ (не "сильных", см.
# post_format.BLUESKY_THREAD_MIN_SCORE) сигналов с картинкой, которые
# уходят в Bluesky минималистичным тизером (см. post_format.
# build_bluesky_teaser) вместо полного хука+сетапа. Не 100%, иначе
# переход в Telegram за разбором обесценился бы - тизер должен быть
# исключением, а не нормой.
BLUESKY_TEASER_PROBABILITY = float(os.environ.get("BLUESKY_TEASER_PROBABILITY", "0.25"))

# Твой Telegram USER_ID для слушания личных сообщений (опционально)
# Если заполнено - бот будет принимать сигналы и из личных сообщений от тебя
YOUR_USER_ID = os.environ.get("YOUR_USER_ID")
if YOUR_USER_ID:
    YOUR_USER_ID = int(YOUR_USER_ID)

# --- Binance Square ---
BINANCE_SQUARE_API_KEY = os.environ.get("BINANCE_SQUARE_API_KEY", "")
BINANCE_SQUARE_BASE_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BINANCE_SQUARE_BASE_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"
BINANCE_SQUARE_ENDPOINT = f"{BINANCE_SQUARE_BASE_V1}/content/add"

# --- Groq (генерация текста) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# --- Поведение бота ---
MIN_POST_INTERVAL_HOURS = float(os.environ.get("MIN_POST_INTERVAL_HOURS", "2"))
# Публикуем (и кладём в очередь сканера - см. scanner.py) только сигналы
# со score СТРОГО БОЛЬШЕ этого значения. Если в очереди нет ни одного
# такого - просто не публикуем в это окно и ждём следующего тика. На
# посты типа "image" (без числового score) порог не действует.
# 70 = нижняя граница качества "Moderate" в scanner._score_and_quality -
# раньше было 90 ("Conservative"), при текущей формуле почти недостижимо.
MIN_SIGNAL_SCORE_TO_PUBLISH = int(os.environ.get("MIN_SIGNAL_SCORE_TO_PUBLISH", "70"))

# За сколько минут до открытия окна публикации "валюта" (и пока оно уже
# открыто) бот переходит в "активный" режим: на каждом тике дёргает
# сканер и проверяет канал. До этого момента - тик почти ничего не
# делает (без сетевых запросов к Binance/Telegram), чтобы не плодить
# в очереди сигналы, которые устареют до публикации, и не жечь лимиты
# впустую. Сам тик всё равно вызывается с частотой из cron (workflow) -
# здесь регулируется не частота запуска job'ы, а то, сколько РАБОТЫ она
# делает внутри.
ACTIVE_WINDOW_LOOKAHEAD_MINUTES = float(os.environ.get("ACTIVE_WINDOW_LOOKAHEAD_MINUTES", "30"))
OPINION_INTERVAL_HOURS = float(os.environ.get("OPINION_INTERVAL_HOURS", "48"))
# Формат "Хот-тейк" - ТОЛЬКО Bluesky (см. hot_take_generator.py), поэтому
# интервал сознательно смещён от OPINION_INTERVAL_HOURS (48ч), чтобы эти
# два формата не выходили в одно и то же окно каждый раз.
HOT_TAKE_INTERVAL_HOURS = float(os.environ.get("HOT_TAKE_INTERVAL_HOURS", "60"))
# Формат "Мини-урок" - тоже ТОЛЬКО Bluesky, своё расписание, смещённое
# от HOT_TAKE_INTERVAL_HOURS (60ч) и OPINION_INTERVAL_HOURS (48ч).
MINI_LESSON_INTERVAL_HOURS = float(os.environ.get("MINI_LESSON_INTERVAL_HOURS", "84"))
# Формат "Вопрос аудитории" - тоже только Bluesky, раз в неделю
AUDIENCE_QUESTION_INTERVAL_HOURS = float(os.environ.get("AUDIENCE_QUESTION_INTERVAL_HOURS", "168"))
ARTICLE_INTERVAL_HOURS = float(os.environ.get("ARTICLE_INTERVAL_HOURS", "168"))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

# --- Treasury Index (собственный инфраструктурный индекс, см. treasury_index.py) ---
# TREASURY_PERIOD_HOURS - за какой период считаем % изменения (окно свечей).
# TREASURY_INTERVAL_HOURS - как часто публикуем пост. Разделены нарочно:
# можно публиковать раз в 12ч, но при этом хотеть, чтобы период расчёта
# был другим (например, 24ч для более сглаженной картины) - по умолчанию
# совпадают, но это не обязано быть так.
TREASURY_PERIOD_HOURS = float(os.environ.get("TREASURY_PERIOD_HOURS", "12"))
TREASURY_INTERVAL_HOURS = float(os.environ.get("TREASURY_INTERVAL_HOURS", "12"))
TREASURY_JITTER_HOURS = float(os.environ.get("TREASURY_JITTER_HOURS", "1"))

# Сколько часов сигнал/картинка может пролежать в очереди публикации,
# прежде чем считается устаревшим и удаляется без публикации - RSI
# сигнал часовой давности уже мог выйти из зоны перекупленности/
# перепроданности, публиковать его как "свежий" было бы нечестно.
SIGNAL_MAX_AGE_HOURS = float(os.environ.get("SIGNAL_MAX_AGE_HOURS", "1"))

# --- Трекинг результатов опубликованных сигналов (outcome_tracker.py) ---
# Сколько часов после публикации мы ждём, пока цена дойдёт до тейка или
# стопа, прежде чем закрыть сигнал как "timeout" (ни то ни другое не
# случилось) и всё равно засчитать его в статистику по факту цены на
# момент таймаута. Без верхней границы часть сигналов зависала бы в
# open_outcomes навечно и никогда не попадала бы в win-rate.
OUTCOME_MAX_TRACK_HOURS = float(os.environ.get("OUTCOME_MAX_TRACK_HOURS", "48"))

# --- Алертинг владельцу в личку Telegram (alerting.py) ---
# Если currency-формат не публиковался дольше этого числа часов - это,
# скорее всего, не "просто нет хороших сигналов" (такое штатно бывает
# часами), а признак реальной поломки (протух API-ключ, упал источник
# сигналов, перестал запускаться workflow) - шлём алерт. Одно и то же
# предупреждение не дублируется чаще, чем раз в DEAD_MANS_SWITCH_HOURS
# (см. alerting.send_owner_alert).
DEAD_MANS_SWITCH_HOURS = float(os.environ.get("DEAD_MANS_SWITCH_HOURS", "24"))

# --- Еженедельный отчёт точности сигналов (accuracy_report_generator.py) ---
# Отдельный формат от currency/opinion/treasury/article - публикует
# статистику из outcome_tracker (win-rate, средний % результата) раз в
# ACCURACY_REPORT_INTERVAL_HOURS. Числа считаются кодом (см. treasury -
# та же идея), LLM только пишет хук поверх готовых цифр.
ACCURACY_REPORT_INTERVAL_HOURS = float(os.environ.get("ACCURACY_REPORT_INTERVAL_HOURS", "168"))
ACCURACY_REPORT_JITTER_HOURS = float(os.environ.get("ACCURACY_REPORT_JITTER_HOURS", "6"))
# Если за период закрылось меньше сигналов - пропускаем публикацию (не
# позориться отчётом "n=1, win-rate 0% или 100%" - статистически бессмысленно).
ACCURACY_REPORT_MIN_CLOSED_SIGNALS = int(os.environ.get("ACCURACY_REPORT_MIN_CLOSED_SIGNALS", "5"))

# --- Разбор неудачных сигналов (loss_review_generator.py, Фаза 4) ---
# Интервал и окно поиска специально близки друг к другу (4 дня / 4.5
# дня), чтобы соседние отчёты почти не пересекались одними и теми же
# сделками. MIN_LOSSES=1 - публикуем, если провал был хоть один: теперь
# у каждого случая есть реальные цифры для анализа (MFE - насколько
# цена всё-таки прошла в сторону тейка, и время до срабатывания стопа -
# см. outcome_tracker._mfe_pct), так что даже один случай даёт
# содержательный пост, а не просто "статистика по 1 сделке".
LOSS_REVIEW_INTERVAL_HOURS = float(os.environ.get("LOSS_REVIEW_INTERVAL_HOURS", "96"))
LOSS_REVIEW_JITTER_HOURS = float(os.environ.get("LOSS_REVIEW_JITTER_HOURS", "6"))
LOSS_REVIEW_LOOKBACK_DAYS = float(os.environ.get("LOSS_REVIEW_LOOKBACK_DAYS", "4.5"))
LOSS_REVIEW_MIN_LOSSES = int(os.environ.get("LOSS_REVIEW_MIN_LOSSES", "1"))

# Случайный разброс окна публикации (+/-), чтобы интервалы не были
# идеально механическими. Не меняет МИНИМАЛЬНЫЙ интервал в среднем -
# просто сдвигает конкретное окно туда-сюда на случайную величину.
CURRENCY_JITTER_MINUTES = float(os.environ.get("CURRENCY_JITTER_MINUTES", "20"))
OPINION_JITTER_HOURS = float(os.environ.get("OPINION_JITTER_HOURS", "4"))
HOT_TAKE_JITTER_HOURS = float(os.environ.get("HOT_TAKE_JITTER_HOURS", "6"))
MINI_LESSON_JITTER_HOURS = float(os.environ.get("MINI_LESSON_JITTER_HOURS", "8"))
AUDIENCE_QUESTION_JITTER_HOURS = float(os.environ.get("AUDIENCE_QUESTION_JITTER_HOURS", "12"))

# --- Формат "Экстренный" (Bluesky) - см. volatility_alert.py ---
# Порог движения $BTC за окно ниже, при превышении которого считаем это
# "рыночным скачком волатильности", достойным отдельного поста.
VOLATILITY_ALERT_THRESHOLD_PCT = float(os.environ.get("VOLATILITY_ALERT_THRESHOLD_PCT", "4.0"))
VOLATILITY_ALERT_WINDOW_HOURS = int(os.environ.get("VOLATILITY_ALERT_WINDOW_HOURS", "3"))
# Проверяется КАЖДЫЙ тик (не по расписанию, как остальные форматы) -
# кулдаун нужен, чтобы не постить про одно и то же движение рынка
# повторно, пока оно ещё не улеглось ниже порога.
EMERGENCY_COOLDOWN_HOURS = float(os.environ.get("EMERGENCY_COOLDOWN_HOURS", "6"))

# --- Формат "Глоссарий" (Telegram, Этап 2) - см. telegram_glossary.py ---
# Публикуется ТОЛЬКО в Telegram, последовательно (не случайно) - раз в
# несколько дней, чтобы серия ощущалась как регулярная рубрика, а не
# спам и не раз в месяц (когда прогресс забывается).
TELEGRAM_GLOSSARY_INTERVAL_HOURS = float(os.environ.get("TELEGRAM_GLOSSARY_INTERVAL_HOURS", "96"))
TELEGRAM_GLOSSARY_JITTER_HOURS = float(os.environ.get("TELEGRAM_GLOSSARY_JITTER_HOURS", "8"))

# --- Формат "Опросы/AMA" (Telegram, Этап 3) - см. telegram_engagement.py ---
TELEGRAM_POLL_INTERVAL_HOURS = float(os.environ.get("TELEGRAM_POLL_INTERVAL_HOURS", "120"))
TELEGRAM_POLL_JITTER_HOURS = float(os.environ.get("TELEGRAM_POLL_JITTER_HOURS", "10"))
TELEGRAM_AMA_INTERVAL_HOURS = float(os.environ.get("TELEGRAM_AMA_INTERVAL_HOURS", "240"))
TELEGRAM_AMA_JITTER_HOURS = float(os.environ.get("TELEGRAM_AMA_JITTER_HOURS", "12"))
ARTICLE_JITTER_HOURS = float(os.environ.get("ARTICLE_JITTER_HOURS", "12"))

DB_PATH = BASE_DIR / "bot_state.db"
LOG_PATH = BASE_DIR / "bot.log"

# --- Сигналы по монетам Treasury Index (index_signal_scanner.py) ---
# Тот же RSI/Bollinger сканер, что и scanner.py, но вселенная - только
# 15 монет индекса, не весь рынок. Идея: подписчикам, которые следят
# именно за этой корзиной, ценнее знать "SOL сейчас перепродан, удобная
# точка для докупки в рамках индекса", чем узнавать об этом только
# постфактум в еженедельной сводке Treasury Index.
INDEX_SIGNAL_INTERVAL_HOURS = float(os.environ.get("INDEX_SIGNAL_INTERVAL_HOURS", "8"))
INDEX_SIGNAL_JITTER_HOURS = float(os.environ.get("INDEX_SIGNAL_JITTER_HOURS", "2"))
# Порог публикации ниже, чем MIN_SIGNAL_SCORE_TO_PUBLISH у общего сканера -
# вселенная маленькая (15 монет), сигналов и так немного, задирать порог
# как для рынка в 150 пар означало бы почти никогда не публиковать.
MIN_INDEX_SIGNAL_SCORE_TO_PUBLISH = int(os.environ.get("MIN_INDEX_SIGNAL_SCORE_TO_PUBLISH", "55"))
INDEX_SIGNAL_ALERT_COOLDOWN_HOURS = float(os.environ.get("INDEX_SIGNAL_ALERT_COOLDOWN_HOURS", "12"))
INDEX_SIGNAL_MAX_AGE_HOURS = float(os.environ.get("INDEX_SIGNAL_MAX_AGE_HOURS", "2"))


def validate_config() -> list[str]:
    """Возвращает список незаполненных обязательных переменных."""
    required = {
        "FOLLOWUP_CHANNEL_USERNAME": FOLLOWUP_CHANNEL_USERNAME,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "BINANCE_SQUARE_API_KEY": BINANCE_SQUARE_API_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    return [name for name, value in required.items() if not value]