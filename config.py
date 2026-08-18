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
# Лимиты Groq free tier считаются ОТДЕЛЬНО по каждой модели (свой пул
# RPM/TPM/RPD на модель, см. https://console.groq.com/docs/rate-limits).
# GROQ_MODEL используется только для приоритетного формата (валютный
# сигнал - text_generator) - у него весь бюджет основной модели в
# одиночку. Все второстепенные форматы (мнение, хот-тейк, мини-урок,
# глоссарий, AMA, промо, поздравления, алерты волатильности, рибаланс)
# идут через GROQ_MODEL_SECONDARY - отдельная модель со своим пулом,
# поэтому они не конкурируют с публикацией сигналов за один и тот же
# TPM/RPM и не выбивают друг друга в 429, когда несколько окон публикации
# открываются в один тик.
GROQ_MODEL_SECONDARY = os.environ.get("GROQ_MODEL_SECONDARY", "llama-3.1-8b-instant")

# --- Поведение бота ---
MIN_POST_INTERVAL_HOURS = float(os.environ.get("MIN_POST_INTERVAL_HOURS", "2"))
# Публикуем (и кладём в очередь сканера - см. scanner.py) только сигналы
# со score СТРОГО БОЛЬШЕ этого значения. Если в очереди нет ни одного
# такого - просто не публикуем в это окно и ждём следующего тика. На
# посты типа "image" (без числового score) порог не действует.
# 70 = нижняя граница качества "Moderate" в scanner._score_and_quality -
# раньше было 90 ("Conservative"), при текущей формуле почти недостижимо.
MIN_SIGNAL_SCORE_TO_PUBLISH = int(os.environ.get("MIN_SIGNAL_SCORE_TO_PUBLISH", "70"))

# Минимальное соотношение прибыль/риск (см. signal_parser.calc_risk_reward_ratio),
# ниже которого сигнал СТРУКТУРНО отбраковывается - независимо от score
# и от того, насколько "уверенно" выглядит сама стратегия. Базовое
# правило риск-менеджмента любого профессионального трейдера: сделка с
# R:R хуже этого порога математически убыточна в среднем даже при
# честном 50% win-rate, и никакая "уверенность" в сигнале это не
# компенсирует. По факту накопленной статистики (см. .env.example/
# config.py комментарий про BINANCE_FUTURES_MIN_SIGNAL_SCORE) именно
# MACD Crossover, у которой стоп и тейк оба привязаны к одному и тому
# же 20-свечному диапазону без учёта ТЕКУЩЕГО положения цены внутри
# него, регулярно давала сделки с R:R заметно хуже 1:1 - что и
# объясняет её отрицательный средний результат при неплохом win-rate
# (много мелких выигрышей, которые не перекрывают редкие крупные
# проигрыши). 1.2, а не ровно 1.0 - небольшой запас на комиссии/
# проскальзывание (см. D1, futures_executor._calc_slippage_pct),
# которые сами по себе слегка снижают реальный R:R относительно
# теоретического.
MIN_RISK_REWARD_RATIO = float(os.environ.get("MIN_RISK_REWARD_RATIO", "1.2"))

