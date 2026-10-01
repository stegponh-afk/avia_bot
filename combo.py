"""
Связки: два отдельных билета через третий город дешевле, чем один билет.

Классическая ситуация — одним билетом 10 000, а A→B плюс B→C двумя билетами
выходят в 8 000. Слово «прямой» тут неточно: маршрут одним билетом тоже может
быть с пересадками, важно именно число заказов. Ни один агрегатор такого
не покажет: все они ищут то, что продаётся одним заказом.

Почему поиск точечный, а не «просканируем всё подряд». Широкие эндпоинты
(latest, city-directions) отдают по паре разрозненных дат на направление,
причём city-directions — вообще цены туда-обратно. Складывать такое с
односторонним плечом нельзя, а пересечений по датам почти не бывает: на
живых данных широкий скан дал четыре «находки», и все четыре оказались
смесью одностороннего билета с круговым. Зато prices_for_dates по КОНКРЕТНОЙ
паре отдаёт полную сетку на месяц: 31 дата, все в одну сторону, с временем
вылета и длительностью. Поэтому берём короткий список направлений
и запрашиваем календари по ним.

Главная оговорка, которую бот обязан сказать вслух: это ДВА НЕЗАВИСИМЫХ билета.
Опоздал на второй из-за задержки первого — никто ничего не обязан менять,
багаж надо получать и сдавать заново. Поэтому запас времени тут больше,
чем у обычной пересадки, а при смене аэропорта в городе — ещё больше.
"""
from datetime import datetime, timedelta

import baseline
import config as C
import db
import places
import tp


