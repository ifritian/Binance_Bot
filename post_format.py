"""
Общие константы и сборка финального текста поста - используется всеми
генераторами (text_generator, opinion_generator, article_generator),
чтобы дисклеймер и структура были одинаковыми во всех форматах.
"""
import re

import random

import config
import signal_explainer
import signal_parser

# Фиксированная фраза дисклеймера - меняй только здесь.
DISCLAIMER = "Не финансовый совет."


# Реферальная ссылка Binance - добавляется только в "мнение" и "статью"
# (опционально, через assemble_post(..., include_referral=True)), НЕ в
# частые валютные сигналы/картинки - там посты выходят по 6-12 раз в
# день, и одна и та же ссылка в каждом выглядела бы как спам и могла бы
# триггернуть модерацию Square.
REFERRAL_LINK = "https://www.binance.com/register?ref=ES7YTYML"
REFERRAL_LINE = (f"Open a Binance account: {REFERRAL_LINK}" if config.SQUARE_TEMPLATE_LANG != "ru"
                 else f"Открыть аккаунт на Binance: {REFERRAL_LINK}")


# Короткие CTA-строки про выгоду/фичи именно площадки Binance Square -
# добавляются НЕ в каждый пост-сигнал (см. maybe_binance_cta ниже и
# config.BINANCE_CTA_PROBABILITY), а с вероятностью, чтобы не выглядеть
# спамом. В отличие от REFERRAL_LINE (та даёт конкретную реферальную
# ссылку) - это качественные фразы БЕЗ конкретных цифр по комиссиям (у
# бота нет доступа к актуальной официальной тарифной сетке Binance,
# см. также binance_promo_generator.py) - фиксированный список, не LLM.
# Раньше здесь были рекламные утверждения ("одни из самых низких комиссий
# на рынке") - их нечем подтвердить, и они звучали как реклама, поэтому
# оставлены только честные призывы к действию.
_BINANCE_CTA_LINES = [
    "Нажми на тикер, чтобы открыть график и цену прямо в ленте.",
    "Подпишись, чтобы утром не пропускать срез рынка.",
    "Не согласен? Напиши в комментариях, разберём вместе.",
]


def maybe_binance_cta() -> str | None:
    """Возвращает случайную CTA-строку (см. _BINANCE_CTA_LINES) с
    вероятностью config.BINANCE_CTA_PROBABILITY, либо None. Используется
    ТОЛЬКО в тексте, который уходит на Binance Square (main._do_publish) -
    Telegram/Bluesky получают свой собственный текст без этой строки,
    формат площадок теперь осознанно разный, а не одна и та же копия."""
    import random
    if random.random() >= config.BINANCE_CTA_PROBABILITY:
        return None
    return random.choice(_BINANCE_CTA_LINES)


# --- Топик-хэштеги (ТОЛЬКО Binance Square) ---
# Square различает два механизма дискаверабилити в тексте поста (см.
# официальное уведомление Binance про апгрейд API постинга): "token tag"
# ($TICKER - привязывает пост к конкретному активу и его подписчикам) и
# "topic hashtag" (#тег - шанс попасть на страницу трендовой темы). Оба
# парсятся сервером из голого текста (см. docstring binance_publisher.py),
# без отдельного структурированного поля в API.
#
# $CASHTAG уже был - text_generator.py явно просит LLM начинать хук с
# $TICKER (см. промпт там же). А вот #-хэштега в постах на Square не было
# ВООБЩЕ: signal_setup_lines/assemble_signal_post (сетап+дисклеймер,
# собран кодом) не содержит ни одного тега, и единственное место, где
# строился #{TICKER}, было build_bluesky_post - формат кросспоста в
# Bluesky, который на Square не публикуется. Ниже - именно недостающая
# половина: #-хэштеги для самого частого формата (сигнал, см.
# main._do_publish) и для еженедельной статьи.
_SQUARE_GENERAL_HASHTAGS = list(config.SQUARE_GENERAL_HASHTAGS)

# Тематический тег по стратегии сигнала - он описывает, ЧТО именно
# происходит на графике, а не "крипту вообще" (такие теги привязывают пост
# к узкой теме вместо общей свалки). Ключ - подстрока названия стратегии
# в нижнем регистре, первый подходящий выигрывает.
_STRATEGY_TOPIC_TAGS = (
    ("donchian", "#Breakout"),
    ("breakout", "#Breakout"),
    ("macd", "#MACD"),
    ("bollinger", "#RSI"),
    ("rsi", "#RSI"),
)