# Cooldown (в часах) на конкретный символ после закрытия реальной позиции
# по стопу (см. futures_position_monitor._determine_close_reason_and_cleanup,
# futures_signal_bridge.execute_signal) - если сигнал по этому же символу
# приходит раньше, чем прошло это время с последнего стоп-аута, он
# пропускается для РЕАЛЬНОГО исполнения (посты в Telegram/Binance Square
# не затрагиваются - см. queue_manager.mark_stopped_out). Идея: сразу
# после стоп-аута повышен статистический шанс, что цена продолжает
# "пилить" в районе того же уровня, а не даёт чистый новый сетап - см.
# роадмап фазы 2, пункт P1.1.
FUTURES_SYMBOL_COOLDOWN_HOURS = float(os.environ.get("FUTURES_SYMBOL_COOLDOWN_HOURS", "4"))

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
# Было 12 (дважды в день) - слишком часто для "среза" индекса, снижает
# ценность каждого отдельного поста. Раз в сутки.
TREASURY_INTERVAL_HOURS = float(os.environ.get("TREASURY_INTERVAL_HOURS", "24"))
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
# Ежедневный дайджест владельцу (см. ops_digest.py) - привязан к
# КОНКРЕТНОМУ часу UTC (см. OPS_DIGEST_HOUR_UTC ниже), не к плавающему
# интервалу от последнего запуска - раньше был именно троттлинг-порог
# "не чаще раза в ЭТО число часов" без привязки к времени суток, из-за
# чего момент отправки постепенно "дрейфовал" на произвольное время
# дня. Теперь OPS_DIGEST_HOUR_UTC решает, В КАКОЙ час отправлять
# (под конец дня), а это число часов - подстраховка от дубля: воркфлоу
# гоняется каждые ~10 минут (см. .github/workflows/bot.yml), значит
# внутри целевого часа UTC этот шаг попытается сработать до 6 раз -
# троттлинг гарантирует, что реально уйдёт только первая попытка.
OPS_DIGEST_MIN_REPEAT_HOURS = float(os.environ.get("OPS_DIGEST_MIN_REPEAT_HOURS", "20"))
# Час UTC, начиная с которого дайджест считается "под конец дня" (см.
# ops_digest._is_end_of_day_window) - используется как ">= этого часа",
# а не "== этому часу ровно", чтобы задержка запуска воркфлоу (GitHub
# Actions не гарантирует cron минута-в-минуту) не пропустила окно
# целиком. 23 = последний час UTC-суток.
OPS_DIGEST_HOUR_UTC = int(os.environ.get("OPS_DIGEST_HOUR_UTC", "23"))

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
# Было 240 (раз в 10 дней) - слишком редко для формата, который держит
# вовлечённость аудитории. Раз в 2 дня.
TELEGRAM_AMA_INTERVAL_HOURS = float(os.environ.get("TELEGRAM_AMA_INTERVAL_HOURS", "48"))
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
# Мягкая ступень ПЕРЕД жёстким kill switch (см. risk_guard.py) - начиная
# с этого числа убытков подряд (но ДО достижения MAX_CONSECUTIVE_LOSSES,
# когда торговля просто остановится целиком) размер новых позиций
# домножается на BINANCE_FUTURES_SOFT_DERISK_MULTIPLIER. По умолчанию
# 2 и 0.5 - т.е. после 2 убытков подряд следующая сделка рискует не 1%,
# а 0.5% от баланса. Это снижает урон ДО того, как сработает жёсткий
# стоп, вместо того чтобы идти на полном риске до самого последнего
# момента. Должно быть < MAX_CONSECUTIVE_LOSSES, иначе бессмысленно
# (see risk_guard.get_risk_multiplier).
BINANCE_FUTURES_SOFT_DERISK_AFTER_LOSSES = int(os.environ.get("BINANCE_FUTURES_SOFT_DERISK_AFTER_LOSSES", "2"))
BINANCE_FUTURES_SOFT_DERISK_MULTIPLIER = float(os.environ.get("BINANCE_FUTURES_SOFT_DERISK_MULTIPLIER", "0.5"))

# Порог НЕВЫГОДНОЙ ставки фандинга (см. futures_signal_bridge.py) -
# доля, а не проценты (0.001 = 0.1% за 8ч интервал). Обычная ставка на
# Binance Futures обычно в пределах ±0.01%, выше 0.1% - уже признак
# сильного перекоса рынка в одну сторону. Если фандинг движется ПРОТИВ
# направления сигнала (лонг при высоком положительном фандинге, шорт
# при сильно отрицательном) сильнее этого порога - сигнал пропускается:
# стоимость удержания позиции может съесть значимую часть ожидаемой
# прибыли ещё до того, как сработает тейк/стоп. Проверка не блокирует
# сделки ПО направлению фандинга (лонг при отрицательном, шорт при
# положительном) - там фандинг наоборот платит нам.
BINANCE_FUTURES_MAX_UNFAVORABLE_FUNDING_RATE = float(
    os.environ.get("BINANCE_FUTURES_MAX_UNFAVORABLE_FUNDING_RATE", "0.001")
)

