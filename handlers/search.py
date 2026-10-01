"""
🔍 Найти билет: человек уже знает, куда хочет, и спрашивает цену.

Три ступени, каждая — одним действием:
  город           → календарь: самые дешёвые даты, нажал — купил;
  город + дата(ы) → цена на эти даты, два билета против одного, соседние дни;
  «с пересадкой»  → связка через третий город, если выходит дешевле.

Вводить можно и текстом в любой момент: «Сочи 18.10 25.10».
"""
import html
from datetime import date

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config as C
import db
import detect
import keyboards as kb
import parse
import places
import poll
import post
import render
import tp
import trip
import ui
import users
from .common import Ask, say

router = Router()

HELP = ui.screen(
    "🔍 <b>Найти билет</b>",
    "Напиши, куда летим — можно сразу с датами:",
    ui.rows_block([
        "<code>Сочи</code> — цены на все даты",
        "<code>Сочи 18.10</code> — в одну сторону",
        "<code>Сочи 18.10 25.10</code> — туда и обратно",
        "<code>Киров Питер 18.10</code> — из другого города",
        "<code>Киров Москва</code> — все даты и 🔔 слежка за направлением",
    ]),
    footer="Или выбери направление 👇")


async def show(m: Message):
    await say(m, HELP, kb.search(users.origin(m.chat.id)))


# ---------- календарь направления ----------

@router.callback_query(F.data.startswith("dest:"))
async def cb_dest(q: CallbackQuery):
    await q.answer("Смотрю цены…")
    parts = q.data.split(":")            # dest:КУДА или dest:КУДА:ОТКУДА
    await show_dest(q.message, parts[1], parts[2] if len(parts) > 2 else None)


async def show_dest(m: Message, code, origin=None):
    """
    Самые дешёвые даты по направлению на три месяца вперёд.
    origin — не свой город вылета («Киров Москва» при вылете из Казани).
    """
    chat = m.chat.id
    own = users.origin(chat)
    origin = origin or own
    if code == origin:
        await m.answer("Это твой город вылета 🙂 Напиши, куда летим.")
        return
    a, b = users.dates(chat)
    async with aiohttp.ClientSession() as s:
        api = tp.Travelpayouts(s, origin)
        got = []
        for ym in tp.months_ahead(3):
            got += await api.calendar(code, ym)
    today = date.today().isoformat()
    got = [d for d in got if (d.get("depart") or today) >= today
           and users.in_window(d.get("depart"), a, b)]
    if not got:
        await m.answer("{} → {}: цен не нашлось{}. Попробуй другие даты или город."
                       .format(places.name(origin), places.full(code),
                               " в выбранные даты" if (a or b) else ""))
        return
    db.save_prices(got)
    best = sorted(detect.dedup_dates(got), key=lambda d: d["price"])

    # обычная цена рядом с минимальной — чтобы было видно, выгодно ли это вообще
    usual, n, _ = detect.stats(origin, code)
    w = db.get_watch(chat, origin, code)
    lines = [f"от <b>{render.money(best[0]['price'])}</b> в одну сторону"]
    if usual and n:
        lines.append(f"обычно — от {render.money(usual)}")
    km = places.distance(code)
    about = [places.country(code) or "", render.km_str(km) if km else ""]
    await say(m, ui.screen(
        f"📍 <b>{places.name(origin)} → {places.name(code)}</b>",
        " · ".join(x for x in about if x), ui.rows_block(lines),
        footer="Самые дешёвые даты — нажми, чтобы купить 👇"),
        kb.dest_card(code, best, origin, own=origin == own, watch_id=w["id"] if w else None))


# ---------- туда и обратно ----------

@router.callback_query(F.data.startswith("rt:"))
async def cb_round_trip(q: CallbackQuery, state: FSMContext):
    """Откуда и куда уже известны — спрашиваем только даты."""
    code = q.data.split(":", 1)[1]
    await state.set_state(Ask.rt)
    await state.update_data(dest=code)
    await q.answer()
    await say(q.message, ui.screen(
        f"🎫 <b>{places.name(users.origin(q.message.chat.id))} → "
        f"{places.name(code)}</b>",
        "Когда летим? Пришли даты:",
        ui.rows_block(["<code>18.10 25.10</code> — туда и обратно",
                       "<code>18.10</code> — только туда"])))


@router.message(Ask.rt)
async def got_rt_dates(m: Message, state: FSMContext):
    a, b = parse.parse_dates(m.text or "")
    if not a:
        await m.answer("Нужны даты, например <code>18.10 25.10</code>.")
        return
    dest = (await state.get_data()).get("dest")
    await state.clear()
    if not dest:
        await show(m)
        return
    await run_trip(m, users.origin(m.chat.id), dest, a, b)


@router.callback_query(F.data.startswith("trip:"))
async def cb_trip(q: CallbackQuery):
    """Нажали соседнюю дату — считаем маршрут заново на неё."""
    _, origin, dest, depart, ret = q.data.split(":")
    await q.answer("Считаю на эти даты")
    await run_trip(q.message, origin, dest, depart, ret or None)


async def run_trip(m: Message, origin, dest, depart, ret):
    """Цена на даты: одним билетом, двумя, и соседние дни."""
    bar = await ui.Progress(m, "🎫 <b>Считаю маршрут</b>").start(
        f"{places.name(origin)} → {places.name(dest)}")
    res, err = None, None
    try:
        async with aiohttp.ClientSession() as s:
            res = await trip.lookup(s, origin, dest, depart, ret, progress=bar.step)
    except Exception as e:
        err = e
    finally:
        await bar.stop()
    if err:
        await m.answer(f"Не получилось: {html.escape(str(err))[:200]}")
        return
    for d in (res.get("out"), res.get("back"), res.get("round")):
        if d:
            db.save_prices([d])
    await say(m, render.trip(res), kb.trip_kb(res))


# ---------- с пересадкой ----------

@router.callback_query(F.data.startswith("combo:"))
async def cb_combo(q: CallbackQuery):
    await q.answer("Ищу, это до минуты")
    await run_combo(q.message, q.data.split(":", 1)[1])


async def run_combo(m: Message, dest):
    """Связка в конкретный город: два билета через хаб дешевле одного?"""
    chat = m.chat.id
    bar = await ui.Progress(m, "🔀 <b>Ищу вариант с пересадкой</b>").start(
        f"в город {places.name(dest)}")
    got, err = [], None
    try:
        got = await poll.find_combos(users.origin(chat), dests=[dest],
                                     months=users.months(chat),
                                     hubs_n=C.COMBO_PICK_HUBS, store=False,
                                     progress=bar.step)
    except Exception as e:
        err = e
    finally:
        await bar.stop()
    if err:
        await m.answer(f"Не получилось: {html.escape(str(err))[:200]}")
        return
    if not got:
        await m.answer(f"С пересадкой в {places.full(dest)} дешевле не выходит: "
                       "через другие города дороже или рейсы не стыкуются по времени. "
                       "Лучше брать один билет.")
        return
    for c in sorted(got, key=lambda x: -x["saving"])[:3]:
        c["why"] = ["связка"]
        detect.discount(c)
        await post.send(m.bot, chat, c, "bot_combo", users.style(chat))