def _dt(s):
    """'2026-11-16T12:50:00+03:00' -> datetime с поясом."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def need_layover(leg1, leg2):
    """
    Сколько часов запаса требовать между рейсами.

    Тонкость: часть эндпоинтов не отдаёт коды аэропортов, и тогда в поле стоит
    код города. Для Москвы это опасно — MOW это и Шереметьево, и Домодедово,
    и Внуково, между ними три часа с багажом. Поэтому «аэропорт неизвестен»
    приравниваем к «аэропорты разные» и требуем больший запас.
    """
    a1, a2 = leg1.get("to_airport"), leg2.get("from_airport")
    known = a1 and a2 and a1 != leg1.get("dest") and a2 != leg2.get("origin")
    if known and a1 == a2:
        return C.COMBO_MIN_LAYOVER_H
    return C.COMBO_DIFF_AIRPORT_H


def layover(leg1, leg2):
    """Часы между прилётом первого и вылетом второго, либо None если не стыкуется."""
    dep1, dep2 = _dt(leg1.get("depart_at")), _dt(leg2.get("depart_at"))
    dur = leg1.get("duration")
    if not dep1 or not dep2 or not dur:
        return None
    gap = (dep2 - (dep1 + timedelta(minutes=int(dur)))).total_seconds() / 3600
    if gap <= 0 or gap > C.COMBO_MAX_LAYOVER_H:
        return None
    return gap if gap >= need_layover(leg1, leg2) else None


def _by_date(rows):
    """Список предложений -> {дата: самое дешёвое}. Круговые тарифы отбрасываем."""
    out = {}
    for r in rows:
        if r.get("ret"):        # цена туда-обратно, с односторонним плечом не сложить
            continue
        day = r.get("depart")
        if not day or not r.get("depart_at"):
            continue
        if day not in out or r["price"] < out[day]["price"]:
            out[day] = r
    return out


def pick_targets(origin, limit=None):
    """
    Куда имеет смысл искать связку.

    Берём направления, переоценённые относительно своего расстояния: именно
    там прямой дорогой, а через хаб можно уехать дешевле. Пока кривая
    не построена, сортировка выродится в «самые дорогие за километр».
    """
    limit = limit or C.COMBO_SCAN_DESTS
    scored = []
    for dest, price in db.min_by_direction(origin, C.VALUE_WINDOW_DAYS):
        if not price or dest in ("?", origin) or price > C.COMBO_PRICE_CAP:
            continue
        km = places.distance(dest)
        if not km or km < C.VALUE_MIN_KM:
            continue
        exp = baseline.expected(origin, km)
        scored.append((price / exp if exp else price / km, dest))
    scored.sort(reverse=True)
    return [dest for _, dest in scored[:limit]]


def _hubs(origin, dest, limit=None):
    """Через какие города пробовать. Сам город вылета и цель — не хабы."""
    return [h for h in C.COMBO_HUBS
            if h not in (origin, dest)][:limit or C.COMBO_SCAN_HUBS]


def _combine(origin, hub, dest, legs1, legs2, directs):
    """Из трёх календарей собирает лучшую связку. Все цены — в одну сторону."""
    best = None
    for day1, leg1 in legs1.items():
        d1 = _dt(leg1.get("depart_at"))
        if not d1:
            continue
        # второй рейс в тот же день или на следующий: дальше это уже
        # не пересадка, а остановка в городе
        for shift in (0, 1):
            leg2 = legs2.get((d1.date() + timedelta(days=shift)).isoformat())
            if not leg2:
                continue
            gap = layover(leg1, leg2)
            if gap is None:
                continue

            total = leg1["price"] + leg2["price"]
            if total > C.COMBO_PRICE_CAP:
                continue     # экономия есть, но билет всё равно неподъёмный

            # сравниваем с прямым в те же примерно даты, а не с самым дешёвым
            # за весь месяц: человек летит в конкретные дни
            near = [d for day, d in directs.items()
                    if abs((datetime.fromisoformat(day).date() - d1.date()).days)
                    <= C.COMBO_DATE_TOL]
            if not near:
                continue
            direct = min(near, key=lambda x: x["price"])

            save = direct["price"] - total
            if (save < C.COMBO_MIN_SAVE_RUB
                    or save < direct["price"] * C.COMBO_MIN_SAVE_PCT / 100):
                continue
            if best and best["price"] <= total:
                continue

            best = {
                "origin": origin, "dest": dest, "via": hub,
                "depart": day1, "depart_at": leg1.get("depart_at"),
                "ret": None, "price": total,
                "airline": None, "flight": "", "transfers": None, "duration": None,
                "link": leg1.get("link"), "src": "combo",
                "legs": [leg1, leg2],
                "layover_h": gap,
                "direct_price": direct["price"],
                "direct_link": direct.get("link"),
                "saving": save,
                "airport_known": bool(
                    leg1.get("to_airport") != leg1.get("dest")
                    and leg2.get("from_airport") != leg2.get("origin")),
            }
    return best


async def search(session, origin, dests=None, months=None, log=None, hubs_n=None,
                 progress=None):
    """
    Точечный поиск связок для одного города вылета.

    Запросов: направления × (1 + хабы) на каждый месяц плюс календари до хабов.
    При настройках по умолчанию это около сорока — примерно минута работы,
    поэтому за раз проверяется короткий список направлений, а не всё подряд.

    Если направление названо явно (dests=['AYT']), запросов выходит меньше
    десятка, и тогда есть смысл перебрать больше хабов: hubs_n.
    """
    if not C.COMBO_ENABLED:
        return []

    dests = dests or pick_targets(origin)
    months = months or tp.months_ahead(C.COMBO_SCAN_MONTHS)
    if not dests:
        return []

    api = tp.Travelpayouts(session, origin)
    found = []

    for ym in months:
        to_hub = {}                  # календари origin → хаб, общие для всех целей
        for n, dest in enumerate(dests, 1):
            if progress:
                progress(f"{places.name(dest)} · {n} из {len(dests)}, {ym}")
            directs = _by_date(await api.calendar(dest, ym))
            if not directs:
                continue
            for hub in _hubs(origin, dest, hubs_n):
                if hub not in to_hub:
                    to_hub[hub] = _by_date(await api.calendar(hub, ym))
                if not to_hub[hub]:
                    continue
                legs2 = _by_date(
                    await tp.Travelpayouts(session, hub).calendar(dest, ym))
                if not legs2:
                    continue
                c = _combine(origin, hub, dest, to_hub[hub], legs2, directs)
                if c:
                    found.append(c)
                    if log:
                        log(describe(c))

    best = {}                        # на каждое направление оставляем лучший вариант
    for c in found:
        k = (c["origin"], c["dest"])
        if k not in best or c["price"] < best[k]["price"]:
            best[k] = c
    return list(best.values())


def describe(c):
    """Короткая строка для логов и диагностики."""
    return (f"{places.name(c['origin'])} → {places.name(c['via'])} → "
            f"{places.name(c['dest'])}: {c['price']} вместо {c['direct_price']}, "
            f"экономия {c['saving']}, пересадка {c['layover_h']:.1f} ч")
