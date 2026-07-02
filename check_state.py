"""
check_state.py - диагностика без побочных эффектов.

Показывает текущее состояние bot_state.db: что лежит в очереди на
публикацию, сколько осталось до каждого окна (валюта/мнение/статья),
и offset Telegram. НЕ публикует, НЕ ходит в Telegram/Binance API -
только читает локальную базу и config, чтобы можно было предсказать
поведение бота на следующем тике без угадывания.

Запуск: python check_state.py
"""
import config
import outcome_tracker
import queue_manager


def fmt_seconds(s: float) -> str:
    if s == float("inf"):
        return "ещё не публиковал(ось) ни разу"
    if s < 0:
        return f"уже наступило ({-s/3600:.1f}ч назад)"
    h = s / 3600
    return f"{h:.1f}ч"


def report_window(post_type: str, interval_hours: float) -> None:
    elapsed = queue_manager.seconds_since_last_post(post_type)
    jitter = queue_manager.get_jitter_seconds(post_type)
    min_seconds = interval_hours * 3600 + jitter
    remaining = min_seconds - elapsed

    print(f"\n[{post_type}]")
    print(f"  интервал: {interval_hours}ч, джиттер сейчас: {jitter/60:+.1f} мин")
    if elapsed == float("inf"):
        print("  с момента последней публикации: ещё не публиковал(ось)")
        print("  -> окно ОТКРЫТО (публикация при первом подходящем контенте)")
    else:
        print(f"  с момента последней публикации: {fmt_seconds(elapsed)}")
        if remaining <= 0:
            print("  -> окно ОТКРЫТО прямо сейчас")
        else:
            mins = remaining / 60
            print(f"  -> окно откроется через ~{remaining/3600:.1f}ч ({mins:.0f} мин)")

    if post_type in ("opinion", "article"):
        remaining_backoff = queue_manager.get_retry_backoff_remaining_seconds(post_type)
        if remaining_backoff is not None:
            print(f"  !! БЭКОФФ ПОСЛЕ СБОЯ АКТИВЕН: попыток не будет ещё ~{remaining_backoff/60:.0f} мин, "
                  f"даже если окно выше открыто (см. лог последнего сбоя в Run one bot check).")


def main() -> None:
    print("=== Состояние бота (только чтение, без запросов к API) ===")

    offset = queue_manager.get_telegram_update_offset()
    print(f"\nTelegram update offset: {offset}")

    if config.TELEGRAM_PUBLISH_CHANNEL:
        print(f"Кросспостинг в Telegram: включён -> {config.TELEGRAM_PUBLISH_CHANNEL}")
    else:
        print("Кросспостинг в Telegram: ВЫКЛЮЧЕН (TELEGRAM_PUBLISH_CHANNEL не задан)")

    queue_len = queue_manager.pending_queue_length()
    pending = queue_manager.get_pending_post(min_score=config.MIN_SIGNAL_SCORE_TO_PUBLISH)
    if queue_len == 0:
        print("\nОчередь на публикацию (валюта): ПУСТА — даже если откроется окно, "
              "публиковать нечего, пока не придёт новый сигнал.")
    else:
        print(f"\nОчередь на публикацию (валюта): {queue_len} поста(ов) лежат, "
              f"порог публикации: score > {config.MIN_SIGNAL_SCORE_TO_PUBLISH}.")
        if pending is None:
            print("  Ни один из них не проходит порог по score - бот НЕ будет "
                  "публиковать, даже если окно открыто. Ждём сигнал получше "
                  "или истечения лимита попыток/переполнения очереди.")
        else:
            _, kind, payload = pending
            if kind == "signal":
                print(f"  Лучший подходящий: тип=signal, "
                      f"тикер={payload.ticker}, score={payload.score}, направление={payload.direction}, "
                      f"вход={payload.entry_low}-{payload.entry_high}, "
                      f"стоп={payload.invalidation}, тейк={payload.target}")
            elif kind == "image":
                print(f"  Лучший подходящий: тип=image (без score), "
                      f"тикер={payload.ticker}, направление={payload.direction}")
        if queue_len > 1:
            print("  Вся очередь:")
            for line in queue_manager.pending_queue_summary():
                print(f"    - {line}")
        print("  -> если окно публикации (currency) открыто и есть подходящий пост выше - "
              "бот опубликует именно его на следующем тике (если пройдёт генерацию "
              "текста, проверку чисел и получится сделать график/скачать картинку).")

    report_window("currency", config.MIN_POST_INTERVAL_HOURS)
    report_window("opinion", config.OPINION_INTERVAL_HOURS)
    report_window("article", config.ARTICLE_INTERVAL_HOURS)

    open_outcomes = queue_manager.get_open_outcomes()
    print(f"\n=== Трекинг результатов сигналов ===")
    print(f"Открытых (ждут тейка/стопа/таймаута): {len(open_outcomes)}")

    def _fmt_bucket(name: str, s: dict) -> str:
        if s["count"] == 0:
            return f"    {name}: нет данных"
        wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "н/д"
        return f"    {name}: n={s['count']}, win-rate={wr}, средний результат={s['avg_pnl_pct']:+.2f}%"

    for label, days in (("за всё время", None), ("за 30 дней", 30), ("за 7 дней", 7)):
        stats = outcome_tracker.get_accuracy_stats(days=days)
        overall = stats["overall"]
        print(f"\n  [{label}]")
        print(_fmt_bucket("итого", overall))
        if overall["count"] and stats["by_strategy"]:
            print("    по стратегии:")
            for strat, s in sorted(stats["by_strategy"].items(), key=lambda kv: -kv[1]["count"]):
                print("  " + _fmt_bucket(strat, s))
        if overall["count"] and stats["by_quality"]:
            print("    по качеству:")
            for q, s in sorted(stats["by_quality"].items(), key=lambda kv: -kv[1]["count"]):
                print("  " + _fmt_bucket(q, s))

    print(f"\nИнтервал тика бота: {config.POLL_INTERVAL_SECONDS}с "
          "(в GitHub Actions фактически раз в ~10 мин по расписанию cron, "
          "независимо от этого значения).")

    print("\n--- Вывод ---")
    if pending is not None:
        elapsed = queue_manager.seconds_since_last_post("currency")
        jitter = queue_manager.get_jitter_seconds("currency")
        min_seconds = config.MIN_POST_INTERVAL_HOURS * 3600 + jitter
        if elapsed >= min_seconds:
            print("Если СЕЙЧАС появится новый тик (запуск бота) - пост на Binance "
                  "ОПУБЛИКУЕТСЯ (валютный формат), при условии что новый пост "
                  "уже лежит в очереди как показано выше.")
        else:
            print("Новый пост в канале ляжет в очередь, но публикация ВАЛЮТНОГО "
                  "формата произойдёт не раньше, чем откроется окно (см. выше).")
    else:
        print("Если новый пост появится в канале ДО следующего тика бота - "
              "он попадёт в очередь, но публикация всё равно произойдёт "
              "не раньше открытия окна (currency), см. выше.")


if __name__ == "__main__":
    main()