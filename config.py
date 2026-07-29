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

# Вероятность добавить CTA-строку про выгоду/фичи Binance Square к посту
# на Square (см. post_format.maybe_binance_cta) - не 100%, иначе каждый
# пост выглядел бы как реклама и мог бы триггернуть модерацию.
BINANCE_CTA_PROBABILITY = float(os.environ.get("BINANCE_CTA_PROBABILITY", "0.2"))

# Случайная задержка (в минутах) перед отложенным кросспостом в
# Telegram/Bluesky после публикации на Binance Square (см.
# main._schedule_crossposts) - каждая площадка получает свою
# независимую задержку в этом диапазоне, чтобы порядок публикации
# между площадками не был всегда одинаковым/механическим.
CROSSPOST_DELAY_MIN_MINUTES = float(os.environ.get("CROSSPOST_DELAY_MIN_MINUTES", "5"))
CROSSPOST_DELAY_MAX_MINUTES = float(os.environ.get("CROSSPOST_DELAY_MAX_MINUTES", "30"))

# Через сколько часов зависшая запись в очереди отложенных кросспостов
# считается устаревшей и удаляется (см. queue_manager.prune_stale_crossposts) -
# защита от бесконечного накопления в bot_state.db, если площадка была
# настроена в момент постановки в очередь, но перестала быть настроена.
CROSSPOST_STALE_HOURS = float(os.environ.get("CROSSPOST_STALE_HOURS", "24"))

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
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")

# --- Поведение бота ---
MIN_POST_INTERVAL_HOURS = float(os.environ.get("MIN_POST_INTERVAL_HOURS", "2"))
# Публикуем (и кладём в очередь сканера - см. scanner.py) только сигналы
# со score СТРОГО БОЛЬШЕ этого значения. Если в очереди нет ни одного
# такого - просто не публикуем в это окно и ждём следующего тика. На
# посты типа "image" (без числового score) порог не действует.
# 70 = нижняя граница качества "Moderate" в scanner._score_and_quality -
# раньше было 90 ("Conservative"), при текущей формуле почти недостижимо.
MIN_SIGNAL_SCORE_TO_PUBLISH = int(os.environ.get("MIN_SIGNAL_SCORE_TO_PUBLISH", "70"))

# Ручной денай-лист тикеров (без USDT, через запятую, например "PHB,FLOKI") -
# для монет, которые формально проходят фильтр по объёму (scanner.
# MIN_QUOTE_VOLUME_24H), но по своей природе слишком тонкие/волатильные и
# из-за этого непропорционально часто залетают в экстремальный RSI,
# вытесняя более качественные сетапы. Полностью убираются из сканирования
# в scanner._fetch_universe - сигналов по ним не будет вообще, а не
# просто с более низким приоритетом.
EXCLUDED_TICKERS = {
    t.strip().upper() for t in os.environ.get("EXCLUDED_TICKERS", "").split(",") if t.strip()
}

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
# Формат "Промо" - ТОЛЬКО Binance Square (см. binance_promo_generator.py),
# своё расписание, смещённое от остальных Square-форматов (opinion/
# article), чтобы не выходить в одно и то же окно каждый раз.
BINANCE_PROMO_INTERVAL_HOURS = float(os.environ.get("BINANCE_PROMO_INTERVAL_HOURS", "72"))
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
BINANCE_PROMO_JITTER_HOURS = float(os.environ.get("BINANCE_PROMO_JITTER_HOURS", "8"))

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

# --- Формат "Предложения по ребалансировке" (Telegram, Этап 4, A) ---
# Раз в ~месяц - редкий, глубокий разбор, не ежедневная рубрика. Бот
# только предлагает кандидатов на пересмотр (см. rebalance_advisor.py),
# решение о фактическом изменении BASKET в treasury_index.py принимает
# человек.
REBALANCE_REVIEW_INTERVAL_HOURS = float(os.environ.get("REBALANCE_REVIEW_INTERVAL_HOURS", "720"))
REBALANCE_REVIEW_JITTER_HOURS = float(os.environ.get("REBALANCE_REVIEW_JITTER_HOURS", "24"))

# Диаграмма состава корзины (treasury_composition_chart.py) - появляется
# не на каждый пост Treasury Index, а раз в N постов (14 постов при
# цикле в 12ч ~= раз в неделю) - состав статичен между ребалансировками,
# показывать его каждый раз избыточно.
TREASURY_COMPOSITION_INTERVAL_POSTS = int(os.environ.get("TREASURY_COMPOSITION_INTERVAL_POSTS", "14"))
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

