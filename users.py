"""
Настройки подписчика и решение «слать ли ему эту находку».

Настройки понятны без объяснений: откуда летает, что присылать, бюджет,
когда хочет лететь и как выглядят сообщения. Пороги и источники решает
бот или админ.

«Что присылать» — набор галочек, их можно сочетать как угодно:
уровень скидок (нет / только суперскидки / все), «дешевле бюджета»,
«мои направления» и «куда на выходные». Хранится строкой в subs.alerts:
«deals,watch». Пусто — не присылать ничего.
"""
import config as C
import db
import tp

# что присылать: (значок, название, пояснение)
MODES = {
    "super":  ("🔥", "Только суперскидки", f"от {C.SUPER_PCT}%, редко, но метко"),
    "deals":  ("💸", "Все скидки", f"от {C.DEAL_PCT}% к обычной цене"),
    "budget": ("✈️", "Всё дешевле бюджета", "любой билет в пределах бюджета"),
    "off":    ("🔕", "Ничего", "загляну сам"),
}

# уровень скидок — один из трёх; остальное — независимые галочки
LEVELS = {
    "off":   ("🔕", "Без скидок", ""),
    "super": ("🔥", "Суперскидки", f"от {C.SUPER_PCT}%, редко, но метко"),
    "deals": ("💸", "Все скидки", f"от {C.DEAL_PCT}% к обычной цене маршрута"),
}
EXTRAS = {
    "budget":  ("✈️", "Дешевле бюджета", "любой билет в пределах бюджета, даже без скидки"),
    "watch":   ("🔔", "Мои направления", "когда дешевеет маршрут, за которым ты следишь"),
    "weekend": ("🏖", "Куда на выходные", "по четвергам — поездки на выходные из твоего города"),
}

# старый режим -> галочки: у кого выбор был сделан до галочек, он сохраняется
_FROM_MODE = {"super": {"super", "watch"}, "deals": {"deals", "watch"},
              "budget": {"budget", "watch"}, "off": set()}


def alerts_of(s):
    """Включённые уведомления человека: множество из LEVELS (кроме off) и EXTRAS."""
    if not s:
        return set(_FROM_MODE.get(C.MODE, {"deals", "watch"}))
    if s["alerts"] is not None:
        return {x for x in s["alerts"].split(",") if x}
    if not s["active"]:
        return set()
    return set(_FROM_MODE.get(mode_of(s), {"deals", "watch"}))


def alerts(chat_id):
    return alerts_of(get(chat_id))


def level_of(s):
    a = alerts_of(s)
    return "super" if "super" in a else "deals" if "deals" in a else "off"


def summary(s):
    """«💸 все скидки · 🔔 мои направления» — одной строкой для экранов."""
    a = alerts_of(s) if (not s or s["active"]) else set()
    parts = []
    lvl = level_of(s) if a else "off"
    if lvl != "off":
        icon, title, _ = LEVELS[lvl]
        parts.append(f"{icon} {title.lower()}")
    for k, (icon, title, _) in EXTRAS.items():
        if k in a:
            parts.append(f"{icon} {title.lower()}")
    return " · ".join(parts) or "🔕 ничего — загляну сам"


def get(chat_id):
    return db.get_sub(chat_id)


def origin(chat_id):
    s = get(chat_id)
    return s["origin"] if s and s["origin"] else C.ORIGIN


def budget_of(s):
    """Потолок цены или None, если без ограничения."""
    if not s or s["pmax"] is None or s["pmax"] >= C.NO_LIMIT:
        return None
    return s["pmax"]


def budget(chat_id):
    return budget_of(get(chat_id))


def mode_of(s):
    """
    Режим подписчика.

    У записей, заведённых до появления режимов, поле пустое. Им выводим
    режим из того, что они настраивали раньше: задан коридор цен — значит
    хотели «всё дешевле N», не задан — обычные скидки.
    """
    if not s:
        return C.MODE
    if not s["active"]:
        return "off"
    if s["mode"]:
        return s["mode"]
    return "budget" if budget_of(s) else C.MODE


def mode(chat_id):
    return mode_of(get(chat_id))


def dates(chat_id):
    s = get(chat_id)
    if not s:
        return C.DATE_FROM or None, C.DATE_TO or None
    return s["date_from"], s["date_to"]


def style(chat_id):
    s = get(chat_id)
    return s["style"] if s and s["style"] else C.STYLE


def months(chat_id, default_n=None):
    """
    Какие месяцы проверять для этого человека.

    Задано окно дат — берём его месяцы, иначе ближайшие несколько.
    Искать связку в июле, когда человек летит в декабре, бессмысленно.
    """
    a, b = dates(chat_id)
    if not (a and b):
        return tp.months_ahead(default_n or C.COMBO_SCAN_MONTHS)
    out, (y, m) = [], (int(a[:4]), int(a[5:7]))
    while f"{y:04d}-{m:02d}" <= b[:7] and len(out) < 6:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out or tp.months_ahead(default_n or C.COMBO_SCAN_MONTHS)


def in_window(depart, date_from, date_to):
    """Попадает ли вылет в окно дат."""
    if not (date_from or date_to) or not depart:
        return True
    if date_from and depart < date_from:
        return False
    if date_to and depart > date_to:
        return False
    return True


def fits(s, d):
    """Город вылета, даты и бюджет — общие фильтры и для рассылки, и для ленты."""
    if d["origin"] != (s["origin"] if s and s["origin"] else C.ORIGIN):
        return False
    if s and not in_window(d.get("depart"), s["date_from"], s["date_to"]):
        return False
    b = budget_of(s)
    return not (b and d["price"] > b)


def wants(s, d):
    """
    Присылать ли находку этому человеку.

    Бюджет — жёсткий потолок для всех режимов: «до 10 000» значит, что
    билет за 15 000 не нужен даже со скидкой. Связкам порог свой, ниже:
    экономия в 20% на двух билетах — уже хорошая новость.
    """
    if not fits(s, d):
        return False
    a = alerts_of(s)
    if "budget" in a and budget_of(s):
        return True
    m = level_of(s)
    if m == "off":
        return False
    disc = d.get("discount") or 0
    if "связка" in (d.get("why") or []):
        return disc >= (C.SUPER_PCT if m == "super" else C.COMBO_MIN_SAVE_PCT)
    return disc >= (C.SUPER_PCT if m == "super" else C.DEAL_PCT)