# Через сколько часов после срабатывания kill switch (дневной лимит
# убытка или серия убытков подряд, см. risk_guard.py) бот САМ его
# снимает - без участия человека. 0 (по умолчанию) = автосброс ВЫКЛЮЧЕН,
# нужен осознанный python3 risk_guard_cli.py reset, как и раньше - это
# сознательно КОНСЕРВАТИВНЫЙ дефолт (см. docstring risk_guard.py) - если
# включаете, выбирайте значение, при котором вы действительно готовы
# доверить боту решение "продолжать торговать" без вашего участия.
# При срабатывании автосброса старая серия убытков перестаёт
# учитываться (см. queue_manager.clear_kill_switch) - отсчёт начинается
# заново, а не "снял флаг, но тут же взвёл обратно на той же причине".
BINANCE_FUTURES_KILL_SWITCH_AUTO_RESET_HOURS = float(
    os.environ.get("BINANCE_FUTURES_KILL_SWITCH_AUTO_RESET_HOURS", "0")
)

# --- Частичный профит + перевод в безубыток + трейлинг-стоп (см.
# futures_position_monitor.py, докстринг про "A1" в плане развития) ---
# Сейчас: фиксированные SL/TP из уровней сигнала, ничего не меняется
# после входа до самого закрытия. Здесь - управление сделкой ПОСЛЕ
# входа: когда цена проходит BINANCE_FUTURES_PARTIAL_TP_TRIGGER_FRACTION
# пути от входа до тейка сигнала, часть позиции фиксируется по рынку, а
# стоп на остаток переводится в безубыток и дальше ведётся уже не
# фиксированным тейком, а трейлинг-стопом - чтобы поймать более крупное
# движение, если оно продолжится, вместо того чтобы просто ждать, дойдёт
# ли цена до исходного тейка целиком или развернётся обратно к стопу.
BINANCE_FUTURES_PARTIAL_TP_ENABLED = os.environ.get("BINANCE_FUTURES_PARTIAL_TP_ENABLED", "true").strip().lower() != "false"
# Доля пути от входа до тейка (0..1), при прохождении которой срабатывает
# частичный профит - 0.5 по умолчанию = ровно половина пути.
BINANCE_FUTURES_PARTIAL_TP_TRIGGER_FRACTION = float(os.environ.get("BINANCE_FUTURES_PARTIAL_TP_TRIGGER_FRACTION", "0.5"))
# Какая доля ПОЗИЦИИ (0..1) закрывается по рынку при срабатывании
# триггера выше - остаток ведётся трейлинг-стопом (см.
# BINANCE_FUTURES_TRAILING_CALLBACK_PCT ниже).
BINANCE_FUTURES_PARTIAL_TP_CLOSE_FRACTION = float(os.environ.get("BINANCE_FUTURES_PARTIAL_TP_CLOSE_FRACTION", "0.5"))
# Callback rate (в %) трейлинг-стопа, который ведёт остаток позиции
# после частичного профита - см. FuturesClient.place_trailing_stop_market.
# Binance допускает диапазон 0.1-5.0 для USD-M фьючерсов.
BINANCE_FUTURES_TRAILING_CALLBACK_PCT = float(os.environ.get("BINANCE_FUTURES_TRAILING_CALLBACK_PCT", "1.0"))

