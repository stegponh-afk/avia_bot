"""
Настройки подписчика и решение «слать ли ему эту находку».

Настроек у человека четыре, и все понятны без объяснений: откуда летает,
что присылать, бюджет и когда хочет лететь. Всё остальное — пороги,
источники, оформление — решает бот или админ.
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
    m = mode_of(s)
    if m == "off":
        return False
    if m == "budget" and budget_of(s):
        return True
    disc = d.get("discount") or 0
    if "связка" in (d.get("why") or []):
        return disc >= (C.SUPER_PCT if m == "super" else C.COMBO_MIN_SAVE_PCT)
    return disc >= (C.SUPER_PCT if m == "super" else C.DEAL_PCT)
