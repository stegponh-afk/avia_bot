"""
Что считать дешёвым и о чём писать.

Шесть независимых поводов. Каждый ловит то, что остальные пропускают:

  1. ✈️ коридор   — цена попала в заданные границы. Простой порог в рублях.
  2. 🔥 аномалия  — сильно ниже собственной истории маршрута. Сезонная:
                    декабрь сравнивается с декабрём, иначе новогодние цены
                    вечно выглядят завышенными. Нужна история за 3+ дня.
  3. 💎 выгодно   — дорого в рублях, но дёшево для такого расстояния.
                    Сравнение идёт с кривой по всем направлениям сразу,
                    поэтому работает с первого запуска и видит новые города.
  4. 📉 упало     — подешевело относительно вчерашней цены. Ловит начало
                    распродажи за часы, а не за дни.
  5. 🎯 выброс    — на эту дату резко дешевле, чем на соседние. Обычно это
                    ошибочный тариф или пустой рейс.
  6. 🔀 связка    — два отдельных билета через третий город дешевле прямого.
                    Считается в combo.py, сюда приходит готовым предложением.

Плюс пометка 🌍 «дешевле всех городов страны» — не повод, а уточнение:
самый дешёвый город есть у каждой страны, как признак это был бы шум.

Человеку все шесть поводов не нужны. Ему нужна одна цифра — скидка к обычной
цене, как «суперскидка 96%» на Aviasales. Её считает discount(): берёт самую
надёжную из обычных цен, что нашлась, и сравнивает с ней.

И дедуп, иначе бот превратится в ленту одинаковых сообщений.
"""
from datetime import datetime

import baseline
import config as C
import db
import places
import users


# ---------- мелкая статистика ----------