def strategy_topic_tag(strategy: str | None) -> str | None:
    """Тематический #-тег по названию стратегии сигнала (см.
    _STRATEGY_TOPIC_TAGS) или None, если стратегия не распознана."""
    if not strategy:
        return None
    lowered = str(strategy).lower()
    for needle, tag in _STRATEGY_TOPIC_TAGS:
        if needle in lowered:
            return tag
    return None


_TICKER_RE = re.compile(r"[A-Z0-9]{1,15}")


def ensure_cashtag(text: str, ticker: str | None, anywhere: bool = False) -> str:
    """Гарантирует, что в посте есть $TICKER - именно он привязывает пост
    к токену на Square. Раньше это только ПРОСИЛИ у LLM в промпте, и если
    модель забывала "$", пост уходил без привязки к монете.

    По умолчанию проверяется первый абзац (хук) - если там нет $TICKER,
    он добавляется в самое начало текста. anywhere=True - достаточно
    $TICKER в любом месте текста (для постов, где тикер уже есть в
    блоке с цифрами, например "Забрали профит!"). Регистр не важен,
    "$SOLANA" не считается за "$SOL". Если тикер пустой или выглядит
    странно - текст возвращается как есть."""
    if not ticker:
        return text
    symbol = str(ticker).strip().lstrip("$#").upper()
    if not _TICKER_RE.fullmatch(symbol):
        return text

    scope = text if anywhere else text.strip().split("\n\n")[0]
    if re.search(r"\$" + re.escape(symbol) + r"(?![A-Za-z0-9])", scope, re.IGNORECASE):
        return text
    return f"${symbol} {text.lstrip()}"


_TIER_LINE_PREFIXES = ("🔵", "🟡", "🔴")
_COIN_PCT_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}) ([+\-−]?\d+(?:\.\d+)?)%")


def cashtag_top_movers(text: str, n: int = 3) -> str:
    """Для Treasury Index на Square: в строках уровней (🔵/🟡/🔴) ставит $ перед
    n монетами с самым сильным движением - только они становятся кликабельными
    виджетами цены (весь список из ~15 тикеров с $ выглядел бы как спам)."""
    coins: dict[str, float] = {}
    for line in text.split("\n"):
        if not line.startswith(_TIER_LINE_PREFIXES):
            continue
        for m in _COIN_PCT_RE.finditer(line):
            try:
                coins.setdefault(m.group(1), float(m.group(2).replace("−", "-")))
            except ValueError:
                continue
    top = {t for t, _ in sorted(coins.items(), key=lambda kv: abs(kv[1]), reverse=True)[:n]}
    if not top:
        return text
    out = []
    for line in text.split("\n"):
        if line.startswith(_TIER_LINE_PREFIXES):
            line = _COIN_PCT_RE.sub(lambda m: (f"${m.group(1)} " if m.group(1) in top else f"{m.group(1)} ")
                                    + f"{m.group(2)}%", line)
        out.append(line)
    return "\n".join(out)


def square_hashtags_line(ticker: str | None, topic: str | None = None) -> str | None:
    """#{TICKER} + один тег по теме. topic - тематический тег, подходящий
    именно этому посту (см. strategy_topic_tag); если его нет - один
    ротирующийся общий тег (см. _SQUARE_GENERAL_HASHTAGS). Ротация общих -
    чтобы не постить один и тот же тег буквально в каждом посте. Возвращает
    None, если тикер неизвестен - строить #-хэштег не от чего."""
    import random
    if not ticker:
        return None
    second = topic or random.choice(_SQUARE_GENERAL_HASHTAGS)
    return f"#{ticker.upper()} {second}"


_SQUARE_ARTICLE_HASHTAGS_LINE = "#Crypto #CryptoNews #Bitcoin #Altcoins"


def square_article_hashtags_line() -> str:
    """Топик-хэштеги для еженедельной статьи (article_generator) - в
    отличие от square_hashtags_line, статья не про один тикер, а про
    сводку за неделю сразу по нескольким, поэтому вместо одного
    $CASHTAG/тикера - фиксированный набор широких тем."""
    return _SQUARE_ARTICLE_HASHTAGS_LINE


