"""Цветовая тема второго аккаунта (BOT_PROFILE=b): основной золотой цвет заменяется фиолетовым, светло-золотой остаётся
акцентом (подписи от руки, метки уровней). Красный (падение), серый и белый не трогаются.

Работает на готовых картинках и кадрах видео: модули рисуют как обычно, перекраска идёт в самом конце. У основного
аккаунта тема выключена, все функции возвращают картинку как есть."""
import numpy as np
from PIL import Image

import config

HUE_FROM = (30 / 360 * 255, 47 / 360 * 255)     # "основное" золото: 30-47 градусов (F0B90B = 45.6); светлое FCD535 (48.2) не трогаем
HUE_TO = 262 / 360 * 255                          # фиолетовый
SAT_MUL, VAL_MUL = 0.66, 1.02
MIN_SAT = 0.45 * 255


def active() -> bool:
    return config.THEME == "violet"


def apply(img: Image.Image) -> Image.Image:
    """RGB-картинка с перекрашенным золотом; без темы - та же картинка."""
    if not active():
        return img
    hsv = np.asarray(img.convert("RGB").convert("HSV"), dtype=np.float32).copy()
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (h >= HUE_FROM[0]) & (h <= HUE_FROM[1]) & (s >= MIN_SAT)
    h[mask] = HUE_TO
    s[mask] = s[mask] * SAT_MUL
    v[mask] = np.minimum(v[mask] * VAL_MUL, 255)
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def recolor_file(path):
    """Перекрашивает файл-картинку на месте; возвращает путь. При любой ошибке файл остаётся как был."""
    if not active():
        return path
    try:
        apply(Image.open(path).convert("RGB")).save(path)
    except Exception:
        pass
    return path