# --- A4: лимит на позиции в ОДНУ СТОРОНУ одновременно (см. risk_guard.py,
# докстринг модуля пункт 2) - защита от того, что max_open_positions сам
# по себе не мешает набрать, например, лонг по BTC+ETH+SOL одновременно:
# формально 3 разных слота, а по факту одна большая ставка на рынок
# вверх, а не независимая диверсификация. None = проверка выключена.
# Дефолт 2 при max_open_positions=3 - можно 2 лонга + 1 шорт (или
# наоборот), но не 3 в одну сторону.
_max_same_dir_env = os.environ.get("BINANCE_FUTURES_MAX_SAME_DIRECTION_POSITIONS", "2").strip()
BINANCE_FUTURES_MAX_SAME_DIRECTION_POSITIONS = int(_max_same_dir_env) if _max_same_dir_env else None

# --- A5: вето по funding rate перед входом в перпетуалку (см.
# futures_signal_bridge._funding_rate_veto) - если funding rate сильно
# ПРОТИВ направления сделки (лонг платит при высоком положительном
# funding, шорт платит при сильно отрицательном), вход обходится
# заметно дороже, чем показывает score сигнала. Приоритет ниже A1-A3
# (см. роадмап) - эффект на масштабе этого бота, скорее всего, небольшой,
# поэтому порог намеренно консервативный (ветирует только ЗАМЕТНЫЙ
# встречный funding, не любое ненулевое значение - funding почти всегда
# хоть немного ненулевой).
BINANCE_FUTURES_FUNDING_RATE_VETO_ENABLED = os.environ.get("BINANCE_FUTURES_FUNDING_RATE_VETO_ENABLED", "true").strip().lower() != "false"
# 0.0005 = 0.05% за 8ч (типичный "нормальный" funding редко превышает
# ~0.01-0.02% за 8ч - 0.05% уже заметно повышенный, характерный для
# перегретого рынка в одну сторону).
BINANCE_FUTURES_FUNDING_RATE_VETO_THRESHOLD = float(os.environ.get("BINANCE_FUTURES_FUNDING_RATE_VETO_THRESHOLD", "0.0005"))

# --- A2: ATR вместо фиксированного % отступа для стопа (см. роадмап) ---
# Сейчас (по умолчанию, USE_ATR_STOPS=false): invalidation (стоп) в
# scanner._build_signal/strategies.build_macd_signal/build_breakout_signal
# считается как "ближайший локальный экстремум (recent_high/recent_low
# за 20 свечей, либо уровень пробитого канала) + ОДИН И ТОТ ЖЕ фиксированный
# % буфер на все монеты сразу (0.3-0.5%)". Проблема: одна из самых частых
# причин, по которой стопы бьются "на шуме" - фиксированный % не
# учитывает, что у BTC и у мелкой альты совершенно разная нормальная
# внутридневная волатильность.
#
# Когда USE_ATR_STOPS=true: тот же локальный экстремум/уровень + буфер
# = ATR_STOP_MULTIPLIER * ATR(ATR_PERIOD) (см. strategies.calc_atr) -
# стоп становится шире на волатильных монетах и уже на спокойных, вместо
# одного процента на всех. Если ATR посчитать не удалось (свечей мало,
# см. calc_atr) - тихо используется СТАРАЯ формула с фиксированным % как
# запасной вариант, а не ошибка/пропуск сигнала.
#
# ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО (в отличие от A4/A5) - в отличие от них, это
# изменение самой формулы стопа для уже работающих стратегий, а не
# дополнительный независимый слой поверх. Роадмап явно требует бэктеста
# до/после, прежде чем включать на реальных деньгах (см. backtest.py
# --use-atr-stops) - не переключайте на true, не сравнив хотя бы
# aggregate_report до и после на одних и тех же исторических данных.
USE_ATR_STOPS = os.environ.get("USE_ATR_STOPS", "false").strip().lower() == "true"
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
ATR_STOP_MULTIPLIER = float(os.environ.get("ATR_STOP_MULTIPLIER", "1.5"))

