"""
Находка в личку бота — в том же виде, что пост в канале.

Картинка-карточка (маршрут, цена, зачёркнутая обычная, процент) и короткая
подпись: цена, с чем сравнили, дата и рейс, обратный билет. Кнопки — купить,
обратно, все даты. Хэштегов и пометки о рекламе нет: это не канал.

Карточку не нарисовать (находка из чужого канала без города и даты) или
Telegram не принял картинку — уходит прежний текстовый экран. Лучше проще,
чем никак.
"""
import asyncio

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

import card
import channel
import config as C
import keyboards as kb
import places
import render
import tp
import ui

FOOTNOTE = "цена на момент проверки"
ICON = {"cheaper": "📉"}
FLASH = "⚡️ Цена редкая: такие билеты раскупают за часы — проверь, пока есть."


def drawable(d):
    """Хватает ли данных на карточку: город, дата, цена."""
    dest = d.get("dest")
    return (d.get("src") != "tg" and d.get("price") and d.get("depart")
            and dest and dest != "?" and len(dest) == 3)


def cand(d):
    """
    Разметка находки (discount/usual/basis) в язык канала: {kind, pct, usual, basis}.
    Без скидки (билет просто в бюджете) — kind budget, процента нет.
    """
    pct, usual, basis = d.get("discount"), d.get("usual"), d.get("basis")
    was = d.get("watch_was")               # своё направление: дешевле, чем в прошлый раз
    if was and was > d["price"] and (pct or 0) < C.WATCH_PCT:
        return {"kind": "cheaper", "pct": round((1 - d["price"] / was) * 100),
                "usual": was, "basis": "watch"}
    if d.get("legs"):
        return {"kind": "combo", "pct": pct, "usual": d.get("direct_price"),
                "basis": "direct_price"}
    if not (pct and usual):
        return {"kind": "budget", "pct": None, "usual": None, "basis": None}
    if basis == "упало":
        return {"kind": "drop", "pct": pct, "usual": usual, "basis": "was"}
    return {"kind": "super" if pct >= C.SUPER_PCT else "deal", "pct": pct,
            "usual": usual, "basis": "near_median" if basis == "выброс" else "hist_usual"}


async def prepare(d):
    """Обратный билет, как в канале. Не ответил источник — карточка без него."""
    if d.get("legs") or d.get("ret") or "back" in d:
        return
    try:
        await asyncio.wait_for(channel.enrich(d), 15)
    except Exception as e:
        print(f"  обратный билет {d['origin']}-{d['dest']}: {type(e).__name__}")
        d["back"] = d["weekend"] = None


def image(d, c):
    country = places.country(d["dest"]) or ""
    usual = render.money(c["usual"]) if c["usual"] else None
    if d.get("legs"):
        first = d["legs"][0]
        hub = places.name(d["via"])
        info = " · ".join(x for x in (
            render.when_wd(first.get("depart") or d["depart"]),
            render.clock(first.get("depart_at") or ""),
            f"пересадка {render.hours(d['layover_h'])}", "два билета") if x)
        return card.render("combo", c["pct"], channel.route(d), country,
                           render.money(d["price"]), usual, info,
                           transfers=1, via=[hub], footnote=FOOTNOTE)
    return card.render(c["kind"], c["pct"], channel.route(d), country,
                       render.money(d["price"]), usual, channel.info(d),
                       transfers=d.get("transfers"),
                       via=[places.name(v) for v in (tp.via(d) or [])],
                       back=channel.back_text(d, short=True), footnote=FOOTNOTE)


def caption(d, c):
    """Подпись как у поста в канале, только без хэштегов."""
    if d.get("legs"):
        return _combo(d, c)
    money = render.money
    icon = ICON.get(c["kind"]) or channel.ICON.get(c["kind"], "✈️")
    lines = [f"{icon} <b>{channel.route(d)} — {money(d['price'])}</b>"]
    if c["basis"] == "watch":
        lines.append(f"в прошлый раз {money(c['usual'])}, подешевело на {c['pct']}%")
    else:
        lines.append(channel.usual_line(c) if c["pct"] else "в твоём бюджете")
    lines.append(channel.info(d))
    lines += channel.return_lines(d)
    if (c["pct"] or 0) >= C.FLASH_PCT:
        lines += ["", FLASH]
    if d.get("watch_id"):
        was = d.get("watch_was")
        lines += ["", "🔔 <i>ты следишь за этим направлением"
                  + (f" · в прошлый раз {money(was)}" if was and c["basis"] != "watch"
                     else "") + "</i>"]
    return "\n".join(lines)


def _combo(d, c):
    """Связка: два плеча, пересадка и честное предупреждение про два билета."""
    money = render.money
    hub = render.escape(places.name(d["via"]))
    lines = [f"🔀 <b>{channel.route(d)} — {money(d['price'])}</b>",
             f"одним билетом {money(d['direct_price'])}, двумя через {hub} "
             f"дешевле на {money(d.get('saving') or 0)}", ""]
    for i, leg in enumerate(d["legs"]):
        t = render.clock(leg.get("depart_at") or "")
        lines.append(f"{render.DIGITS[i]} {render.when_wd(leg.get('depart'))}"
                     f"{' ' + t if t else ''} · {places.name(leg['origin'])} → "
                     f"{places.name(leg['dest'])} — {money(leg['price'])}")
    lines += render.combo_stop(d)
    lines += ["", "⚠️ Это <b>два отдельных билета</b>: опоздал на второй из-за первого — "
                  "менять его никто не обязан, багаж получать заново."]
    return "\n".join(lines)


async def send(bot, chat_id, d, sub, style=None, cache=None):
    """
    Отправить находку карточкой. cache — общий на рассылку словарь: одна
    и та же находка рисуется один раз, дальше уходит по file_id картинки.
    """
    if not drawable(d):
        return await ui.push(bot, chat_id, render.deal(d), kb.deal_kb(d, sub),
                             style or C.STYLE)
    cache = {} if cache is None else cache
    k = (d["origin"], d["dest"], d.get("depart"), d["price"])
    item = cache.get(k)
    try:
        if item is None:
            await prepare(d)
            c = cand(d)
            item = cache[k] = {"photo": BufferedInputFile(image(d, c), "card.png"),
                               "caption": caption(d, c)}
        msg = await bot.send_photo(chat_id, item["photo"], caption=item["caption"],
                                   reply_markup=kb.deal_kb(d, sub))
        if msg.photo:
            item["photo"] = msg.photo[-1].file_id
        return msg
    except (TelegramForbiddenError, TelegramRetryAfter):
        raise
    except Exception as e:
        print(f"  карточка {d['origin']}-{d['dest']} не ушла ({e}), шлю текстом")
        return await ui.push(bot, chat_id, render.deal(d), kb.deal_kb(d, sub),
                             style or C.STYLE)