def square_general_hashtag_line() -> str:
    """Один ротирующийся общий тег БЕЗ $CASHTAG (см. _SQUARE_GENERAL_HASHTAGS) -
    для форматов, у которых просто нет конкретного тикера, к которому
    можно привязать $TICKER (промо-пост про саму площадку - см.
    try_publish_binance_promo, и пост-мнение по теме "market" - см.
    opinion_generator.THEMES). В отличие от square_hashtags_line(None),
    которая возвращает None именно потому, что вызывающий код там ЖДЁТ
    возможного отсутствия тикера как штатный случай (и раньше просто
    ничего не добавлял) - здесь тикера в принципе не предполагается, и
    полностью пропускать дискаверабилити тоже не за чем."""
    import random
    return random.choice(_SQUARE_GENERAL_HASHTAGS)


def site_line() -> tuple[str, str] | None:
    """(строка, url) со ссылкой на сайт проекта или None. Только для Bluesky/Telegram."""
    url = (config.SITE_URL or "").strip()
    if not url:
        return None
    lead = "Full record of every call:" if config.SQUARE_TEMPLATE_LANG != "ru" else "Все результаты:"
    return f"{lead} {url}", url


def telegram_channel_line() -> str | None:
    """Строка со ссылкой на наш Telegram-канал (config.TELEGRAM_PUBLISH_CHANNEL) -
    добавляется в посты на Binance Square, чтобы читатели могли перейти
    в Telegram. Возвращает None, если канал не настроен ИЛИ настроен как
    приватный (числовой chat_id, например "-1001234567890") - у приватных
    каналов нет публичной t.me-ссылки, вести туда некуда, и молча
    подставлять битую ссылку не нужно.

    Публичный канал должен быть задан в конфиге с "@" в начале
    (например TELEGRAM_PUBLISH_CHANNEL=@my_channel)."""
    channel = config.TELEGRAM_PUBLISH_CHANNEL
    if not channel or not str(channel).startswith("@"):
        return None
    username = str(channel).lstrip("@")
    if config.SQUARE_TEMPLATE_LANG != "ru":
        return f"📣 More posts in our Telegram: https://t.me/{username}"
    return f"📣 Подробнее и другие посты - в нашем Telegram: https://t.me/{username}"


def assemble_post(hook: str, include_referral: bool = False) -> str:
    """Хук + пустая строка + дисклеймер (+ опционально реферальная
    ссылка отдельной строкой) - структура фиксирована кодом, не
    оставлена на волю LLM."""
    parts = [hook.strip(), DISCLAIMER]
    if include_referral:
        parts.append(REFERRAL_LINE)
    return "\n\n".join(parts)


def _signal_price(value) -> float:
    """Парсит числовое поле сигнала (entry_low/entry_high/invalidation/
    target - RsiSignal хранит их строками, см. signal_parser.py) в
    float. Та же логика, что и outcome_tracker._to_float (запятая как
    разделитель тысяч, не десятичный - формат чисел из scanner.py) -
    продублирована здесь, а не импортирована из outcome_tracker, чтобы
    не тащить в post_format его сетевые зависимости (requests и т.п.)
    ради одной строчки парсинга."""
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def signal_risk_reward_line(signal) -> str | None:
    """Строка "R:R 1:X.X" - соотношение потенциальной прибыли к риску
    (см. C2 в роадмапе: "дёшево и сразу видимый эффект в контенте").
    Сам расчёт - см. signal_parser.calc_risk_reward_ratio (единый
    источник правды, тот же самый, что использует scanner.py для
    фильтра минимального R:R - цифра в посте гарантированно совпадает
    с той, по которой сигнал вообще прошёл или не прошёл в очередь).

    Возвращает None, если риск (|entry - stop|) равен нулю или числа не
    распознались - показывать "R:R 1:inf" или падать тут не за чем,
    просто пропускаем строку молча (как и остальные *_line-хелперы в
    этом модуле - см. square_hashtags_line)."""
    ratio = signal_parser.calc_risk_reward_ratio(signal)
    if ratio is None:
        return None
    return f"R:R 1:{ratio:.1f}"


def signal_setup_lines(signal) -> list:
    """Строки с сетапом (направление/вход/стоп/тейк/RSI/score/R:R) -
    вынесено отдельно от assemble_signal_post, чтобы переиспользовать в
    build_bluesky_thread_signal (там этот блок идёт ОТДЕЛЬНЫМ постом
    треда, а не частью одного текста) без дублирования кода/риска
    разъехаться цифрами между Square-версией и Bluesky-версией."""
    direction_emoji = "🟢" if "лонг" in signal.direction.lower() else "🔴"
    lines = [
        f"{direction_emoji} {signal.direction} | {signal.strategy}",
        f"Вход: {signal.entry_low} - {signal.entry_high}",
        f"Стоп: {signal.invalidation}",
        f"Тейк: {signal.target}",
        f"RSI: {signal.rsi_now} | Score: {signal.score}/100",
    ]
    rr_line = signal_risk_reward_line(signal)
    if rr_line:
        lines.append(rr_line)
    return lines