# --- Futures-исполнение (testnet-первым делом, см. futures_client.py/
# futures_executor.py) - ЭТО НЕ ГЕНЕРАЦИЯ ПОСТОВ, а реальное управление
# позициями (пусть пока и на учебном счёте). Ключи ОБЯЗАНЫ иметь права
# ТОЛЬКО на торговлю (никогда не вывод средств) и никогда не должны
# попадать в git/логи - только через переменные окружения/GitHub Secrets.
#
# BINANCE_FUTURES_USE_TESTNET по умолчанию True - ОСОЗНАННЫЙ выбор:
# переключение на реальные деньги требует явно выставить
# BINANCE_FUTURES_USE_TESTNET=false, а не происходит само по себе из-за
# забытой/дефолтной переменной окружения.
BINANCE_FUTURES_API_KEY = os.environ.get("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.environ.get("BINANCE_FUTURES_API_SECRET", "")
BINANCE_FUTURES_USE_TESTNET = os.environ.get("BINANCE_FUTURES_USE_TESTNET", "true").strip().lower() != "false"
BINANCE_FUTURES_RECV_WINDOW_MS = int(os.environ.get("BINANCE_FUTURES_RECV_WINDOW_MS", "5000"))

# Риск-менеджмент по умолчанию - консервативные значения, которые
# ПОЛЬЗОВАТЕЛЬ должен осознанно увеличивать, а не наоборот.
BINANCE_FUTURES_DEFAULT_LEVERAGE = int(os.environ.get("BINANCE_FUTURES_DEFAULT_LEVERAGE", "3"))
# % от баланса, которым рискуем на ОДНУ сделку (не размер позиции целиком,
# а именно риск = сколько потеряем, если сработает стоп) - см.
# futures_executor.calc_position_size.
BINANCE_FUTURES_RISK_PCT_PER_TRADE = float(os.environ.get("BINANCE_FUTURES_RISK_PCT_PER_TRADE", "1.0"))

# --- Предохранители риска ПОВЕРХ отдельной сделки (см. risk_guard.py) ---
# BINANCE_FUTURES_RISK_PCT_PER_TRADE выше ограничивает риск ОДНОЙ
# сделки - но ничто не мешает открыть сколько угодно таких (по
# отдельности правильно посчитанных) позиций подряд. Эти три лимита -
# про СУММАРНУЮ картину. risk_guard.check_new_position_allowed
# вызывается futures_executor.open_protected_position ПЕРВЫМ делом, до
# единого API-вызова на биржу - именно это делает автоматический вход
# по сигналу (без ручного подтверждения на каждую сделку) безопасным.
#
# Максимум ОДНОВРЕМЕННО открытых позиций (across всех символов) - сверх
# него новая позиция не откроется, пока одна из текущих не закроется
# (это НЕ взводит kill switch ниже - самоустраняется само).
BINANCE_FUTURES_MAX_OPEN_POSITIONS = int(os.environ.get("BINANCE_FUTURES_MAX_OPEN_POSITIONS", "3"))
# Дневной лимит убытка в % от баланса на начало UTC-дня (фиксируется
# при первой проверке за день - см. risk_guard._daily_loss_pct). При
# достижении risk_guard ВЗВОДИТ kill switch - блокирует ВСЕ новые
# позиции, пока кто-то осознанно не снимет его (risk_guard_cli.py
# reset). Специально НЕ снимается сам по себе на следующий день.
BINANCE_FUTURES_MAX_DAILY_LOSS_PCT = float(os.environ.get("BINANCE_FUTURES_MAX_DAILY_LOSS_PCT", "5.0"))
# Сколько убыточных сделок ПОДРЯД (по факту закрытия на бирже - см.
# risk_guard._consecutive_losses) взводят kill switch так же, как
# дневной лимит выше.
BINANCE_FUTURES_MAX_CONSECUTIVE_LOSSES = int(os.environ.get("BINANCE_FUTURES_MAX_CONSECUTIVE_LOSSES", "3"))

# --- Автоматическое исполнение сигналов (см. futures_signal_bridge.py/
# futures_auto_trade.py) ---
# ОТДЕЛЬНЫЙ (и по умолчанию СТРОЖЕ) порог score от MIN_SIGNAL_SCORE_TO_PUBLISH
# (70) выше - "достаточно хорош, чтобы о нём написать пост" и "достаточно
# хорош, чтобы рискнуть на него реальными (пусть пока testnet) деньгами
# без подтверждения человека" - разные по цене ошибки решения, порог для
# второго должен быть заведомо не мягче первого.
BINANCE_FUTURES_MIN_SIGNAL_SCORE = int(os.environ.get("BINANCE_FUTURES_MIN_SIGNAL_SCORE", "80"))


def validate_config() -> list[str]:
    """Возвращает список незаполненных обязательных переменных."""
    required = {
        "FOLLOWUP_CHANNEL_USERNAME": FOLLOWUP_CHANNEL_USERNAME,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "BINANCE_SQUARE_API_KEY": BINANCE_SQUARE_API_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    return [name for name, value in required.items() if not value]