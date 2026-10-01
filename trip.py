"""
Конкретный маршрут на конкретные даты: «18 сентября Киров → Питер, 20-го обратно».

Всё остальное в боте отвечает на вопрос «куда бы слетать подешевле». Здесь
наоборот: маршрут и даты человек уже выбрал, и нужно ответить, сколько это
стоит и нельзя ли дешевле.

Три ответа сразу, потому что поодиночке они мало что значат:

  1. Туда-обратно одним билетом — то, что показал бы любой сайт.
  2. Два отдельных билета в одну сторону. Часто это дешевле, а иногда заметно:
     сайты по умолчанию так не ищут, потому что продают одним заказом.
  3. Соседние даты. Сдвиг на день нередко стоит тысячи, и об этом человек
     узнаёт, только если ему показать.

Запросов выходит немного: по одному на каждый из трёх вопросов плюс два
календаря на месяц, из которых и берутся соседние даты.
"""
from datetime import datetime, timedelta

import config as C
import tp


def _d(s):
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def _cheapest(rows):
    return min(rows, key=lambda r: r["price"]) if rows else None


def _by_date(rows):
    """{дата: самое дешёвое в одну сторону}. Круговые тарифы отбрасываем."""
    out = {}
    for r in rows:
        if r.get("ret"):
            continue
        day = r.get("depart")
        if not day:
            continue
        if day not in out or r["price"] < out[day]["price"]:
            out[day] = r
    return out


def _months(*days):
    """Месяцы, которые надо запросить, чтобы покрыть эти даты и их окрестности."""
    out = []
    for day in days:
        d = _d(day) if isinstance(day, str) else day
        if not d:
            continue
        for shift in (-C.TRIP_FLEX_DAYS, 0, C.TRIP_FLEX_DAYS):
            ym = (d + timedelta(days=shift)).strftime("%Y-%m")
            if ym not in out:
                out.append(ym)
    return out


async def lookup(session, origin, dest, depart, ret=None, progress=None):
    """
    Всё про один маршрут. Возвращает словарь с тремя ответами.

    Соседние даты считаются из месячных календарей: они всё равно нужны,
    а лишних запросов не требуют.
    """
    def say(text):
        if progress:
            progress(text)

    there = tp.Travelpayouts(session, origin)
    back = tp.Travelpayouts(session, dest)

    say("цена туда")
    out_rows = await there.exact(dest, depart)
    out_best = _cheapest(out_rows)

    round_best = back_best = None
    if ret:
        say("цена обратно")
        back_rows = await back.exact(origin, ret)
        back_best = _cheapest(back_rows)
        say("туда-обратно одним билетом")
        round_best = _cheapest(await there.exact(dest, depart, ret))

    say("соседние даты")
    out_cal, back_cal = {}, {}
    for ym in _months(depart):
        out_cal.update(_by_date(await there.calendar(dest, ym)))
    if ret:
        for ym in _months(ret):
            back_cal.update(_by_date(await back.calendar(origin, ym)))

    res = {
        "origin": origin, "dest": dest, "depart": depart, "ret": ret,
        "out": out_best, "back": back_best, "round": round_best,
        "split": None, "saving": None,
        "alternatives": _alternatives(out_cal, back_cal, depart, ret),
        "link": tp.search_link(origin, dest, depart, ret),
    }

    if out_best and (back_best or not ret):
        res["split"] = out_best["price"] + (back_best["price"] if back_best else 0)
    if res["split"] and round_best:
        res["saving"] = round_best["price"] - res["split"]
    return res


def _alternatives(out_cal, back_cal, depart, ret, limit=None):
    """
    Соседние даты, отсортированные по сумме. Считаем как два билета
    в одну сторону: так сравнимо и с обычным поиском, и между собой.
    """
    limit = limit or C.TRIP_ALT_LIMIT
    d0, r0 = _d(depart), _d(ret) if ret else None
    if not d0:
        return []

    outs = [(day, r) for day, r in out_cal.items()
            if _d(day) and abs((_d(day) - d0).days) <= C.TRIP_FLEX_DAYS]
    if not outs:
        return []

    if not r0:
        outs.sort(key=lambda x: x[1]["price"])
        return [{"depart": day, "ret": None, "total": r["price"],
                 "out": r, "back": None} for day, r in outs[:limit]]

    backs = [(day, r) for day, r in back_cal.items()
             if _d(day) and abs((_d(day) - r0).days) <= C.TRIP_FLEX_DAYS]
    want = (r0 - d0).days            # сколько ночей человек собрался пробыть

    combos = []
    for od, orow in outs:
        for bd, brow in backs:
            nights = (_d(bd) - _d(od)).days
            # поездку сдвигаем, но не подменяем: возврат в день вылета
            # и лишние сутки в чужом городе — это уже другая поездка
            if nights < 1 or abs(nights - want) > C.TRIP_LEN_TOL:
                continue
            combos.append({"depart": od, "ret": bd, "nights": nights,
                           "total": orow["price"] + brow["price"],
                           "out": orow, "back": brow})
    combos.sort(key=lambda c: c["total"])
    return combos[:limit]