def assemble_signal_post(hook: str, signal) -> str:
    """Хук (от LLM) + блок сетапа (вход/стоп/тейк/RSI/score - собран
    КОДОМ, не LLM, чтобы цифры были гарантированно точными) + дисклеймер.

    signal - RsiSignal из signal_parser.
    """
    setup_block = "\n".join(signal_setup_lines(signal))
    return f"{hook.strip()}\n\n{setup_block}\n\n{DISCLAIMER}"


def _signal_levels_lines(signal) -> list:
    """Две строки с уровнями без жаргона: направление и вход, затем стоп и
    тейк. Без RSI, score и названия стратегии - обычному читателю они
    ничего не говорят."""
    is_long = signal_parser.is_long_direction(signal.direction)
    emoji = "🟢" if is_long else "🔴"
    word = "Лонг" if is_long else "Шорт"
    entry = (
        f"{signal.entry_low}" if str(signal.entry_low) == str(signal.entry_high)
        else f"{signal.entry_low} - {signal.entry_high}"
    )
    return [
        f"{emoji} {word} | Вход: {entry}",
        f"Стоп: {signal.invalidation} | Тейк: {signal.target}",
    ]


def assemble_signal_post_short(hook: str, signal) -> str:
    """Короткий формат сигнала (основной): хук + две строки уровней +
    дисклеймер. Числа собраны КОДОМ, а не LLM."""
    block = "\n".join(_signal_levels_lines(signal))
    return f"{hook.strip()}\n\n{block}\n\n{DISCLAIMER}"


def assemble_signal_post_explained(hook: str, signal, rng=None) -> str:
    """Тот же короткий формат + блок "Простыми словами" (см.
    signal_explainer) между уровнями и дисклеймером. Выходит редко - раз в
    SIGNAL_EXPLAIN_INTERVAL_HOURS, чтобы лента не превращалась в лекции."""
    block = "\n".join(_signal_levels_lines(signal))
    explanation = signal_explainer.explain_signal(signal, rng=rng)
    return f"{hook.strip()}\n\n{block}\n\n💡 Простыми словами: {explanation}\n\n{DISCLAIMER}"


def assemble_index_management_post(hook: str, signal, tier_label: str, weight: float) -> str:
    """Как assemble_signal_post, но в терминах управления ДОЛЕЙ в
    портфеле (Treasury Index), а не разовой сделки - без слов
    Вход/Стоп/Тейк. Числа те же самые (та же формула RSI/Bollinger, что
    и у обычного сигнала - см. scanner.py), просто названы в контексте
    докупки/частичной фиксации доли, а не открытия/закрытия позиции.

    signal - RsiSignal (из index_signal_scanner.py). tier_label/weight -
    из treasury_index.find_coin_by_ticker(signal.ticker).
    """
    is_buy = signal_parser.is_long_direction(signal.direction)
    emoji = "🟢" if is_buy else "🔴"
    action = "Докупка доли" if is_buy else "Частичная фиксация доли"
    range_label = "Диапазон для докупки" if is_buy else "Диапазон для фиксации"

    lines = [
        f"{tier_label} | вес в индексе: {weight:g}%",
        f"{signal.strategy} | RSI: {signal.rsi_now}",
        "",
        f"Действие: {emoji} {action}",
        f"{range_label}: {signal.entry_low} - {signal.entry_high}",
        f"Ориентир: {signal.target}",
        f"Пересмотреть тезис, если цена уйдёт за: {signal.invalidation}",
    ]
    block = "\n".join(lines)

    return f"{hook.strip()}\n\n{block}\n\n{DISCLAIMER}"


# --- Формат поста для Bluesky (кросспостинг) ---
# Bluesky - короткая соцсеть с жёстким лимитом в 300 символов на пост
# (жёстче, чем было бы у Threads - 500), поэтому туда нельзя просто
# отправлять тот же текст, что на Binance Square/Telegram (там лимиты
# намного мягче, посты длиннее).
# Как и у REFERRAL_LINE (см. выше - туда реферальная ссылка сознательно
# НЕ добавляется в частые валютные посты, чтобы не выглядеть спамом на
# площадке, где сам бот и так публикуется через официальный API), в
# Bluesky ссылки на Binance и на свой Telegram-канал добавляются ВСЕГДА -
# это отдельная площадка, которая иначе вообще не узнает, где смотреть
# остальные посты и как открыть аккаунт.
BLUESKY_CHAR_LIMIT = 300
_BLUESKY_TRUNCATION_SUFFIX = "…"


