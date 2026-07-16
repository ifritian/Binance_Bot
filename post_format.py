"""
Общие константы и сборка финального текста поста - используется всеми
генераторами (text_generator, opinion_generator, article_generator),
чтобы дисклеймер и структура были одинаковыми во всех форматах.
"""
import config

# Фиксированная фраза дисклеймера - меняй только здесь.
DISCLAIMER = "Информационный пост, не финансовая рекомендация."


# Реферальная ссылка Binance - добавляется только в "мнение" и "статью"
# (опционально, через assemble_post(..., include_referral=True)), НЕ в
# частые валютные сигналы/картинки - там посты выходят по 6-12 раз в
# день, и одна и та же ссылка в каждом выглядела бы как спам и могла бы
# триггернуть модерацию Square.
REFERRAL_LINK = "https://www.binance.com/register?ref=ES7YTYML"
REFERRAL_LINE = f"Открыть аккаунт на Binance: {REFERRAL_LINK}"


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
    return f"📣 Подробнее и другие посты - в нашем Telegram: https://t.me/{username}"


def assemble_post(hook: str, include_referral: bool = False) -> str:
    """Хук + пустая строка + дисклеймер (+ опционально реферальная
    ссылка отдельной строкой) - структура фиксирована кодом, не
    оставлена на волю LLM."""
    parts = [hook.strip(), DISCLAIMER]
    if include_referral:
        parts.append(REFERRAL_LINE)
    return "\n\n".join(parts)


def signal_setup_lines(signal) -> list:
    """Строки с сетапом (направление/вход/стоп/тейк/RSI/score) - вынесено
    отдельно от assemble_signal_post, чтобы переиспользовать в
    build_bluesky_thread_signal (там этот блок идёт ОТДЕЛЬНЫМ постом
    треда, а не частью одного текста) без дублирования кода/риска
    разъехаться цифрами между Square-версией и Bluesky-версией."""
    direction_emoji = "🟢" if "лонг" in signal.direction.lower() else "🔴"
    return [
        f"{direction_emoji} {signal.direction} | {signal.strategy}",
        f"Вход: {signal.entry_low} - {signal.entry_high}",
        f"Стоп: {signal.invalidation}",
        f"Тейк: {signal.target}",
        f"RSI: {signal.rsi_now} | Score: {signal.score}/100",
    ]


def assemble_signal_post(hook: str, signal) -> str:
    """Хук (от LLM) + блок сетапа (вход/стоп/тейк/RSI/score - собран
    КОДОМ, не LLM, чтобы цифры были гарантированно точными) + дисклеймер.

    signal - RsiSignal из signal_parser.
    """
    setup_block = "\n".join(signal_setup_lines(signal))
    return f"{hook.strip()}\n\n{setup_block}\n\n{DISCLAIMER}"


def assemble_index_management_post(hook: str, signal, tier_label: str, weight: float) -> str:
    """Как assemble_signal_post, но в терминах управления ДОЛЕЙ в
    портфеле (Treasury Index), а не разовой сделки - без слов
    Вход/Стоп/Тейк. Числа те же самые (та же формула RSI/Bollinger, что
    и у обычного сигнала - см. scanner.py), просто названы в контексте
    докупки/частичной фиксации доли, а не открытия/закрытия позиции.

    signal - RsiSignal (из index_signal_scanner.py). tier_label/weight -
    из treasury_index.find_coin_by_ticker(signal.ticker).
    """
    is_buy = "перепрод" in signal.direction.lower()
    emoji = "🟢" if is_buy else "🔴"
    action = "Докупка доли" if is_buy else "Частичная фиксация доли"
    range_label = "Диапазон для докупки" if is_buy else "Диапазон для фиксации"
    rsi_state = "перепроданность" if is_buy else "перекупленность"

    lines = [
        f"{tier_label} | вес в индексе: {weight:g}%",
        f"RSI: {signal.rsi_now} ({rsi_state})",
        "",
        f"Действие: {emoji} {action}",
        f"{range_label}: {signal.entry_low} - {signal.entry_high}",
        f"Ориентир возврата к среднему: {signal.target}",
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
    links = [REFERRAL_LINE]
    link_facets = [(REFERRAL_LINK, REFERRAL_LINK)]

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
        body = body[: max_body_len - len(_BLUESKY_TRUNCATION_SUFFIX)].rstrip()
        body += _BLUESKY_TRUNCATION_SUFFIX

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
    2) сетап - вход/стоп/тейк/RSI/score, те же гарантированно точные
       цифры, что и в assemble_signal_post (см. signal_setup_lines) -
       код-блок, не LLM, риска расхождения с Square-версией нет;
    3) вывод - дисклеймер + ссылки на Binance/Telegram (с facets, чтобы
       быть кликабельными - см. bluesky_publisher._byte_facets).

    Возвращает список из 3 элементов для bluesky_publisher.publish_thread
    (первые два - просто строки, третий - пара (текст, link_facets)).
    Каждый пост короткий по построению (хук уже проверен на длину/лимит
    Square, сетап - 5 коротких строк, вывод - дисклеймер+2 ссылки) -
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
        text = f"{cashtag_line}\n\nРазбор - в Telegram: {tg_url}"
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
        "Голос: Технарь-паттерновик. Смотришь на рынок через механику "
        "графика - RSI, полосы, объём - и говоришь на этом языке, но "
        "живо, не как учебник. Тебя интересует, ЧТО ИМЕННО показывает "
        "график, а не эмоция вокруг движения. Твоя речевая особенность - "
        "подмечать, когда сетап 'слишком чистый, чтобы быть случайностью' "
        "или, наоборот, разваливается на глазах. Без вопроса в конце."
    ),
    "skeptic": (
        "Голос: Скептик-макро. Мысленно проверяешь любое движение на "
        "прочность - 'а если рынок в целом развернётся, что будет с "
        "этим конкретно'. Твоя речевая особенность - сначала признать "
        "очевидное, а потом добавить оговорку, которую многие упускают. "
        "Не паникуёшь и не обольщаешься, слегка ироничен."
    ),
    "storyteller": (
        "Голос: Рассказчик. Объясняешь движение рынка через живое "
        "сравнение из повседневной жизни, а не абстрактно - находишь "
        "неожиданный образ, который делает голую цифру осязаемой. "
        "Можно с риторическим вопросом в конце, если он реально к месту."
    ),
    "pragmatist": (
        "Голос: Прагматик риска. Тебя не интересует, куда пойдёт цена - "
        "тебя интересует, что будет, если ты ошибёшься. Твоя речевая "
        "особенность - мысленно уже прикинул худший сценарий, прежде чем "
        "говорить об оптимистичном. Суховатый тон, без прикрас, без "
        "вопроса в конце."
    ),
}


def pick_hook_mode(last_mode: str | None) -> str:
    """Выбирает голос, отличный от последнего использованного, чтобы
    посты не звучали одним и тем же человеком раз за разом."""
    import random

    modes = list(HOOK_MODES.keys())
    if last_mode in modes and len(modes) > 1:
        modes = [m for m in modes if m != last_mode]
    return random.choice(modes)