"""
Кнопки. Только разметка, вся логика в handlers/.

Правило одно: на каждом экране — только то, что нужно прямо сейчас.
Внизу всегда три кнопки, остальное появляется по ходу дела.
"""
from datetime import date

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)

import config as C
import db
import hotels
import places
import render
import tp
import users

# нижнее меню — по этим подписям ловим нажатия
DEALS    = "🔥 Скидки"
SEARCH   = "🔍 Найти билет"
SETTINGS = "⚙️ Настройки"
WATCH    = "🔔 Мои направления"
ADMIN    = "🛠 Админка"          # только у владельцев

# Кнопки прошлых версий. У людей они остаются на экране, пока бот не пришлёт
# новую клавиатуру, поэтому нажатия на них понимаем и заодно меню обновляем.
LEGACY = {
    "🔎 Найти сейчас": "deals", "🔀 Связки": "deals",
    "🏆 Топ за сутки": "cheap", "🌍 По странам": "cheap",
    "📍 Куда": "search", "🎫 Маршрут": "search",
    "💰 Цена": "settings", "📅 Даты": "settings", "🛫 Откуда": "settings",
}

# запасные списки, пока не накопилась своя статистика
POPULAR = ["MOW", "LED", "AER", "IST", "DXB", "TAS", "AYT", "MRV", "BAK", "EVN"]
ORIGINS = ["KZN", "MOW", "LED", "SVX", "OVB", "KUF", "UFA", "ROV", "KRR", "GOJ"]

BUDGETS = [5000, 10000, 20000, 40000]
MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
               "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def _b(text, data):
    if data.startswith(("http://", "https://")):
        return InlineKeyboardButton(text=text, url=data)
    return InlineKeyboardButton(text=text, callback_data=data)


def _grid(buttons, per_row=2):
    rows, row = [], []
    for b in buttons:
        row.append(b)
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _mark(on, text):
    return ("✅ " if on else "") + text


# ---------- нижнее меню ----------