def build_bluesky_post(text: str, ticker: str | None = None) -> tuple[str, list]:
    """Адаптирует уже готовый текст поста (собранный для Binance Square/
    Telegram - hook + сетап/данные + дисклеймер) под формат Bluesky:
    добавляет cashtag тикера (если известен) для дискаверабилити и
    ссылки на Binance и Telegram-канал, затем жёстко обрезает ВСЁ до
    300 символов - в первую очередь обрезается основной текст, а не
    ссылки, так как весь смысл кросспоста в Bluesky - привести читателя
    в Telegram/на Binance.

    Возвращает (текст, link_facets) - link_facets - список пар
    (подстрока, url) для bluesky_publisher._byte_facets, чтобы ссылки
    в итоговом посте были кликабельными (Bluesky, в отличие от Threads/
    Square, не парсит "голый" URL в тексте сам - нужны явные facets).

    Ничего не меняет в исходном text - используется только здесь, отдельно
    от текста, который реально уходит на Binance Square/Telegram."""
    links, link_facets = [], []
    site = site_line()
    use_ref = site is None or random.random() < config.REFERRAL_LINK_SHARE   # ссылки чередуются: Binance / сайт
    if use_ref:
        links.append(REFERRAL_LINE)
        link_facets.append((REFERRAL_LINK, REFERRAL_LINK))
    else:
        links.append(site[0])
        link_facets.append((site[1], site[1]))

    tg_line = telegram_channel_line()
    tg_url = None
    if tg_line:
        links.append(tg_line)
        # Сама ссылка (без окружающего текста эмодзи/фразы) - именно она
        # должна стать facet'ом, "голый" https://t.me/... адрес всегда
        # присутствует в tg_line как есть (см. telegram_channel_line).
        tg_url = tg_line.split(" ")[-1]
        link_facets.append((tg_url, tg_url))

    links_block = "\n\n".join(links)

    hashtag = f"#{ticker.upper()}" if ticker else ""

    # Резервируем место под ссылки (и тег, если есть) + разделители
    # между блоками ("\n\n" x количество склеек ниже).
    reserved = len(links_block) + (len(hashtag) + 2 if hashtag else 0) + 2
    max_body_len = max(BLUESKY_CHAR_LIMIT - reserved, 20)

    body = text.strip()
    if len(body) > max_body_len:
        cut = body[: max_body_len - len(_BLUESKY_TRUNCATION_SUFFIX)].rstrip()
        # лучше закончить на границе строки, чем посреди предложения
        nl = cut.rfind("\n")
        if nl >= len(cut) // 2:
            body = cut[:nl].rstrip()
        else:
            body = cut + _BLUESKY_TRUNCATION_SUFFIX

    parts = [body]
    if hashtag:
        parts.append(hashtag)
    parts.append(links_block)
    result = "\n\n".join(p for p in parts if p)

    # Финальная подстраховка на случай, если расчёт места выше всё же
    # не сошёлся (например, из-за очень длинного тикера) - лучше грубо
    # обрезать весь результат, чем упасть при публикации в Bluesky.
    # Обрезаем только если укороченный результат всё ещё содержит обе
    # ссылки целиком - иначе facets будут указывать мимо текста.
    if len(result) > BLUESKY_CHAR_LIMIT:
        result = result[: BLUESKY_CHAR_LIMIT - len(_BLUESKY_TRUNCATION_SUFFIX)].rstrip()
        result += _BLUESKY_TRUNCATION_SUFFIX
        # Ссылка, которую обрезка задела наполовину, битая - для неё
        # facet построить нельзя, оставляем как есть (bluesky_publisher
        # просто пропустит facets, подстрока которых не нашлась целиком).

    return result, link_facets


# --- Формат "Тред-разбор сильных сетапов" (Bluesky) ---
# Не каждый сигнал заслуживает трёхпостового треда - это отдельный,
# "событийный" формат для реально сильных сетапов (высокий score), а не
# стандартная подача. Если делать тред из каждого сигнала - пропадает
# сам эффект "события", ради которого он и нужен (см. main.py,
# _crosspost_to_bluesky - там решается, какой из двух форматов взять).
BLUESKY_THREAD_MIN_SCORE = 85


