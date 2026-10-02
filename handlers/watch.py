"""
🔔 Мои направления: следить за конкретным маршрутом, «Киров → Москва».

Добавить можно тремя путями — кому как удобнее:
  • кнопка «🔔 Следить» под календарём направления;
  • «➕ Добавить» в списке и два города текстом;
  • написать «Киров Москва» — откроется календарь с той же кнопкой.
Сама проверка и решение «писать или нет» — в watch.py.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import parse
import places
import render
import ui
import users
import watch
from .common import Ask, edit, ensure, say

router = Router()


def screen(chat_id):
    ws = db.watches_of(chat_id)
    if not ws:
        return (ui.screen(
            "🔔 <b>Мои направления</b>",
            "Здесь — маршруты, за которыми я слежу персонально для тебя: "
            "к родителям, на работу, домой.",
            watch.RULES,
            footer="Нажми «➕ Добавить» или просто напиши два города: "
                   "<code>Киров Москва</code>"), kb.watch_list(ws))
    body = "\n".join(watch.line(i, w) for i, w in enumerate(ws, 1))
    muted = ("🔕 <i>Уведомления по направлениям выключены — цены здесь обновляются, "
             "но писать не буду. Включить: ⚙️ Настройки → 🔔 Что присылать.</i>"
             if "watch" not in users.alerts(chat_id) else None)
    return (ui.screen("🔔 <b>Мои направления</b>", body, muted or watch.RULES,
                      footer="Нажми маршрут — покажу все даты 👇"), kb.watch_list(ws))


async def show(m: Message):
    await say(m, *screen(m.chat.id))


@router.callback_query(F.data == "watch:list")
async def cb_list(q: CallbackQuery, state: FSMContext):
    await state.clear()
    await q.answer()
    await show(q.message)


@router.callback_query(F.data.startswith("watch:rm:"))
async def cb_remove(q: CallbackQuery):
    """Убрать из списка — список перерисовывается на месте."""
    db.del_watch(q.message.chat.id, int(q.data.split(":")[2]))
    await edit(q.message, *screen(q.message.chat.id))
    await q.answer("Больше не слежу")


@router.callback_query(F.data.startswith("watch:del:"))
async def cb_delete(q: CallbackQuery):
    """Перестать следить из карточки или календаря: сообщение не трогаем."""
    w = db.watch_by_id(int(q.data.split(":")[2]))
    if not w or not db.del_watch(q.message.chat.id, w["id"]):
        await q.answer("Уже не слежу за этим направлением")
        return
    await q.answer(f"Больше не слежу: {watch.route(w)}", show_alert=True)


@router.callback_query(F.data.startswith("watch:add:"))
async def cb_add(q: CallbackQuery):
    _, _, origin, dest = q.data.split(":")
    await q.answer("Добавляю…")
    await add(q.message, q.from_user.full_name, origin, dest)


@router.callback_query(F.data == "watch:new")
async def cb_new(q: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.watch)
    await q.answer()
    home = places.name(users.origin(q.message.chat.id))
    await say(q.message, ui.screen(
        "➕ <b>Какое направление?</b>",
        "Напиши откуда и куда:",
        ui.rows_block(["<code>Киров Москва</code>",
                       "<code>Питер → Калининград</code>",
                       f"<code>Сочи</code> — из твоего города ({home})"])))


@router.message(Ask.watch)
async def got_route(m: Message, state: FSMContext):
    codes = parse._cities(m.text or "")
    if not codes:
        await m.answer("Не нашёл такой город. Напиши, например, <code>Киров Москва</code>.")
        return
    origin, dest = (codes[0], codes[1]) if len(codes) >= 2 \
        else (users.origin(m.chat.id), codes[0])
    if origin == dest:
        await m.answer("Откуда и куда — один и тот же город 🙂 Напиши два разных.")
        return
    await state.clear()
    await add(m, m.from_user.full_name if m.from_user else "", origin, dest)


@router.callback_query(F.data.startswith("wd:"))
async def cb_add_date(q: CallbackQuery):
    """«🔔 Следить за этой датой» — под карточкой билета и ответом на даты."""
    _, origin, dest, day = q.data.split(":")
    await q.answer("Добавляю…")
    await add(q.message, q.from_user.full_name, origin, dest, day)


async def add(m: Message, name, origin, dest, on_date=""):
    """Начать следить и сказать, от какой цены будем мерить."""
    chat = m.chat.id
    ensure(chat, name)
    had = db.get_watch(chat, origin, dest, on_date)
    wid, d, err = await watch.start(chat, origin, dest, on_date)
    if err:
        await say(m, ui.screen("🔔 <b>Список полон</b>", err), kb.watch_list(db.watches_of(chat)))
        return
    route = f"{places.name(origin)} → {places.name(dest)}"
    what = "этим рейсом" if on_date else "этим направлением"
    if on_date:
        route += f", {render.when_wd(on_date)}"
    if had:
        now = f"Уже слежу за {what}."
    elif d:
        now = (f"Сейчас {'' if on_date else 'от '}<b>{render.money(d['price'])}</b>"
               + ("" if on_date else f" · {render.when_wd(d['depart'])}")
               + (f", это на {d['discount']}% дешевле обычного" if d.get("discount") else ""))
    else:
        now = ("Прямо сейчас цен на эту дату нет — буду проверять и напишу, как появятся."
               if on_date else
               "Прямо сейчас цен по этому направлению нет — возможно, рейсов мало. "
               "Буду проверять и напишу, как появятся.")
    rows = [[kb._b("🎯 Указать свою цену", f"watch:tg:{wid}")]]
    if not on_date:
        rows.append([kb._b(f"📅 Все даты · {places.name(dest)}", f"dest:{dest}:{origin}")])
    rows.append([kb._b(kb.WATCH, "watch:list")])
    await say(m, ui.screen(f"🔔 <b>Слежу: {route}</b>", now, watch.RULES,
                           footer=f"Все направления — в «{kb.WATCH}»"), kb._kb(rows))


# ---------- своя цена ----------

@router.callback_query(F.data.startswith("watch:tg:"))
async def cb_target(q: CallbackQuery, state: FSMContext):
    w = db.watch_by_id(int(q.data.split(":")[2]))
    if not w or w["chat_id"] != q.message.chat.id:
        await q.answer("Такого направления уже нет в списке")
        return
    await state.set_state(Ask.watch_price)
    await state.update_data(watch_id=w["id"])
    await q.answer()
    now = (f"Сейчас {'' if w['on_date'] else 'от '}<b>{render.money(w['cur_price'])}</b>."
           if w["cur_price"] else "Цен сейчас нет.")
    await say(q.message, ui.screen(
        f"🎯 <b>{watch.route(w)}</b>",
        "Какая цена тебе подходит? Напиши сумму — напишу, как только билет "
        "станет не дороже неё.",
        now,
        ui.rows_block(["<code>3000</code> — до 3 000 ₽",
                       "<code>0</code> — убрать свою цену, следить как обычно"])))


@router.message(Ask.watch_price)
async def got_target(m: Message, state: FSMContext):
    digits = "".join(ch for ch in (m.text or "") if ch.isdigit())
    if not digits:
        await m.answer("Нужна сумма цифрами, например <code>3000</code>.")
        return
    wid = (await state.get_data()).get("watch_id")
    await state.clear()
    w = db.watch_by_id(wid) if wid else None
    if not w or w["chat_id"] != m.chat.id:
        await say(m, *screen(m.chat.id))
        return
    target = int(digits) or None
    db.set_watch(w["id"], target=target)
    if not target:
        note = "Своя цена убрана — слежу как обычно."
    elif w["cur_price"] and w["cur_price"] <= target:
        # уже сейчас не дороже — говорим здесь, а отдельным письмом не дублируем
        note = (f"Уже сейчас {render.money(w['cur_price'])} — это не дороже твоей цены. "
                "Напишу, если станет ещё дешевле.")
    else:
        note = f"Напишу, как только билет будет не дороже {render.money(target)}."
    await say(m, ui.screen(f"🎯 <b>{watch.route(w)}</b>", note), kb.watch_list(db.watches_of(m.chat.id)))
