"""
⚙️ Настройки: четыре вопроса, на которые человек знает ответ сразу.

Откуда летаешь, что присылать, какой бюджет, когда хочешь лететь.
Каждый — отдельный экран в том же сообщении, с кнопкой «Назад».
"""
import asyncio
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import parse
import places
import poll
import render
import tp
import ui
import users
from .common import Ask, edit, ensure, say

router = Router()


def text(chat_id, note=None):
    icon, title, note = users.MODES[users.mode(chat_id)]
    b = users.budget(chat_id)
    a, bb = users.dates(chat_id)
    lines = [
        f"🛫 Откуда: <b>{places.full(users.origin(chat_id))}</b>",
        f"🔔 Присылаю: <b>{title.lower()}</b> — {note}",
        f"💰 Бюджет: <b>{('до ' + render.money(b)) if b else 'без ограничения'}</b>",
        f"📅 Когда: <b>{render.dates(a, bb)}</b>",
    ]
    return ui.screen("⚙️ <b>Настройки</b>", ui.rows_block(lines), note,
                     footer="Что поменять? 👇")


async def show(m: Message):
    await say(m, text(m.chat.id), kb.settings())


@router.callback_query(F.data == "set:home")
async def cb_home(q: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(q.message, text(q.message.chat.id), kb.settings())
    await q.answer()


# ---------- откуда ----------

@router.callback_query(F.data == "set:origin")
async def cb_origin_menu(q: CallbackQuery):
    cur = users.origin(q.message.chat.id)
    await edit(q.message, ui.screen("🛫 <b>Откуда летаешь?</b>",
                                    f"Сейчас: <b>{places.full(cur)}</b>"),
               kb.origin_menu(cur))
    await q.answer()


@router.callback_query(F.data.startswith("origin:"))
async def cb_origin(q: CallbackQuery, state: FSMContext):
    code = q.data.split(":", 1)[1]
    if code == "other":
        await ask_origin(q, state, onboarding=False)
        return
    await q.answer("Проверяю город…")
    await choose_origin(q.message, q.from_user.full_name, code, screen=q.message)


async def ask_origin(q: CallbackQuery, state: FSMContext, onboarding):
    await state.set_state(Ask.origin)
    await state.update_data(onboarding=onboarding)
    await q.message.answer("Напиши свой город, например <code>Самара</code>.")
    await q.answer()


@router.message(Ask.origin)
async def got_origin(m: Message, state: FSMContext):
    code = places.find(m.text or "")
    if not code:
        await m.answer("Не нашёл такой город. Попробуй иначе — например, "
                       "<code>Нижний Новгород</code> или код <code>GOJ</code>.")
        return
    onboarding = (await state.get_data()).get("onboarding")
    await state.clear()
    await choose_origin(m, m.from_user.full_name, code, onboarding=onboarding)


async def choose_origin(m: Message, name, code, onboarding=False, screen=None):
    """
    Проверить город и сохранить.

    Проверка двойная: город есть в справочнике и из него реально летают —
    в справочнике десять тысяч кодов, включая аэродромы без рейсов.
    screen — сообщение, которое заменить на экран настроек; иначе шлём новое.
    """
    bar = None if screen else await ui.Progress(m, "🛫 <b>Проверяю город</b>").start(
        places.full(code))
    try:
        ok, n, title = await tp.validate_origin(code)
    finally:
        if bar:
            await bar.stop()
    chat = m.chat.id
    if not ok:
        await m.answer(f"Город <b>{places.full(code)}</b> нашёлся, но рейсов "
                       "из него не видно. Выбери ближайший крупный город.",
                       reply_markup=kb.onboarding() if onboarding
                       else kb.origin_menu(users.origin(chat)))
        return

    fresh = not db.get_sub(chat)
    ensure(chat, name)
    db.set_origin(chat, code)
    ready = db.has_recent(code, hours=6)
    if not ready:
        # первый сбор по городу — в фоне, чтобы лента не пустовала до вечера
        asyncio.create_task(poll.warm(code))

    if onboarding or fresh:
        from .start import welcome_done
        await welcome_done(m, code, ready)
    elif screen:
        await edit(screen, text(chat), kb.settings())
    else:
        await say(m, text(chat), kb.settings())


# ---------- что присылать ----------

@router.callback_query(F.data == "set:mode")
async def cb_mode_menu(q: CallbackQuery):
    cur = users.mode(q.message.chat.id)
    lines = [f"{icon} <b>{title}</b> — {note}"
             for icon, title, note in users.MODES.values()]
    await edit(q.message, ui.screen("🔔 <b>Что присылать?</b>", ui.rows_block(lines)),
               kb.mode_menu(cur))
    await q.answer()


@router.callback_query(F.data.startswith("mode:"))
async def cb_mode(q: CallbackQuery):
    chat, mode = q.message.chat.id, q.data.split(":", 1)[1]
    if mode not in users.MODES:
        await q.answer()
        return
    if mode == "off":
        ensure(chat, q.from_user.full_name)
        db.stop_sub(chat)
    else:
        db.add_sub(chat, q.from_user.full_name)       # заодно включает выключенного
        db.set_mode(chat, mode)
    note = None
    if mode == "budget" and not users.budget(chat):
        note = "Бюджет пока без ограничения — задай его, иначе буду слать только скидки."
    await edit(q.message, text(chat, note), kb.settings())
    await q.answer("Сохранено")


# ---------- бюджет ----------

@router.callback_query(F.data == "set:budget")
async def cb_budget_menu(q: CallbackQuery):
    await edit(q.message, ui.screen(
        "💰 <b>Бюджет на билет</b>",
        "Дороже не присылаю, даже со скидкой. Цена — в одну сторону."),
        kb.budget_menu(users.budget(q.message.chat.id)))
    await q.answer()


@router.callback_query(F.data.startswith("budget:"))
async def cb_budget(q: CallbackQuery, state: FSMContext):
    chat, val = q.message.chat.id, q.data.split(":", 1)[1]
    if val == "custom":
        await state.set_state(Ask.budget)
        await q.message.answer("Пришли сумму в рублях, например <code>15000</code>.")
        await q.answer()
        return
    ensure(chat, q.from_user.full_name)
    db.set_range(chat, 0, int(val) or None)
    await edit(q.message, text(chat), kb.settings())
    await q.answer("Сохранено")


@router.message(Ask.budget)
async def got_budget(m: Message, state: FSMContext):
    nums = [int(x) for x in (m.text or "").replace(" ", "").replace("-", ",").split(",")
            if x.isdigit()]
    if not nums or max(nums) < 500:
        await m.answer("Нужна сумма числом, например <code>15000</code>.")
        return
    await state.clear()
    ensure(m.chat.id, m.from_user.full_name)
    db.set_range(m.chat.id, 0, max(nums))
    await show(m)


# ---------- когда ----------

@router.callback_query(F.data == "set:dates")
async def cb_dates_menu(q: CallbackQuery):
    await edit(q.message, ui.screen("📅 <b>Когда хочешь лететь?</b>",
                                    "Буду присылать только вылеты в эти даты."),
               kb.dates_menu(users.dates(q.message.chat.id)))
    await q.answer()


@router.callback_query(F.data.startswith("dates:"))
async def cb_dates(q: CallbackQuery, state: FSMContext):
    parts = q.data.split(":")
    chat = q.message.chat.id
    if parts[1] == "custom":
        await state.set_state(Ask.dates)
        await q.message.answer("Пришли период, например <code>01.12 15.12</code>.")
        await q.answer()
        return
    ensure(chat, q.from_user.full_name)
    if parts[1] == "any":
        db.set_dates(chat, None, None)
    else:                                   # конкретный месяц
        y, mo = int(parts[2][:4]), int(parts[2][5:7])
        nxt = date(y + (mo == 12), (mo % 12) + 1, 1)
        db.set_dates(chat, date(y, mo, 1).isoformat(),
                     (nxt - timedelta(days=1)).isoformat())
    await edit(q.message, text(chat), kb.settings())
    await q.answer("Сохранено")


@router.message(Ask.dates)
async def got_dates(m: Message, state: FSMContext):
    a, b = parse.parse_dates(m.text or "")
    if not a:
        await m.answer("Не понял даты. Пришли так: <code>01.12 15.12</code>.")
        return
    await state.clear()
    ensure(m.chat.id, m.from_user.full_name)
    db.set_dates(m.chat.id, a, b)
    await show(m)
