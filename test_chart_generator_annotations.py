#!/usr/bin/env python3
"""
Тесты рукописных пометок на графиках (chart_generator._wobbly_*,
draw_win_annotations, draw_signal_annotations, _nearest_candle_index).

Раньше этих тестов не было вообще - пометки были добавлены и
подключены в generate_chart_image(), но без единого теста. Ручная
рендер-проверка (сгенерировать реальный PNG и посмотреть на него)
сразу поймала баг: у draw_signal_annotations стрелка для "long"
указывала ВНИЗ вместо ВВЕРХ (перепутан знак смещения по Y). Тест
test_signal_annotation_arrow_points_up_for_long/_down_for_short
закрепляет именно этот случай, чтобы регрессия не проскочила снова
без визуального ревью.
"""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

import chart_generator as cg


def _make_candles(n=48, base=100.0, step_ms=3_600_000, start_ms=1_700_000_000_000):
    """Простые псевдослучайные (но детерминированные) свечи для тестов
    геометрии - реальные значения OHLC не важны, важен диапазон цен и
    количество свечей (x_span/price_span)."""
    candles = []
    price = base
    for i in range(n):
        o = price
        c = price + (1 if i % 2 == 0 else -1) * 0.5
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        candles.append({
            "open_time": start_ms + i * step_ms,
            "open": o, "high": h, "low": l, "close": c,
            "volume": 10.0,
        })
        price = c
    return candles


# --- _wobbly_ellipse_points / _wobbly_line_points: геометрия -------------

def test_wobbly_ellipse_points_closed_ring():
    xs, ys = cg._wobbly_ellipse_points(0, 0, 10, 5, n=60)
    assert len(xs) == len(ys) == 61
    # Кольцо должно замыкаться - первая и последняя точка совпадают.
    assert xs[0] == pytest.approx(xs[-1])
    assert ys[0] == pytest.approx(ys[-1])


def test_wobbly_ellipse_points_stay_within_wobble_bound():
    cx, cy, rx, ry, wobble = 5.0, 200.0, 2.0, 8.0, 0.10
    xs, ys = cg._wobbly_ellipse_points(cx, cy, rx, ry, wobble=wobble)
    for x, y in zip(xs, ys):
        # Расстояние (в нормированных по rx/ry координатах) от центра
        # должно быть в пределах [1-wobble, 1+wobble] с небольшим
        # запасом на интерполяцию между контрольными точками.
        norm = math.hypot((x - cx) / rx, (y - cy) / ry)
        assert 1 - wobble - 1e-6 <= norm <= 1 + wobble + 1e-6


def test_wobbly_line_points_perpendicular_offset_bounded_by_amplitude():
    x0, y0, x1, y1, wobble = 0.0, 0.0, 10.0, 0.0, 0.08
    length = math.hypot(x1 - x0, y1 - y0)
    amp = length * wobble
    xs, ys = cg._wobbly_line_points(x0, y0, x1, y1, n=16, wobble=wobble)
    # Линия горизонтальная, значит дрожание (перпендикулярно, т.е. по Y)
    # должно оставаться в пределах +-amp на каждой точке, включая концы
    # (см. docstring - control points на самих краях тоже "дрожат", это
    # не баг, а часть имитации "руки", а не гарантия точного совпадения
    # с исходными координатами).
    for y in ys:
        assert -amp - 1e-9 <= y <= amp + 1e-9


def test_wobbly_line_points_deterministic_with_seed():
    rng1 = cg.random.Random(123)
    rng2 = cg.random.Random(123)
    xs1, ys1 = cg._wobbly_line_points(0, 0, 10, 5, rng=rng1)
    xs2, ys2 = cg._wobbly_line_points(0, 0, 10, 5, rng=rng2)
    assert xs1 == xs2
    assert ys1 == ys2


# --- _nearest_candle_index ------------------------------------------------

def test_nearest_candle_index_exact_match():
    candles = _make_candles(n=10)
    target = candles[4]["open_time"]
    assert cg._nearest_candle_index(candles, target) == 4


def test_nearest_candle_index_out_of_range_returns_none():
    candles = _make_candles(n=10)
    # Время далеко за пределами диапазона свечей (на много шагов раньше).
    far_before = candles[0]["open_time"] - 100 * 3_600_000
    assert cg._nearest_candle_index(candles, far_before) is None


def test_nearest_candle_index_empty_candles():
    assert cg._nearest_candle_index([], 12345) is None


# --- draw_win_annotations -------------------------------------------------

def _new_ax():
    fig, ax = plt.subplots()
    yield_ax = ax
    return fig, yield_ax