def is_strong_setup(signal) -> bool:
    """True, если score сигнала достаточно высок для формата
    "Тред-разбор" (см. BLUESKY_THREAD_MIN_SCORE) - порог заметно выше
    общего MIN_SIGNAL_SCORE_TO_PUBLISH (70), тред должен быть редким
    исключением, а не обычным способом подачи."""
    try:
        return int(str(signal.score).strip()) >= BLUESKY_THREAD_MIN_SCORE
    except (TypeError, ValueError):
        return False


def build_bluesky_thread_signal(hook: str, signal) -> list:
    """Собирает 3 поста для Bluesky-треда сильного сетапа:
    1) интрига - тот же хук, что ушёл на Square/Telegram (validator уже
       гарантирует отсутствие в нём чисел - это первый пост треда,
       цифры намеренно приберегаются для второго);
    2) сетап - вход/стоп/тейк/RSI/score(/R:R) - те же гарантированно
       точные цифры, что и в assemble_signal_post (см. signal_setup_lines) -
       код-блок, не LLM, риска расхождения с Square-версией нет;
    3) вывод - дисклеймер + ссылки на Binance/Telegram (с facets, чтобы
       быть кликабельными - см. bluesky_publisher._byte_facets).

    Возвращает список из 3 элементов для bluesky_publisher.publish_thread
    (первые два - просто строки, третий - пара (текст, link_facets)).
    Каждый пост короткий по построению (хук уже проверен на длину/лимит
    Square, сетап - 5-6 коротких строк, вывод - дисклеймер+2 ссылки) -
    отдельная обрезка под 300 символов не требуется ни одному из трёх."""
    post1 = hook.strip()
    post2 = "\n".join(signal_setup_lines(signal))

    links = [REFERRAL_LINE]
    link_facets = [(REFERRAL_LINK, REFERRAL_LINK)]
    tg_line = telegram_channel_line()
    if tg_line:
        links.append(tg_line)
        tg_url = tg_line.split(" ")[-1]
        link_facets.append((tg_url, tg_url))
    post3 = f"{DISCLAIMER}\n\n" + "\n\n".join(links)

    return [post1, post2, (post3, link_facets)]


# --- Формат "Тизер-график" (Bluesky) ---
# Альтернативный, минималистичный стиль подачи ОБЫЧНОГО (не "сильного")
# сигнала: вместо хука+сетапа - просто картинка почти без слов. Чистый
# curiosity gap - показываем ЧТО-ТО (график), но не говорим ничего,
# пока человек не перейдёт в Telegram за разбором. Используется НЕ
# каждый раз (см. main.config.BLUESKY_TEASER_PROBABILITY) - если бы
# каждый обычный сигнал шёл тизером, разбор в Telegram обесценился бы
# сам по себе (незачем переходить, если тизеры никогда не раскрываются
# отдельным полным постом).
def build_bluesky_teaser(ticker: str | None = None) -> tuple[str, list]:
    """Возвращает (текст, link_facets) - картинка прикладывается
    ОТДЕЛЬНО вызывающим кодом (main._crosspost_to_bluesky), как и в
    остальных форматах."""
    cashtag_line = f"${ticker.upper()} 👀" if ticker else "👀"

    tg_line = telegram_channel_line()
    if tg_line:
        tg_url = tg_line.split(" ")[-1]
        lead = "Full breakdown on Telegram:" if config.SQUARE_TEMPLATE_LANG != "ru" else "Разбор - в Telegram:"
        text = f"{cashtag_line}\n\n{lead} {tg_url}"
        link_facets = [(tg_url, tg_url)]
    else:
        text = f"{cashtag_line}\n\n{REFERRAL_LINE}"
        link_facets = [(REFERRAL_LINK, REFERRAL_LINK)]

    return text, link_facets


# --- Форматы "Win-reveal" и "До/После" (Bluesky) ---
# Оба строятся из ОДНОГО И ТОГО ЖЕ closed-record (см. outcome_tracker.
# check_open_outcomes -> "closed_records"), но это РАЗНЫЕ посты с разной
# ролью:
# - build_bluesky_outcome_reply - фактический итог РЕПЛАЕМ в тот же тред,
#   где был вход (см. record["bluesky_ref"]) - публикуется на ЛЮБОЙ исход
#   (win/loss/timeout), это "закрытие" открытой петли, начатой входным
#   постом (см. main._post_outcome_updates_to_bluesky).
# - build_bluesky_win_reveal - отдельный, самостоятельный "победный" пост
#   (не реплай) - публикуется ТОЛЬКО на result == "win", специально для
#   ленты (не все, кто увидит его, видели исходный пост про вход).