def main(chat_id=None):
    """
    Четыре кнопки: что выгодно сейчас, свои направления, найти билет, настройки.
    Владельцу (chat_id из AVIA_OWNER_IDS) — пятая, «🛠 Админка».
    """
    # is_persistent не ставим: он убирает кнопку сворачивания, и половина
    # экрана оказывается занята меню без возможности его убрать
    rows = [[KeyboardButton(text=DEALS), KeyboardButton(text=WATCH)],
            [KeyboardButton(text=SEARCH), KeyboardButton(text=SETTINGS)]]
    if chat_id in C.OWNER_IDS:
        rows.append([KeyboardButton(text=ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Куда летим? Например: Сочи 18.10")


# ---------- знакомство ----------

def cities(prefix, cur=None):
    """Города вылета кнопками + «другой». prefix — куда вернуть ответ."""
    rows = _grid([_b(_mark(cur == c, places.name(c)), f"{prefix}:{c}") for c in ORIGINS])
    rows.append([_b("✏️ Другой город", f"{prefix}:other")])
    return rows


def onboarding():
    return _kb(cities("start"))


# ---------- лента ----------

def feed(tab, items):
    """Вкладки «скидки / дешевле всего» и по кнопке на каждую находку."""
    rows = [[_b(_mark(tab == "deals", "🔥 Скидки"), "feed:deals"),
             _b(_mark(tab == "cheap", "💰 Дешевле всего"), "feed:cheap")]]
    buttons = []
    for i, (label, data) in enumerate(items[:8], 1):
        buttons.append(_b(f"{i}. {label}", data))
    rows += _grid(buttons)
    return _kb(rows)


def morning(items):
    """Под утренней сводкой: по кнопке на находку — откроет карточку."""
    return _kb(_grid([_b(f"{i}. {places.name(d['dest'])} −{d['discount']}%"
                         if d.get("discount") else f"{i}. {places.name(d['dest'])}",
                         f"deal:{d['origin']}:{d['dest']}")
                      for i, d in enumerate(items, 1)]))


def deal_kb(d, sub="bot"):
    """Под находкой: купить и посмотреть другие даты. sub — метка источника в ссылке."""
    rows = []
    if d.get("legs"):          # у связки покупаются два билета, значит две кнопки
        for i, leg in enumerate(d["legs"], 1):
            if leg.get("link"):
                rows.append([_b(f"🎫 {i}) {places.name(leg['origin'])} → "
                                f"{places.name(leg['dest'])} · "
                                f"{render.money(leg['price'])}",
                                tp.buy_link(leg, sub))])
        if d.get("direct_link"):
            direct = {"origin": d["origin"], "dest": d["dest"], "depart": d.get("depart"),
                      "link": d["direct_link"]}
            rows.append([_b("✈️ Сравнить с одним билетом", tp.buy_link(direct, sub))])
        return _kb(rows)
    if d.get("link"):
        label = ("📣 Открыть пост" if d.get("src") == "tg"
                 else f"🎫 Купить за {render.money(d['price'])}")
        # пост из канала-источника — это ссылка на пост, а не на билет
        rows.append([_b(label, d["link"] if d.get("src") == "tg" else tp.buy_link(d, sub))])
    b = d.get("back")              # обратный билет — если карточка его нашла
    if b:
        back = {"origin": d["dest"], "dest": d["origin"], "depart": b["depart"],
                "link": b.get("link")}
        rows.append([_b(f"↩️ Обратно {render.when(b['depart'])} за "
                        f"{render.money(b['price'])}", tp.buy_link(back, sub + "_back"))])
    if d.get("stay"):              # отели в городе прилёта — если карточка их нашла
        rows.append([_b(hotels.label(d["stay"]), hotels.link(d["stay"], sub + "_hotel"))])
    dest = d.get("dest")
    if dest and dest != "?" and len(dest) == 3:
        rows.append([_b(f"📅 Все даты · {places.name(dest)}", f"dest:{dest}:{d['origin']}")])
    if d.get("watch_id"):
        rows.append([_b("🔕 Больше не следить", f"watch:del:{d['watch_id']}")])
    return _kb(rows)


# ---------- поиск ----------

def search(origin):
    """Куда можно нажать, не печатая: дешёвые по статистике + популярные."""
    codes = []
    try:
        for r in db.top_cheap(origin or C.ORIGIN, hours=48, limit=8):
            if r["dest"] and r["dest"] != "?" and r["dest"] not in codes:
                codes.append(r["dest"])
    except Exception:
        pass
    for c in POPULAR:
        if len(codes) >= 8:
            break
        if c not in codes and c != origin:
            codes.append(c)
    rows = _grid([_b(places.name(c), f"dest:{c}") for c in codes[:8]])
    rows.append([_b(WATCH, "watch:list")])
    return _kb(rows)


def dest_card(code, best, origin=None, own=True, watch_id=None):
    """
    Под календарём направления. Даты — сразу ссылки на покупку:
    нажал «14 окт · 4 300 ₽» и уже на сайте, без промежуточных экранов.

    own=False — направление не из своего города («Киров → Москва»): туда-
    обратно и пересадки считаются от города из настроек, их не показываем.
    """
    rows = _grid([_b(f"{render.when_wd(d['depart'])} · {render.money(d['price'])}",
                     tp.buy_link(d, "bot_calendar")) for d in best[:6]])
    if watch_id:
        rows.append([_b("🔕 Не следить за этим направлением", f"watch:del:{watch_id}")])
    elif origin:
        rows.append([_b("🔔 Следить за скидками здесь", f"watch:add:{origin}:{code}")])
    if own:
        rows.append([_b("🔁 Туда и обратно — выбрать даты", f"rt:{code}")])
        rows.append([_b("🔀 Поискать дешевле с пересадкой", f"combo:{code}")])
    return _kb(rows)


def watch_list(ws):
    """Свои направления: открыть календарь или перестать следить."""
    rows = [[_b(f"📍 {places.name(w['origin'])} → {places.name(w['dest'])}",
                f"dest:{w['dest']}:{w['origin']}"),
             _b("🔕 Убрать", f"watch:rm:{w['id']}")] for w in ws]
    if len(ws) < C.WATCH_MAX:
        rows.append([_b("➕ Добавить направление", "watch:new")])
    return _kb(rows)


def trip_kb(res):
    """Под ответом о маршруте: купить каждое плечо и соседние даты."""
    rows = []
    if res.get("round") and res["round"].get("link"):
        rows.append([_b("🎫 Туда-обратно одним билетом",
                        tp.buy_link(res["round"], "bot_trip"))])
    if res.get("out") and res["out"].get("link"):
        rows.append([_b(f"🎫 Туда · {places.name(res['dest'])}",
                        tp.buy_link(res["out"], "bot_trip"))])
    if res.get("back") and res["back"].get("link"):
        rows.append([_b(f"🎫 Обратно · {places.name(res['origin'])}",
                        tp.buy_link(res["back"], "bot_trip"))])

    # Соседние даты — это ПЕРЕСЧЁТ, а не ссылка на покупку. Раньше кнопка вела
    # на ссылку первого плеча, а подпись показывала сумму за два билета:
    # человек жал «8 843 ₽» и попадал на билет в одну сторону за 4 300.
    for a in (res.get("alternatives") or [])[:3]:
        if a["total"] >= (res.get("split") or 10 ** 9):
            continue
        label = (f"{render.when(a['depart'])} → {render.when(a['ret'])}"
                 if a.get("ret") else render.when(a["depart"]))
        rows.append([_b(f"📅 {label} · {render.money(a['total'])}",
                        f"trip:{res['origin']}:{res['dest']}:"
                        f"{a['depart']}:{a.get('ret') or ''}")])

    rows.append([_b("🔀 Поискать дешевле с пересадкой", f"combo:{res['dest']}")])
    return _kb(rows)


# ---------- настройки ----------

def settings():
    return _kb([[_b("🛫 Откуда", "set:origin"), _b("🔔 Что присылать", "set:mode")],
                [_b("💰 Бюджет", "set:budget"), _b("📅 Когда", "set:dates")],
                [_b("🎨 Оформление", "set:style")]])


BACK = [_b("⬅️ Назад", "set:home")]


def origin_menu(cur):
    return _kb(cities("origin", cur) + [BACK])


def alerts_menu(on):
    """
    Галочки «что присылать». Скидки — три кнопки в ряд, выбрана одна;
    остальное включается и выключается независимо. on — множество включённого.
    """
    lvl = "super" if "super" in on else "deals" if "deals" in on else "off"
    short = {"off": "🔕 Нет", "super": "🔥 Супер", "deals": "💸 Все"}   # три в ряд
    rows = [[_b(_mark(lvl == k, short[k]), f"al:lvl:{k}") for k in users.LEVELS]]
    rows += [[_b(("✅ " if k in on else "⬜ ") + f"{icon} {title}", f"al:t:{k}")]
             for k, (icon, title, _) in users.EXTRAS.items()]
    return _kb(rows + [BACK])


def style_menu(cur):
    return _kb([[_b(_mark(cur == "new", "✨ Новое"), "style:new"),
                 _b(_mark(cur == "old", "📄 Обычное"), "style:old")], BACK])


def budget_menu(cur):
    rows = _grid([_b(_mark(cur == v, f"до {render.money(v)}"), f"budget:{v}")
                  for v in BUDGETS])
    rows.append([_b(_mark(cur is None, "Без ограничения"), "budget:0")])
    rows.append([_b("✏️ Своя сумма", "budget:custom")])
    return _kb(rows + [BACK])


def dates_menu(cur):
    """Любые даты, конкретный месяц на полгода вперёд или свой период."""
    a, b = cur or (None, None)
    rows = [[_b(_mark(not a, "Любые даты"), "dates:any")]]
    y, m = date.today().year, date.today().month
    months = []
    for _ in range(6):
        ym = f"{y:04d}-{m:02d}"
        on = bool(a and b and a[:7] == ym and b[:7] == ym)
        # «сен 26» читается как 26 сентября — пишем месяц словом, год только чужой
        label = MONTH_NAMES[m - 1] + ("" if y == date.today().year else f" {y}")
        months.append(_b(_mark(on, label), f"dates:m:{ym}"))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    rows += _grid(months, 2)
    rows.append([_b("✏️ Свой период", "dates:custom")])
    return _kb(rows + [BACK])


# ---------- админ ----------

def admin(style):
    return _kb([[_b("🔎 Опросить сейчас", "admin:poll"),
                 _b("🔀 Искать связки", "admin:combo")],
                [_b("➕ Создать ссылку отслеживания", "links:new")],
                [_b("🔗 Ссылки", "links:list")],
                [_b("📣 Пример черновика для канала", "admin:example")],
                [_b("📌 Закреп с навигацией", "admin:nav"),
                 _b("🏆 Дайджест сейчас", "admin:digest")],
                [_b("🏖 «Куда на выходные» сейчас", "admin:weekend")],
                [_b(f"🎨 Оформление: {'новое' if style == 'new' else 'старое'}",
                    "admin:style")]])


# ---------- ссылки-отслеживания ----------

def links_list(items):
    """items — [(id, подпись)]: по кнопке на ссылку и «создать»."""
    rows = [[_b(label, f"links:show:{i}")] for i, label in items]
    rows.append([_b("➕ Создать ссылку отслеживания", "links:new")])
    return _kb(rows)


def link_card(link_id):
    return _kb([[_b("🔄 Обновить", f"links:upd:{link_id}"),
                 _b("⬅️ Все ссылки", "links:list")]])