def test_draw_win_annotations_returns_true_and_draws_circle_and_check():
    fig, ax = plt.subplots()
    candles = _make_candles(n=48)
    n_lines_before = len(ax.lines)

    result = cg.draw_win_annotations(
        ax, candles,
        entry_price=candles[5]["open"],
        entry_time=candles[5]["open_time"] / 1000,
        exit_price=candles[-1]["close"],
        seed=42,
    )

    assert result is True
    # Круг (1 линия) + галочка (2 штриха) = минимум 3 новые линии.
    assert len(ax.lines) - n_lines_before >= 3
    plt.close(fig)


def test_draw_win_annotations_entry_out_of_range_still_draws_checkmark():
    fig, ax = plt.subplots()
    candles = _make_candles(n=48)
    far_before = candles[0]["open_time"] / 1000 - 1_000_000

    result = cg.draw_win_annotations(
        ax, candles,
        entry_price=candles[0]["open"],
        entry_time=far_before,
        exit_price=candles[-1]["close"],
        seed=1,
    )

    # Пометка входа пропускается, но галочка у выхода рисуется всегда.
    assert result is True
    plt.close(fig)


# --- draw_signal_annotations: направление стрелки (регрессия) ------------

def _arrow_tip_and_entry_y(ax, entry_price):
    """Из всех нарисованных линий на ax находит те, что относятся к
    стрелке (не входят в окрестность круга по y) и возвращает
    максимальное отклонение y от entry_price с сохранением знака
    смещения в сторону, где кончик стрелки дальше всего от входа."""
    max_abs_offset = 0.0
    signed_offset = 0.0
    for line in ax.lines:
        ydata = line.get_ydata()
        for y in ydata:
            offset = y - entry_price
            if abs(offset) > max_abs_offset:
                max_abs_offset = abs(offset)
                signed_offset = offset
    return signed_offset


def test_signal_annotation_arrow_points_up_for_long():
    fig, ax = plt.subplots()
    candles = _make_candles(n=48)
    entry_price = candles[-1]["close"]

    cg.draw_signal_annotations(ax, candles, entry_price=entry_price, direction="long", seed=7)

    signed_offset = _arrow_tip_and_entry_y(ax, entry_price)
    # Для "long" самая дальняя от входа точка (кончик стрелки) должна
    # быть ВЫШЕ entry_price (больше по Y) - см. docstring
    # draw_signal_annotations. Раньше здесь было наоборот (баг).
    assert signed_offset > 0, (
        "Стрелка для 'long' должна указывать вверх (выше entry_price), "
        f"а максимальное отклонение оказалось {signed_offset}"
    )
    plt.close(fig)


def test_signal_annotation_arrow_points_down_for_short():
    fig, ax = plt.subplots()
    candles = _make_candles(n=48)
    entry_price = candles[-1]["close"]

    cg.draw_signal_annotations(ax, candles, entry_price=entry_price, direction="short", seed=7)

    signed_offset = _arrow_tip_and_entry_y(ax, entry_price)
    assert signed_offset < 0, (
        "Стрелка для 'short' должна указывать вниз (ниже entry_price), "
        f"а максимальное отклонение оказалось {signed_offset}"
    )
    plt.close(fig)


def test_draw_signal_annotations_empty_candles_returns_false():
    fig, ax = plt.subplots()
    assert cg.draw_signal_annotations(ax, [], entry_price=100.0, direction="long") is False
    plt.close(fig)


# --- generate_chart_image: сквозная проверка with monkeypatch ------------

def test_generate_chart_image_with_win_annotation_does_not_raise(tmp_path, monkeypatch):
    candles = _make_candles(n=48)
    monkeypatch.setattr(cg, "fetch_klines", lambda ticker, days: candles)
    monkeypatch.setattr(cg, "_CHARTS_DIR", tmp_path)

    win_annotation = {"entry_price": candles[5]["open"], "entry_time": candles[5]["open_time"] / 1000}
    path = cg.generate_chart_image(
        "TESTCOIN", days=2, expected_price=candles[-1]["close"],
        filename_suffix="_wintest", win_annotation=win_annotation,
    )

    assert path is not None
    assert path.exists()


def test_generate_chart_image_annotation_failure_does_not_break_publication(tmp_path, monkeypatch):
    """Пометка декоративная - если draw_win_annotations упадёт с
    исключением, график всё равно должен сохраниться (см. try/except
    в generate_chart_image)."""
    candles = _make_candles(n=48)
    monkeypatch.setattr(cg, "fetch_klines", lambda ticker, days: candles)
    monkeypatch.setattr(cg, "_CHARTS_DIR", tmp_path)

    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(cg, "draw_win_annotations", _boom)

    win_annotation = {"entry_price": candles[5]["open"], "entry_time": candles[5]["open_time"] / 1000}
    path = cg.generate_chart_image(
        "TESTCOIN", days=2, expected_price=candles[-1]["close"],
        filename_suffix="_failtest", win_annotation=win_annotation,
    )

    assert path is not None
    assert path.exists()