# --- Автоматическое исполнение сигналов (см. futures_signal_bridge.py/
# futures_auto_trade.py) ---
# ОТДЕЛЬНЫЙ (и по умолчанию СТРОЖЕ) порог score от MIN_SIGNAL_SCORE_TO_PUBLISH
# (70) выше - "достаточно хорош, чтобы о нём написать пост" и "достаточно
# хорош, чтобы рискнуть на него реальными (пусть пока testnet) деньгами
# без подтверждения человека" - разные по цене ошибки решения, порог для
# второго должен быть заведомо не мягче первого.
#
# 90, а не 80 (как было изначально) - по факту накопленной статистики
# (см. outcome_tracker.get_accuracy_stats, срез "по качеству" в
# check_state.py): сигналы с score 70-89 ("Moderate", см.
# strategies._quality_from_score/scanner._score_and_quality) в среднем
# УБЫТОЧНЫ (win-rate ~33%, средний результат ~-0.4%) на выборке в
# несколько десятков закрытых сигналов - недостаточно данных, чтобы
# утверждать это математически строго, но достаточно, чтобы не рисковать
# реальными деньгами на этом диапазоне по умолчанию. 90+ ("Conservative") -
# единственный диапазон с явно положительным средним результатом
# (~+0.5%). Публикация постов (MIN_SIGNAL_SCORE_TO_PUBLISH=70) НЕ
# поднята вместе с этим порогом сознательно - ошибка в посте стоит
# дешевле ошибки в реальной сделке, у аудитории и так есть win-rate по
# качеству в каждом посте, чтобы самим решать, доверять ли Moderate-сигналу.
BINANCE_FUTURES_MIN_SIGNAL_SCORE = int(os.environ.get("BINANCE_FUTURES_MIN_SIGNAL_SCORE", "90"))

# --- Ребалансировка портфеля спотом к целевым весам Treasury Index
# (см. portfolio_rebalancer.py) - БЕЗ плеча, без ликвидации, ниже
# приоритет и ниже риск, чем автотрейдинг фьючерсов выше. Отдельные
# ключи от BINANCE_FUTURES_* - testnet.binance.vision (спот) и
# testnet.binancefuture.com (фьючерсы) две РАЗНЫЕ тестовые сети,
# ключи между ними не взаимозаменяемы.
BINANCE_SPOT_API_KEY = os.environ.get("BINANCE_SPOT_API_KEY", "")
BINANCE_SPOT_API_SECRET = os.environ.get("BINANCE_SPOT_API_SECRET", "")
BINANCE_SPOT_USE_TESTNET = os.environ.get("BINANCE_SPOT_USE_TESTNET", "true").strip().lower() != "false"
BINANCE_SPOT_RECV_WINDOW_MS = int(os.environ.get("BINANCE_SPOT_RECV_WINDOW_MS", "5000"))
# "По надобности", а не по расписанию (см. решение в обсуждении с
# пользователем) - portfolio_rebalancer.py проверяет отклонение от
# целевых весов на КАЖДОМ тике (дёшево - только баланс+цены, без
# ордеров), но реально торгует только если максимальное отклонение по
# какой-либо монете корзины превышает этот порог (в процентных пунктах
# от общей стоимости управляемого портфеля). Слишком низкий порог -
# комиссии съедают выгоду от частой мелкой ребалансировки, слишком
# высокий - портфель подолгу остаётся заметно перекошен относительно
# целевых весов.
PORTFOLIO_REBALANCE_DRIFT_THRESHOLD_PCT = float(os.environ.get("PORTFOLIO_REBALANCE_DRIFT_THRESHOLD_PCT", "5.0"))


def validate_config() -> list[str]:
    """Возвращает список незаполненных обязательных переменных."""
    required = {
        "FOLLOWUP_CHANNEL_USERNAME": FOLLOWUP_CHANNEL_USERNAME,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "BINANCE_SQUARE_API_KEY": BINANCE_SQUARE_API_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    return [name for name, value in required.items() if not value]