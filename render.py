"""
Как предложение выглядит в сообщении. Только оформление, без логики.

Общий приём один: заголовок, линия, дальше смысловые блоки через пустую
строку. Раньше всё шло сплошняком, и глазу не за что было зацепиться —
теперь цена отдельно, маршрут отдельно, причины отдельно.
"""
from datetime import datetime
from html import escape

import config as C
import places
import tp
import ui

MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн",
          "июл", "авг", "сен", "окт", "ноя", "дек")
MONTHS_OF = ("января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря")

DIGITS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣")
INDENT = "        "          # подверстка второй строки плеча


def money(v):
    return f"{int(v):,}".replace(",", " ") + " ₽"


def km_str(v):
    return f"{int(v):,}".replace(",", " ") + " км"


def plural(n, one, few, many):
    """61 наблюдение / 62 наблюдения / 65 наблюдений."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def when(s):
    """'2026-10-14' -> '14 окт'."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s[:10])
        return f"{d.day} {MONTHS[d.month - 1]}"
    except Exception:
        return s


def month_name(mm):
    """'12' -> 'декабря'."""
    try:
        return MONTHS_OF[int(mm) - 1]
    except Exception:
        return None


def clock(s):
    """'2026-11-16T12:50:00+03:00' -> '12:50'."""
    try:
        return datetime.fromisoformat(s).strftime("%H:%M")
    except Exception:
        return ""


def hours(h):
    """5.35 -> '5 ч 20 мин'."""
    total = int(round(h * 60))
    return f"{total // 60} ч {total % 60:02d} мин" if total % 60 else f"{total // 60} ч"


def transfers(n):
    if n is None:
        return None
    n = int(n)
    if n == 0:
        return "прямой"
    return f"{n} пересадка" if n == 1 else f"{n} пересадки"


def stops(d):
    """«прямой», «пересадка: Стамбул», «2 пересадки: Стамбул, Ташкент» или «1 пересадка»."""
    v = tp.via(d)
    if v:
        names = ", ".join(places.name(c) for c in v)
        if len(v) == 1:
            return f"пересадка: {names}"
        return f"{len(v)} {plural(len(v), 'пересадка', 'пересадки', 'пересадок')}: {names}"
    if v == [] and d.get("transfers") in (None, 0):
        return "прямой"
    return transfers(d.get("transfers"))


def price_range(lo, hi):
    """«0 — 10 000 ₽» читается хуже, чем «до 10 000 ₽»."""
    if not lo:
        return f"до {money(hi)}"
    if hi >= 1000000:
        return f"от {money(lo)}"
    return f"{money(lo)} — {money(hi)}"


def dates(date_from, date_to):
    """Окно дат человеческим языком."""
    if not (date_from or date_to):
        return "любые даты"
    if date_from and date_to:
        return f"{when(date_from)} — {when(date_to)}"
    return f"с {when(date_from)}" if date_from else f"до {when(date_to)}"


# ---------- предложение ----------

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS_IN = ("январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре")


def when_wd(s):
    """'2026-10-14' -> '14 окт, ср'. День недели решает не меньше даты."""
    try:
        d = datetime.fromisoformat(s[:10])
        return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"
    except Exception:
        return when(s)


def headline(d):
    """
    Заголовок находки одной фразой — то, что человек видит в уведомлении.

    Внутри бота шесть разных поводов, но человеку нужна одна цифра:
    насколько дешевле обычного. Отсюда и язык «суперскидок» Aviasales.
    """
    disc = d.get("discount")
    if d.get("legs"):
        return f"🔀 <b>Дешевле на {disc}% с пересадкой</b>"
    if disc and disc >= C.SUPER_PCT:
        return f"🔥 <b>Суперскидка {disc}%</b>"
    if disc:
        return f"💸 <b>Скидка {disc}%</b>"
    return "✈️ <b>Билет в твоём бюджете</b>"


def price_line(price, usual=None):
    """«12 069 ₽  26 000 ₽» со второй зачёркнутой — как на витрине."""
    out = f"<b>{money(price)}</b>"
    if usual and usual > price:
        out += f"  <s>{money(usual)}</s>"
    return out


def flight_line(d):
    """Дата, время, пересадки, авиакомпания — всё, что решает «подходит ли»."""
    bits = []
    if d.get("depart"):
        t = clock(d.get("depart_at") or "")
        bits.append(when_wd(d["depart"]) + (f", {t}" if t else ""))
    if d.get("ret"):
        bits.append("обратно " + when_wd(d["ret"]))
    bits.append(stops(d))
    if d.get("airline"):
        bits.append(escape(str(d["airline"])))
    return " · ".join(b for b in bits if b)


