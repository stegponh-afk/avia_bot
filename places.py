"""
Справочник направлений: IATA-код -> русское название города, страна, координаты.

Берём готовые файлы Travelpayouts — они бесплатные, без токена и сразу на русском.
Скачиваем один раз в places.json, дальше читаем из кэша.
"""
import json
import math
import os
import time

import requests

import config as C

CACHE = C.PLACES
TTL_DAYS = 30
SRC = {
    "cities":    "https://api.travelpayouts.com/data/ru/cities.json",
    "airports":  "https://api.travelpayouts.com/data/ru/airports.json",
    "countries": "https://api.travelpayouts.com/data/ru/countries.json",
}

KZN = (55.6062, 49.2787)   # Казань, для расчёта расстояния

_places = None


def _download():
    out = {}
    countries = {}
    try:
        r = requests.get(SRC["countries"], timeout=30,
                         proxies={"https": C.API_PROXY} if C.API_PROXY else None)
        for c in r.json():
            countries[c["code"]] = c.get("name") or c["code"]
    except Exception as e:
        print(f"  справочник стран не скачался: {e}")

    for kind in ("cities", "airports"):
        try:
            r = requests.get(SRC[kind], timeout=60,
                             proxies={"https": C.API_PROXY} if C.API_PROXY else None)
            for it in r.json():
                code = it.get("code")
                if not code or code in out:
                    continue
                co = it.get("coordinates") or {}
                out[code] = {
                    "name": it.get("name") or code,
                    # у аэропорта — код его города: SVO -> MOW. Без этого
                    # пересадка «Шереметьево → Внуково» выглядит как две
                    "city": it.get("city_code") if kind == "airports" else None,
                    "country": countries.get(it.get("country_code"), it.get("country_code") or ""),
                    "lat": co.get("lat"),
                    "lon": co.get("lon"),
                }
        except Exception as e:
            print(f"  справочник {kind} не скачался: {e}")
    return out


def load(force=False):
    """Возвращает словарь кодов. Кэш обновляется раз в месяц."""
    global _places
    if _places is not None and not force:
        return _places

    fresh = (os.path.exists(CACHE)
             and time.time() - os.path.getmtime(CACHE) < TTL_DAYS * 86400)
    if fresh and not force:
        with open(CACHE, encoding="utf-8") as f:
            _places = json.load(f)
        # кэш старого формата, без привязки аэропортов к городам — перекачать
        if "city" in (_places.get("SVO") or {"city": 1}):
            return _places
        print("  справочник старого формата")

    print("  обновляю справочник направлений...")
    data = _download()
    if data:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        _places = data
    elif os.path.exists(CACHE):          # сеть подвела — живём на старом кэше
        with open(CACHE, encoding="utf-8") as f:
            _places = json.load(f)
    else:
        _places = {}
    return _places


def name(code):
    """AER -> 'Сочи'. Неизвестный код возвращаем как есть."""
    p = load().get(code)
    return p["name"] if p else code


_routes = None


def direct_routes(origin):
    """
    Куда из города летают прямые рейсы, по справочнику маршрутов Travelpayouts.

    Файл большой (14 МБ), поэтому храним выжимку «город -> города» и
    обновляем раз в месяц, как и справочник городов.
    """
    global _routes
    if _routes is None:
        path = C.ROUTES
        fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL_DAYS * 86400
        if fresh:
            with open(path, encoding="utf-8") as f:
                _routes = json.load(f)
        else:
            by = {}
            try:
                r = requests.get("https://api.travelpayouts.com/data/routes.json", timeout=120,
                                 proxies={"https": C.API_PROXY} if C.API_PROXY else None)
                for x in r.json():
                    if x.get("transfers"):
                        continue
                    o = city_of(x.get("departure_airport_iata"))
                    d = city_of(x.get("arrival_airport_iata"))
                    if o and d and o != d:
                        by.setdefault(o, set()).add(d)
                _routes = {o: sorted(ds) for o, ds in by.items()}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(_routes, f)
            except Exception as e:
                print(f"  справочник маршрутов не скачался: {e}")
                _routes = {}
    return _routes.get(origin, [])


def city_of(code):
    """Аэропорт -> код города: SVO -> MOW, SAW -> IST. Город -> он сам."""
    p = load().get(code)
    return (p.get("city") or code) if p else code


def full(code):
    """AER -> 'Сочи, Россия'."""
    p = load().get(code)
    if not p:
        return code
    return f"{p['name']}, {p['country']}" if p.get("country") else p["name"]


def distance(code, frm=KZN):
    """Расстояние по большому кругу от Казани, км. None если координат нет."""
    p = load().get(code)
    if not p or p.get("lat") is None:
        return None
    lat1, lon1 = map(math.radians, frm)
    lat2, lon2 = math.radians(p["lat"]), math.radians(p["lon"])
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return round(6371 * 2 * math.asin(math.sqrt(a)))


def country(code):
    """AER -> 'Россия'. Нужно для признака «дешевле всех городов страны»."""
    p = load().get(code)
    return (p.get("country") or None) if p else None


def exists(code):
    return code in load()


def in_country(name):
    """Все коды направлений указанной страны."""
    return [c for c, p in load().items() if p.get("country") == name]


# Как города зовут в жизни, а не в справочнике. Без этого «Питер» не находится.
ALIASES = {
    "питер": "LED", "спб": "LED", "санкт петербург": "LED", "петербург": "LED",
    "мск": "MOW", "москва": "MOW",
    "ёбург": "SVX", "екб": "SVX", "екат": "SVX", "свердловск": "SVX",
    "нижний": "GOJ", "нн": "GOJ",
    "новосиб": "OVB", "нск": "OVB",
    "сочи": "AER", "адлер": "AER",
    "казань": "KZN", "киров": "KVX",
    "стамбул": "IST", "дубай": "DXB", "анталия": "AYT", "анталья": "AYT",
    "ташкент": "TAS", "баку": "BAK", "ереван": "EVN", "минск": "MSQ",
}


def find(query):
    """Поиск кода по русскому названию: 'сочи' -> 'AER'."""
    q = query.strip().lower()
    if q in ALIASES:
        return ALIASES[q]
    if len(q) == 3 and q.upper() in load():
        return q.upper()
    for code, p in load().items():
        if p["name"].lower() == q:
            return code
    for code, p in load().items():
        if q in p["name"].lower():
            return code
    return None


if __name__ == "__main__":
    load(force=True)
    print(f"направлений в справочнике: {len(load())}")
    for c in ("AER", "LED", "MOW", "IST", "DXB", "TAS"):
        print(f"  {c}: {full(c)}, {distance(c)} км")
