"""
Погода в городе прилёта на даты поездки — строкой в посте и на карточке.

Источник — Open-Meteo: бесплатный, без ключа. Прогноз есть на 16 дней
вперёд; дальше показываем, какая погода была в эти же даты год назад
(архив), и честно пишем «обычно». Не ответил — пост выходит без погоды.
"""
import asyncio
import time
from collections import Counter
from datetime import date, timedelta

import aiohttp

import config as C
import places

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
# Прогноз сервис даёт на 16 дней, но считает их по Гринвичу: около полуночи
# по Москве его «сегодня» ещё вчера. Берём с запасом — дальше прошлый год.
HORIZON = 13
_cache = {}           # (код, с, по) -> (когда спросили, ответ)
TTL = 3 * 3600
FAIL_TTL = 10 * 60    # не ответил — переспросим скоро, а не через три часа
_gate = None          # не больше двух запросов разом: на шесть сразу сервис отказывает


def _semaphore():
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(2)
    return _gate


def _icon(code):
    """Код погоды WMO -> значок."""
    if code is None:
        return "🌡"
    if code <= 1:
        return "☀️"
    if code <= 3:
        return "⛅"
    if code <= 48:
        return "🌫"
    if code <= 67 or 80 <= code <= 82:
        return "🌧"
    if code <= 77 or 85 <= code <= 86:
        return "❄️"
    return "⛈"


async def for_trip(code, start, end=None):
    """
    Погода на поездку: {day, night, icon, usual}. day/night — средние
    дневная и ночная температуры за даты поездки; usual — это не прогноз,
    а прошлый год. Нет координат или сервис молчит — None.
    """
    p = places.load().get(code)
    if not p or p.get("lat") is None or not start:
        return None
    a = date.fromisoformat(start[:10])
    b = date.fromisoformat(end[:10]) if end else a + timedelta(days=2)
    b = min(b, a + timedelta(days=13))
    key = (code, a, b)
    if key in _cache and time.time() - _cache[key][0] < (TTL if _cache[key][1] else FAIL_TTL):
        return _cache[key][1]

    usual = a > date.today() + timedelta(days=HORIZON)
    if usual:
        url = ARCHIVE
        a2, b2 = a - timedelta(days=365), b - timedelta(days=365)   # 29 февраля тоже
    else:
        url = FORECAST
        a2, b2 = a, min(b, date.today() + timedelta(days=HORIZON))
    params = {"latitude": p["lat"], "longitude": p["lon"], "timezone": "auto",
              "daily": "temperature_2m_max,temperature_2m_min,weather_code",
              "start_date": a2.isoformat(), "end_date": b2.isoformat()}
    try:
        async with _semaphore(), aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10),
                             proxy=C.API_PROXY) as r:
                body = await r.json(content_type=None)
        if body.get("error"):
            raise ValueError(body.get("reason") or "ошибка сервиса")
        data = body.get("daily") or {}
        hi =[t for t in data.get("temperature_2m_max") or [] if t is not None]
        lo = [t for t in data.get("temperature_2m_min") or [] if t is not None]
        codes = [c for c in data.get("weather_code") or [] if c is not None]
        if not hi or not lo:
            raise ValueError("пустой ответ")
        # значок — самая частая погода, но дождь важнее солнца: хоть день из трёх
        # с дождём — так и пишем
        icons = [_icon(c) for c in codes]
        icon = Counter(icons).most_common(1)[0][0] if icons else "🌡"
        if "🌧" in icons and icon in ("☀️", "⛅") and icons.count("🌧") * 3 >= len(icons):
            icon = "🌧"
        w = {"day": round(sum(hi) / len(hi)), "night": round(sum(lo) / len(lo)),
             "icon": icon, "usual": usual}
    except Exception as e:
        print(f"  погода {code}: {type(e).__name__}: {e}")
        w = None
    _cache[key] = (time.time(), w)
    return w


def deg(t):
    return f"+{t}°" if t > 0 else f"{t}°"


def line(w, city):
    """«☀️ Анталья: днём +24°, ночью +16° — прогноз на твои даты»."""
    if not w:
        return None
    tail = "обычно в эти даты" if w["usual"] else "прогноз на даты поездки"
    return f"{w['icon']} {city}: днём {deg(w['day'])}, ночью {deg(w['night'])} — {tail}"


def short(w):
    """Для карточки, без значка (в шрифте нет цветных эмодзи): «днём +24°»."""
    return f"днём {deg(w['day'])}" if w else None
