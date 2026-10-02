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


async def add(m: Message, name, origin, dest):
    """Начать следить и сказать, от какой цены будем мерить."""
    chat = m.chat.id
    ensure(chat, name)
    had = db.get_watch(chat, origin, dest)
    wid, d, err = await watch.start(chat, origin, dest)
    if err:
        await say(m, ui.screen("🔔 <b>Список полон</b>", err), kb.watch_list(db.watches_of(chat)))
        return
    route = f"{places.name(origin)} → {places.name(dest)}"
    if had:
        now = "Уже слежу за этим направлением."
    elif d:
        now = (f"Сейчас от <b>{render.money(d['price'])}</b> · {render.when_wd(d['depart'])}"
               + (f", это на {d['discount']}% дешевле обычного" if d.get("discount") else ""))
    else:
        now = ("Прямо сейчас цен по этому направлению нет — возможно, рейсов мало. "
               "Буду проверять и напишу, как появятся.")
    back = kb._b(f"📅 Все даты · {places.name(dest)}", f"dest:{dest}:{origin}")
    await say(m, ui.screen(f"🔔 <b>Слежу: {route}</b>", now, watch.RULES,
                           footer=f"Все направления — в «{kb.WATCH}»"),
              kb._kb([[back], [kb._b(kb.WATCH, "watch:list")]]))