def basis_line(d):
    """
    Откуда взялась «обычная цена». Без этой строки скидка — просто слово:
    человек должен видеть, что зачёркнутая сумма не выдумана.
    """
    b, usual = d.get("basis"), d.get("usual")
    if not (b and usual):
        return None
    kind = "прямой рейс" if d.get("transfers") == 0 else "билет"
    if b == "аномалия":
        n = d.get("history_n") or 0
        mm = (d.get("depart") or "")[5:7]
        when_txt = "другие даты"
        if d.get("seasonal") and mm.isdigit():
            when_txt = f"другие даты в {MONTHS_IN[int(mm) - 1]}"
        text = (f"на {when_txt} {kind} стоит от {money(usual)} — "
                f"смотрел {n} {plural(n, 'дату', 'даты', 'дат')} вылета")
    elif b == "выброс":
        text = f"на соседние даты {kind} — около {money(usual)}"
    elif b == "упало":
        text = f"ещё вчера этот {kind} стоил {money(usual)}"
    else:
        return None
    return f"<i>{text}</i>"


def deal(d):
    """Одно предложение -> готовый текст экрана."""
    if d.get("legs"):
        return _combo(d)
    origin, dest = d["origin"], d["dest"]
    city = places.name(dest) if dest and dest != "?" else "куда — в посте"
    country = places.country(dest) if dest and dest != "?" else None
    route = f"<b>{escape(places.name(origin))} → {escape(city)}</b>"
    if country and country != places.country(origin):
        route += f" · {escape(country)}"

    flash = None
    if (d.get("discount") or 0) >= C.FLASH_PCT:
        flash = ("⚡️ Цена редкая: такие билеты раскупают за часы. "
                 "Проверь по кнопке, пока есть.")

    return ui.screen(headline(d),
                     ui.rows_block([route, price_line(d["price"], d.get("usual")),
                                    flight_line(d)]),
                     basis_line(d), flash, _extra(d))


def ad_label(trs=None):
    """
    Пометка «Реклама…» для сообщений с партнёрскими ссылками: по закону
    о маркировке это реклама — и в канале, и в личных сообщениях бота.
    Текст — ровно тот, что даёт Travelpayouts; erid — из параметров
    партнёрской ссылки проекта (бота по умолчанию), обновляется раз в сутки.
    """
    if not C.AD_LABEL:
        return None
    text = C.AD_LABEL.rstrip(". ")
    params = tp._partner_params(C.TP_TRS_BOT if trs is None else trs) or {}
    if C.AD_LABEL_ERID and params.get("erid"):
        text += f". erid: {params['erid']}"
    return f"<i>{escape(text)}</i>"


def _extra(d):
    """Пометка источника и текст поста, если предложение пришло из канала."""
    out = []
    if d.get("note"):
        out.append("<i>" + escape(d["note"]) + "</i>")
    if d.get("text"):
        out.append("<blockquote>" + escape(d["text"][:300]) + "</blockquote>")
    return "\n\n".join(out)


def _combo(d):
    """Связка из двух билетов: маршрут, деньги, плечи, предупреждение."""
    # хаб ставим прямо в цепочку маршрута: «через Москва» без падежей
    # не написать, а стрелки читаются и без предлога
    via = escape(places.name(d["via"]))
    money_block = ui.rows_block([
        f"<b>{escape(places.name(d['origin']))} → {via} → "
        f"{escape(places.name(d['dest']))}</b>",
        price_line(d["price"], d.get("direct_price")),
        f"<i>одним билетом — {money(d['direct_price'])}, двумя через {via} "
        f"дешевле на {money(d.get('saving') or 0)}</i>",
    ])

    legs = []
    for i, leg in enumerate(d["legs"]):
        t = clock(leg.get("depart_at") or "")
        path = f"{places.name(leg['origin'])} → {places.name(leg['dest'])}"
        who = f" · {escape(str(leg['airline']))}" if leg.get("airline") else ""
        legs.append(f"{DIGITS[i]} {when_wd(leg.get('depart'))} {t} · {escape(path)}")
        legs.append(f"{INDENT}<b>{money(leg['price'])}</b>{who}")

    return ui.screen(
        headline(d), money_block, ui.rows_block(legs), ui.rows_block(combo_stop(d)),
        footer="⚠️ Это <b>два отдельных билета</b>. Опоздал на второй из-за "
               "первого — менять его никто не обязан, багаж получать заново.")