_RESULT_LABELS = {
    "win": "🎯 Цель достигнута",
    "loss": "🛑 Сработал стоп",
    "timeout": "⌛ Тайм-аут (не дошло ни до тейка, ни до стопа)",
}


def build_bluesky_outcome_reply(record: dict) -> tuple[str, list]:
    """Короткий фактический итог сделки для реплая в исходный тред.
    Не претендует на анализ причин - только сухие цифры (result уже
    вычислен outcome_tracker.check_open_outcomes по реальным свечам)."""
    label = _RESULT_LABELS.get(record["result"], record["result"])
    pnl = record["pnl_pct"]
    pnl_str = f"{pnl:+.2f}%"

    lines = [
        f"{label}",
        f"${record['ticker']}: вход {record['entry']:g} → выход {record['exit_price']:g} ({pnl_str})",
    ]
    text = "\n".join(lines)
    return text, []


def build_bluesky_win_reveal(record: dict) -> tuple[str, list]:
    """Отдельный "победный" пост - публикуется только для result == "win".
    В отличие от build_bluesky_outcome_reply (сухой итог реплаем в тред),
    этот пост самостоятельный и рассчитан на ленту - со ссылками, чтобы
    случайный зритель, который НЕ видел исходный вход, тоже мог дойти до
    Telegram/Binance."""
    pnl_str = f"+{record['pnl_pct']:.2f}%"
    direction_ru = "шорт" if record["direction"] == "short" else "лонг"

    links = [REFERRAL_LINE]
    link_facets = [(REFERRAL_LINK, REFERRAL_LINK)]
    tg_line = telegram_channel_line()
    if tg_line:
        links.append(tg_line)
        tg_url = tg_line.split(" ")[-1]
        link_facets.append((tg_url, tg_url))
    links_block = "\n\n".join(links)

    text = (
        f"✅ ${record['ticker']} ({direction_ru}, {record['strategy']}) - цель достигнута, {pnl_str}\n\n"
        f"Разборы сетапов и вход/стоп/тейк по каждому - здесь:\n\n{links_block}"
    )
    return text, link_facets


# --- Формат "Забрали профит!" (ТОЛЬКО Binance Square) ---
# Реакция на сигнал, закрывшийся в плюс (result == "win", см.
# outcome_tracker.check_open_outcomes) - см. win_celebration_generator.py
# за системным промптом/генерацией хука. Как и у assemble_signal_post -
# хук (эмоция, от LLM) и фактический блок цифр (вход/выход/результат,
# собран КОДОМ из closed-record) разделены: LLM физически не может
# исказить результат, потому что не пишет ни одного числа сама -
# win_celebration_generator.validate_win_celebration_hook отбраковывает
# хук, если в нём вообще есть цифры.


def win_celebration_lines(record: dict) -> list:
    """Строки с фактическим результатом сделки - вынесено отдельно от
    assemble_win_celebration_post по той же причине, что и
    signal_setup_lines от assemble_signal_post (переиспользование без
    риска разъехаться цифрами, если формат когда-нибудь тоже станет
    многосоставным, как Bluesky-тред)."""
    direction_emoji = "🔴" if record["direction"] == "short" else "🟢"
    direction_ru = "шорт" if record["direction"] == "short" else "лонг"
    pnl_str = f"{record['pnl_pct']:+.2f}%"
    return [
        f"{direction_emoji} ${record['ticker']} ({direction_ru}, {record['strategy']})",
        f"Вход: {record['entry']:g} -> Выход: {record['exit_price']:g}",
        f"Результат: {pnl_str} за {record['hours_to_close']:g}ч",
    ]


def win_context_line(stats: dict | None, days: int = 7) -> str:
    """Честный контекст к посту о выигрыше: общий результат за период
    (win-rate и средний pnl по ВСЕМ закрытым сделкам, не только победам).
    Пустая строка, если данных нет."""
    overall = (stats or {}).get("overall") or {}
    count = overall.get("count") or 0
    if not count or overall.get("win_rate") is None or overall.get("avg_pnl_pct") is None:
        return ""
    return (
        f"Честно, за {days} дн.: закрыто {count}, в плюс {overall['win_rate']:g}%, "
        f"средний итог {overall['avg_pnl_pct']:+.2f}% на сделку."
    )


