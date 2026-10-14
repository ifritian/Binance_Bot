"""
coin_whitelist.py - белый список "узнаваемых" монет для публикации
сигналов на Binance Square.

Зачем: сканер берёт топ-150 пар по суточному обороту, а туда попадают и
разовые "памп"-монеты (MARSCOIN, KORUB, TUT и т.п.) - оборот у них высокий,
но аудитории у тега $TICKER нет, и посты по ним не набирают ни реакций, ни
подписчиков. Фильтр применяется ТОЛЬКО при выборе поста из очереди
публикации (queue_manager.get_pending_post) - сканер, торговый блок
(futures_*) и трекинг результатов работают как раньше.

Состав списка: крупнейшие по капитализации монеты, популярные у розницы
L1/L2/DeFi/AI-проекты и самые известные мемкоины, которые торгуются на
споте Binance к USDT. Монеты, на которые уже объявлен делистинг (ACX, HFT,
PIVX, PYR, VANRY, VIC и др.), сюда не входят. Актуальность торговли пары
дополнительно проверяет сам сканер (scanner._is_actively_trading).

Менять список без правки кода можно через переменные окружения (секреты
GitHub): SQUARE_COIN_WHITELIST_EXTRA - добавить тикеры, SQUARE_COIN_WHITELIST_
EXCLUDE - убрать, SQUARE_COIN_WHITELIST_ENABLED=false - отключить фильтр.
"""
import config

DEFAULT_WHITELIST = frozenset("""
BTC ETH BNB XRP SOL TRX DOGE ADA HYPE LINK
AVAX DOT LTC BCH XLM SUI TON SHIB UNI AAVE
NEAR APT ICP ETC FIL ATOM HBAR ARB OP INJ
TIA SEI STX RENDER FET TAO WLD ENA ONDO JUP
PENDLE CRV MKR LDO POL ALGO VET THETA EGLD S
RUNE PEPE BONK WIF FLOKI TRUMP PENGU WLFI ZEC DASH
QNT GRT SAND MANA AXS GALA IMX CAKE DYDX SNX
COMP CHZ APE ENS JTO XTZ NEO
""".split())


def _parse(raw: str) -> set:
    return {item.strip().lstrip("$#").upper() for item in (raw or "").split(",") if item.strip().lstrip("$#")}


def allowed_tickers() -> frozenset:
    """Итоговый список: базовый + SQUARE_COIN_WHITELIST_EXTRA - SQUARE_COIN_WHITELIST_EXCLUDE."""
    tickers = set(DEFAULT_WHITELIST) | _parse(config.SQUARE_COIN_WHITELIST_EXTRA)
    tickers -= _parse(config.SQUARE_COIN_WHITELIST_EXCLUDE)
    return frozenset(tickers)


def is_allowed(ticker) -> bool:
    """True, если пост про эту монету можно публиковать на Square. При
    выключенном фильтре (SQUARE_COIN_WHITELIST_ENABLED=false) пропускает всё."""
    if not config.SQUARE_COIN_WHITELIST_ENABLED:
        return True
    return str(ticker or "").strip().lstrip("$#").upper() in allowed_tickers()
