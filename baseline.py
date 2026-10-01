"""
Ожидаемая цена для расстояния.

Отвечает на вопрос «дёшево ли это для такого перелёта», когда истории по
маршруту ещё нет. Логика простая: по минимальным ценам всех известных
направлений подгоняем кривую цена ≈ a · км^b и смотрим, насколько конкретное
предложение лежит ниже неё.

На живых данных из Казани кривая выходит почти линейной — около 10 ₽/км, —
и отклонения осмысленные: Дубай за 17 286 это 0.55 от нормы для своих 3415 км,
а Батуми за 41 687 — 2.73, то есть втрое дороже обычного для такого плеча.

Зачем это нужно отдельно от «аномалии»: аномалия сравнивает маршрут сам с собой
и потому слепа к направлению, которое видит впервые. Здесь сравнение идёт со
всеми остальными направлениями сразу, так что работает с первого запуска.
"""
import math
import time

import config as C
import db
import places

# кривая своя для каждого города вылета: из Москвы летают дешевле, чем из Казани
_fits = {}


def fit(origin, force=False):
    """Подгонка методом наименьших квадратов в логарифмах. Результат кэшируется."""
    f = _fits.setdefault(origin, {"a": None, "b": None, "n": 0, "at": 0.0})
    if not force and time.time() - f["at"] < C.VALUE_FIT_TTL_MIN * 60:
        return f

    pts = []
    for dest, price in db.min_by_direction(origin, C.VALUE_WINDOW_DAYS):
        if not price or dest == "?":
            continue
        km = places.distance(dest)
        if km and km >= C.VALUE_MIN_KM:
            pts.append((km, price))

    f["at"] = time.time()
    f["n"] = len(pts)
    if len(pts) < C.VALUE_MIN_DIRECTIONS:
        f["a"] = f["b"] = None            # данных мало, кривую не строим
        return f

    xs = [math.log(k) for k, _ in pts]
    ys = [math.log(p) for _, p in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:                          # все направления на одном расстоянии
        f["a"] = f["b"] = None
        return f

    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    f["b"] = b
    f["a"] = my - b * mx
    return f


def expected(origin, km):
    """Сколько обычно стоит перелёт на такое расстояние. None, если не судим."""
    if not km:
        return None
    f = fit(origin)
    if f["a"] is None:
        return None
    return math.exp(f["a"] + f["b"] * math.log(km))


def ratio(origin, price, km):
    """Во сколько раз предложение дешевле обычного: 0.5 = вдвое дешевле нормы."""
    exp = expected(origin, km)
    if not exp:
        return None
    return price / exp


def describe(origin):
    """Строка для диагностики: какая сейчас кривая."""
    f = fit(origin)
    if f["a"] is None:
        return (f"{origin}: кривая не построена, направлений {f['n']}, "
                f"нужно {C.VALUE_MIN_DIRECTIONS}")
    return (f"{origin}: цена ≈ {math.exp(f['a']):.1f} · км^{f['b']:.2f} "
            f"(по {f['n']} направлениям)")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for origin in db.origins():
        print(describe(origin))
        for km in (500, 1000, 2000, 3500, 6000):
            e = expected(origin, km)
            print(f"  {km:>5} км -> обычно {e:>8.0f} ₽" if e
                  else f"  {km:>5} км -> нет оценки")
        print()