def assemble_win_celebration_post(hook: str, record: dict, context_line: str = "") -> str:
    """Хук (эмоция, от LLM, БЕЗ единой цифры) + блок результата (собран
    кодом из closed-record, см. win_celebration_lines) + дисклеймер.
    record - один элемент closed_records из outcome_tracker.check_open_outcomes
    (result == "win")."""
    block = "\n".join(win_celebration_lines(record))
    if context_line:
        block = f"{block}\n\n{context_line}"
    return f"{hook.strip()}\n\n{block}\n\n{DISCLAIMER}"


# --- Авторские голоса - для разнообразия постов ---
# Раньше это были просто варианты ТОНА (вопрос/утверждение/сравнение/
# шутка) - одна и та же безликая позиция, произнесённая по-разному.
# Теперь каждый режим - узнаваемая ЛИЧНОСТЬ со своим углом зрения на
# рынок и своей речевой особенностью (не путать с ИИ-клише из
# voice_guidelines.py - это придуманные, ИНДИВИДУАЛЬНЫЕ тики конкретного
# голоса, которые как раз и делают его узнаваемым человеком, а не
# наоборот). Инструкция добавляется к системному промпту LLM - сама
# ротация (какой голос выбрать сейчас) реализована в pick_hook_mode
# ниже, избегаем повтора последнего использованного.

HOOK_MODES: dict[str, str] = {
    "technician": (
        "Голос: Наблюдатель-технарь. Смотришь на график внимательно и "
        "говоришь, что он реально показывает, простыми словами, без "
        "названий индикаторов. Твоя особенность - замечать, когда картинка "
        "слишком чистая, чтобы быть случайной, или, наоборот, разваливается "
        "на глазах. Коротко и по делу. Вопрос в конце - только если он "
        "конкретный (отскок или пробой?)."
    ),
    "skeptic": (
        "Голос: Скептик. Сначала признаёшь очевидное, потом добавляешь "
        "оговорку, которую большинство упускает. Не паникуешь и не "
        "обольщаешься, слегка ироничен. Можно закончить честным сомнением "
        "или конкретным вопросом с выбором из двух вариантов."
    ),
    "storyteller": (
        "Голос: Наблюдатель за толпой. Говоришь о том, что делает "
        "большинство, и что на самом деле происходит с ценой ('все ждут X, "
        "а цена делает Y'). Без метафор и сравнений 'будто/словно' - только "
        "прямое наблюдение. Можно закончить вопросом, который задел бы "
        "читателя за живое."
    ),
    "pragmatist": (
        "Голос: Прагматик риска. Тебя интересует не куда пойдёт цена, а "
        "что будет, если ты ошибёшься. Мысленно прикинул худший сценарий, "
        "прежде чем говорить об оптимистичном. Суховатый тон, без прикрас. "
        "Заканчивай там, где мысль закончилась, или одним практичным "
        "вопросом ('где ваш стоп?')."
    ),
}


HOOK_MODES_EN: dict[str, str] = {
    "technician": (
        "Voice: the chart watcher. You look at the chart closely and say what it "
        "actually shows, in plain words, no indicator names. Your quirk: you notice "
        "when a picture is too clean to be random, or when it is falling apart in "
        "front of you. Short and to the point. End with a question only if it is "
        "specific (bounce or breakdown?)."
    ),
    "skeptic": (
        "Voice: the skeptic. First admit the obvious, then add the caveat most people "
        "miss. You neither panic nor get carried away, slightly ironic. You can end "
        "with an honest doubt or a specific either/or question."
    ),
    "storyteller": (
        "Voice: the crowd watcher. You talk about what most people are doing and what "
        "price is actually doing ('everyone waits for X, price does Y'). No similes "
        "or metaphors, only direct observation. You can end with a question that "
        "hits close to home."
    ),
    "pragmatist": (
        "Voice: the risk pragmatist. You care less where price goes than what happens "
        "if you are wrong. You think through the worst case before the optimistic one. "
        "Dry tone, no decoration. Stop where the thought ends, or end with one "
        "practical question ('where is your stop?')."
    ),
}

DISCLAIMER_EN = "Not financial advice."


def hook_mode_text(mode: str) -> str:
    """Описание голоса на языке Square (config.SQUARE_TEMPLATE_LANG)."""
    return (HOOK_MODES if config.SQUARE_TEMPLATE_LANG == "ru" else HOOK_MODES_EN)[mode]


def pick_hook_mode(last_mode: str | None) -> str:
    """Выбирает голос, отличный от последнего использованного, чтобы
    посты не звучали одним и тем же человеком раз за разом."""
    import random

    modes = list(HOOK_MODES.keys())
    if last_mode in modes and len(modes) > 1:
        modes = [m for m in modes if m != last_mode]
    return random.choice(modes)