def percentile(sorted_vals, p):
    """p-й перцентиль по уже отсортированному списку."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def median(vals):
    s = sorted(vals)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _date(s):
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


# ---------- дедуп ----------

def key(d):
    """
    Ключ дедупа — город вылета и направление, без даты.

    С датой в ключе один и тот же рейс за одну и ту же цену прилетает шестью
    сообщениями на шесть разных дней. Человеку нужно одно сообщение на город
    с лучшей ценой; остальные даты он посмотрит кнопкой «Ещё в этот город».
    """
    return f"{d['origin']}-{d['dest']}"


def dedup(deals):
    """Из пачки предложений оставляем по одному самому дешёвому на направление."""
    best = {}
    for d in deals:
        k = key(d)
        if k not in best or d["price"] < best[k]["price"]:
            best[k] = d
    return list(best.values())


def dedup_dates(deals):
    """Для календаря по одному направлению: лучшая цена на каждую дату."""
    best = {}
    for d in deals:
        k = (d["origin"], d["dest"], d.get("depart"))
        if k not in best or d["price"] < best[k]["price"]:
            best[k] = d
    return list(best.values())


# ---------- контекст прогона ----------

def neighbour_medians(deals):
    """
    Для каждого (город вылета, направление, прямой ли, дата) — медиана
    самых дешёвых цен на соседние даты.

    Считается по текущему прогону, а не по истории: в одном проходе у нас есть
    все месяцы сразу, и этого достаточно, чтобы увидеть провал на одном числе.
    Прямые считаем отдельно — по той же причине, что и в истории: прямой
    рейс по вторникам за 10 000 не «выброс» среди пересадок за 40 000.
    """
    by_dir = {}
    for d in deals:
        day = _date(d.get("depart") or "")
        if not day:
            continue
        kinds = [False] + ([True] if d.get("transfers") == 0 else [])
        for direct in kinds:
            slot = by_dir.setdefault((d["origin"], d["dest"], direct), {})
            if day not in slot or d["price"] < slot[day]:
                slot[day] = d["price"]

    # даты по порядку и окно ±N дней: с обходом у направления до 300 дат,
    # и перебор «каждая с каждой» стал бы заметно медленным
    out = {}
    for (origin, dest, direct), days in by_dir.items():
        order = sorted(days)
        for i, day in enumerate(order):
            near = []
            for j in range(max(0, i - C.OUTLIER_WINDOW), min(len(order), i + C.OUTLIER_WINDOW + 1)):
                other = order[j]
                if other != day and abs((other - day).days) <= C.OUTLIER_WINDOW:
                    near.append(days[other])
            if len(near) >= C.OUTLIER_MIN_NEIGH:
                out[(origin, dest, direct, day)] = (median(near), len(near))
    return out


def country_bests(deals):
    """Самое дешёвое направление в каждой стране по текущему прогону."""
    by_country = {}
    for d in deals:
        c = places.country(d["dest"]) if d["dest"] != "?" else None
        if not c:
            continue
        slot = by_country.setdefault((d["origin"], c), {})
        if d["dest"] not in slot or d["price"] < slot[d["dest"]]:
            slot[d["dest"]] = d["price"]

    out = {}
    for (origin, c), cities in by_country.items():
        if len(cities) >= C.COUNTRY_MIN_CITIES:
            best = min(cities, key=cities.get)
            out[(origin, c)] = (best, cities[best], len(cities))
    return out


def corridor():
    """
    Коридор для повода ✈️ — самый широкий бюджет среди тех, кто просил
    «всё дешевле бюджета».

    Сбор идёт один на всех, а бюджет у каждого свой и применяется при
    отправке. Остальным коридор не нужен: им пишут про скидки.
    """
    hi = 0
    try:
        for s in db.active_subs():
            if "budget" in users.alerts_of(s) and users.budget_of(s):
                hi = max(hi, users.budget_of(s))
    except Exception:
        pass
    return 0, hi or C.PRICE_MAX


# ---------- сами признаки ----------

def stats(origin, dest, month=None, exclude=None, direct=False):
    """
    Обычная цена маршрута: (рубли, по скольким датам, сезонная ли).

    «Обычная» — нижняя четверть самых дешёвых цен по другим датам вылета,
    а не середина. Скидкой называем только то, что дешевле даже дешёвых
    дней: так «−60%» значит ровно это, а не удачное расписание.

    Если по нужному месяцу набралось достаточно дат — считаем по нему,
    это и есть сезонность. Иначе откатываемся на всю историю.

    Статистикой считаем только историю, набранную за несколько разных дней.
    Иначе первый прогон, залив три сотни цен за минуту, объявит аномалией
    любую из них.
    """
    if db.history_days(origin, dest, C.ANOMALY_WINDOW_DAYS) < C.ANOMALY_MIN_DAYS:
        return None, 0, False

    seasonal = False
    h = []
    if C.SEASON_ENABLED and month:
        h = db.history(origin, dest, C.ANOMALY_WINDOW_DAYS, month=month,
                       exclude=exclude, direct=direct)
        seasonal = len(h) >= C.SEASON_MIN_POINTS
    if not seasonal:
        h = db.history(origin, dest, C.ANOMALY_WINDOW_DAYS,
                       exclude=exclude, direct=direct)

    if len(h) < C.ANOMALY_MIN_POINTS:
        return None, len(h), seasonal
    return percentile(h, C.ANOMALY_PCTL), len(h), seasonal


def classify(d, ctx):
    """
    Навешивает на предложение поводы для оповещения.
    Возвращает список причин: [] значит писать не о чем.
    """
    price = d["price"]
    origin, dest = d["origin"], d["dest"]
    why = []
    lo, hi = ctx["corridor"]

    if lo <= price <= hi:
        why.append("коридор")

    # --- 🔥 своя история маршрута, с поправкой на месяц вылета ---
    if C.ANOMALY_ENABLED and price <= C.ANOMALY_HARD_CAP:
        month = (d.get("depart") or "")[5:7] or None
        direct = d.get("transfers") == 0
        usual, n, seasonal = stats(origin, dest, month,
                                   exclude=d.get("depart"), direct=direct)
        if usual and price <= usual * (1 - C.ANOMALY_MIN_DROP / 100):
            d["hist_usual"] = usual
            d["history_n"] = n
            d["seasonal"] = seasonal
            d["hist_direct"] = direct
            why.append("аномалия")

    # --- 💎 дорого в рублях, но дёшево для расстояния ---
    if C.VALUE_ENABLED and price <= C.VALUE_PRICE_CAP:
        km = places.distance(dest) if dest != "?" else None
        r = baseline.ratio(origin, price, km)
        if r is not None and r <= C.VALUE_RATIO:
            d["expected"] = baseline.expected(origin, km)
            d["km"] = km
            why.append("выгодно")

    # --- 📉 подешевело со вчера ---
    if C.DROP_ENABLED:
        prev = db.min_price_between(origin, dest, C.DROP_FROM_H, C.DROP_TO_H,
                                    depart=d.get("depart"))
        if prev and price <= prev * (1 - C.DROP_PCT / 100):
            d["was"] = prev
            why.append("упало")

    # --- 🎯 провал на одной дате относительно соседних ---
    if C.OUTLIER_ENABLED:
        day = _date(d.get("depart") or "")
        near = (ctx["neighbours"].get((origin, dest, d.get("transfers") == 0, day))
                if day else None)
        if near and near[0] and price <= near[0] * (1 - C.OUTLIER_PCT / 100):
            d["near_median"] = near[0]
            d["near_n"] = near[1]
            why.append("выброс")

    # --- 🌍 пометка, а не повод ---
    if C.COUNTRY_ENABLED:
        c = places.country(dest) if dest != "?" else None
        best = ctx["countries"].get((origin, c)) if c else None
        if best and best[0] == dest:
            d["country"] = c
            d["country_cities"] = best[2]

    # --- 🔀 связка через третий город, посчитана в combo.py ---
    if C.COMBO_ENABLED and d.get("src") == "combo":
        why.append("связка")

    if d.get("src") == "tg" and lo <= price <= hi:
        why.append("канал")
    if d.get("src") == "airline":
        why.append("акция")

    return why


# С чем сравнивать, по убыванию надёжности. История маршрута — лучшее, что
# есть: это буквально «обычно сюда летают за столько». Соседние даты честны,
# но узки. Вчерашняя цена говорит о падении, а не об обычной цене.
#
# Кривой расстояний здесь нет намеренно. Проверено на живых данных: она
# объявляла «−79%» на Сочи за 2 868 ₽ — цену хорошую, но обычную для
# межсезонья. Кривая годится, чтобы заметить маршрут, но не чтобы назвать
# процент: одна формула на все направления не знает, где летают лоукостеры.
BASIS = (("аномалия", "hist_usual"), ("выброс", "near_median"), ("связка", "direct_price"),
         ("упало", "was"))


def discount(d):
    """
    Скидка к обычной цене: кладёт в предложение discount (%), usual (₽)
    и basis (с чем сравнивали). Без скидки — всё None.

    Берём первое основание по надёжности, а не самое щедрое: «−70% к кривой
    расстояний» звучит громче, но «−30% к обычной цене этого маршрута» — правда.
    """
    why = d.get("why") or []
    for reason, field in BASIS:
        ref = d.get(field)
        if reason in why and ref and ref > d["price"]:
            d["discount"] = round((1 - d["price"] / ref) * 100)
            d["usual"] = int(ref)
            d["basis"] = reason
            return d["discount"]
    d["discount"] = d["usual"] = d["basis"] = None
    return None


def worth_sending(d):
    """
    Писали ли уже про это направление.
    Повторяем, только если цена заметно упала.
    """
    prev = db.was_sent(key(d))
    if prev is None:
        return True
    return d["price"] <= prev * (1 - C.RESEND_DROP_PCT / 100)


# ---------- главная ----------

def evaluate(deals):
    """
    Разметить всю пачку: у каждого направления — поводы и скидка.
    Ничего не отправляет; результат идёт и в ленту 🔥, и в рассылку.

    Сравниваем с историей ДО того, как влили в неё текущий прогон,
    иначе предложение сравнивается само с собой.
    """
    deals = [d for d in deals if d.get("price")]
    if not deals:
        return []

    # Хабы опрашиваются только ради связок: цены из них нужны в истории,
    # но поводом написать быть не должны. Иначе дешёвый Стамбул → Анталья
    # съедает лимит сообщений, и настоящая находка до человека не доходит.
    watched = set(db.origins())

    # соседние даты и минимумы по странам считаем по всей пачке,
    # до дедупа: после него от каждого направления останется одна строка
    ctx = {
        "corridor": corridor(),
        "neighbours": neighbour_medians(deals) if C.OUTLIER_ENABLED else {},
        "countries": country_bests(deals) if C.COUNTRY_ENABLED else {},
    }

    out = []
    for d in dedup(deals):
        if d["origin"] not in watched:
            continue
        why = classify(d, ctx)
        if not why:
            continue
        d["why"] = why
        discount(d)
        out.append(d)

    # связки в историю не пишем: это сумма двух билетов, а не цена рейса.
    # Попади они в статистику — испортят и медианы, и кривую расстояний.
    db.save_prices([d for d in deals if d.get("src") not in ("combo", "tp/sweep")])
    db.save_daily([d for d in deals if d.get("src") == "tp/sweep"])
    return out


def select(found):
    """
    Из размеченного — то, о чём ещё не писали, самые большие скидки первыми.

    Лимит считаем на каждый город вылета отдельно: иначе город с дешёвыми
    билетами забирает всю квоту, а второй подписчик не получает ничего.
    Сколько из этого дойдёт до конкретного человека, решает рассылка.
    """
    out = [d for d in found if worth_sending(d)]
    out.sort(key=lambda x: (-(x.get("discount") or 0), x["price"]))
    per_origin, limited = {}, []
    for d in out:
        n = per_origin.get(d["origin"], 0)
        if n >= C.MAX_ALERTS_PER_RUN:
            continue
        per_origin[d["origin"]] = n + 1
        limited.append(d)
    for d in limited:
        db.mark_sent(key(d), d["price"])
    db.forget_old()
    return limited


def pick(deals):
    """Разметить и отобрать за один вызов — для постов из каналов."""
    return select(evaluate(deals))