def combo_stop(d):
    """Строки про пересадку связки: сколько ждать, какой аэропорт, ночёвка."""
    a1 = d["legs"][0].get("to_airport")
    a2 = d["legs"][1].get("from_airport")
    if not d.get("airport_known"):
        where = ", аэропорт уточни по ссылкам"
    elif a1 == a2:
        where = f", аэропорт {escape(str(a1))}"
    else:
        where = f", смена аэропорта {escape(str(a1))} → {escape(str(a2))}"

    stop = [f"⏱ пересадка {hours(d['layover_h'])}{where}"]
    if d["layover_h"] >= 12:
        stop.append("🌙 ночёвка — заложи гостиницу, экономия может её не окупить")
    return stop


# ---------- лента ----------

def feed_item(i, d):
    """Строка ленты скидок: процент, город, цена, зачёркнутая обычная, дата."""
    city = escape(places.name(d["dest"]))
    if d.get("legs"):
        city += " 🔀"
    tail = [f"<s>{money(d['usual'])}</s>" if d.get("usual") else None,
            when_wd(d.get("depart")) if d.get("depart") else None]
    return (f"{i}. <b>−{d['discount']}%</b> {city} — <b>{money(d['price'])}</b>\n"
            f"{INDENT}{' · '.join(t for t in tail if t)}")


# ---------- конкретный маршрут ----------

def trip(res):
    """Ответ на «сколько стоит вот этот маршрут в эти даты»."""
    o, d = places.name(res["origin"]), places.name(res["dest"])
    if res.get("ret"):
        title = f"🎫 <b>{escape(o)} → {escape(d)} → {escape(o)}</b>"
        when_line = f"{when(res['depart'])} — {when(res['ret'])}"
    else:
        title = f"🎫 <b>{escape(o)} → {escape(d)}</b>"
        when_line = f"{when(res['depart'])}, в одну сторону"

    blocks = [when_line]

    if res.get("round"):
        r = res["round"]
        bits = [b for b in (transfers(r.get("transfers")),
                            escape(str(r["airline"])) if r.get("airline") else None) if b]
        blocks.append(rows_block([
            "<b>Одним билетом туда-обратно</b>",
            f"{money(r['price'])}" + (f" · {' · '.join(bits)}" if bits else ""),
        ]))

    if res.get("split"):
        lines = ["<b>Двумя билетами по отдельности</b>"]
        out, back = res.get("out"), res.get("back")
        if out and back:
            lines.append(f"{money(out['price'])} + {money(back['price'])} = "
                         f"<b>{money(res['split'])}</b>")
        elif out:
            lines.append(f"<b>{money(out['price'])}</b>")
        save = res.get("saving")
        if save and save > 0:
            lines.append(f"дешевле на {money(save)}")
        elif save and save < 0:
            lines.append(f"дороже на {money(-save)} — бери одним билетом")
        blocks.append(rows_block(lines))

    alts = [a for a in (res.get("alternatives") or [])
            if a["total"] < (res.get("split") or 10 ** 9)][:4]
    if alts:
        lines = ["<b>📅 Соседние даты дешевле</b>",
                 "<i>сумма за два билета в одну сторону</i>"
                 if res.get("ret") else "<i>в одну сторону</i>"]
        want = None
        if res.get("ret"):
            want = (datetime.fromisoformat(res["ret"][:10])
                    - datetime.fromisoformat(res["depart"][:10])).days
        for a in alts:
            label = (f"{when(a['depart'])} → {when(a['ret'])}" if a.get("ret")
                     else when(a["depart"]))
            note = ""
            if a.get("nights") and want and a["nights"] != want:
                n = a["nights"]
                note = f" · {n} {plural(n, 'ночь', 'ночи', 'ночей')}"
            lines.append(f"{label} — <b>{money(a['total'])}</b>{note}")
        blocks.append(rows_block(lines))

    if not res.get("out") and not res.get("round"):
        blocks.append("На эти даты цен не нашлось. Попробуй соседние или "
                      "проверь, летают ли вообще по этому маршруту.")

    return ui.screen(title, *blocks)


def rows_block(lines):
    """Локальный синоним ui.rows_block — чтобы не тащить ui в каждую строку."""
    return ui.rows_block(lines)


# ---------- списки ----------

def row(r):
    """Строка для списков «топ за сутки» и «по странам»."""
    city = places.name(r["dest"])
    price = r["p"]
    d = when(r["depart"]) if "depart" in r.keys() else None
    tail = f" · {d}" if d else ""
    link = tp.tagged(r["link"], "bot_cheap") if "link" in r.keys() else None
    name = f'<a href="{escape(link, quote=True)}">{escape(city)}</a>' if link else escape(city)
    country = places.country(r["dest"])
    if country and country != "Россия":
        name += f", {escape(country)}"
    return f"<b>{money(price)}</b> — {name}{tail}"
