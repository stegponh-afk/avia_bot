"""
Знакомство. Новому человеку — один вопрос: откуда летаешь. Всё.

Режим, бюджет и даты стоят по умолчанию разумно (скидки от 25%, без
ограничений), и объяснять их на входе — значит потерять человека
на втором экране.
"""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config as C
import db
import keyboards as kb
import links
import places
import ui
import users
from . import settings
from .common import allowed, edit, say

router = Router()

HOW_TO = ui.rows_block([
    "🔥 <b>Скидки</b> — что сейчас дешевле обычного",
    "🔍 <b>Найти билет</b> — цены на твои даты",
    "🔔 <b>Мои направления</b> — слежу за нужным рейсом, например Киров → Москва",
    "Или просто напиши: <code>Сочи 18.10</code>",
])


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    if not allowed(m.from_user.id):
        await m.answer("Доступ закрыт. Твой id: <code>{}</code>".format(m.from_user.id))
        return
    is_new = db.get_sub(m.chat.id) is None
    # /start youtube — пришёл по ссылке-отслеживанию из админки
    links.track(command.args, m.chat.id, is_new)
    if is_new:
        await say(m, ui.screen(
            "✈️ <b>Привет! Я ловлю дешёвые билеты</b>",
            "Слежу за ценами круглосуточно и пишу, когда билет стоит намного "
            "дешевле обычного — бывает и на 50–90%.",
            footer="Откуда ты обычно летаешь? 👇"), kb.onboarding())
        return
    await home(m)


@router.message(Command("menu", "help"))
async def cmd_menu(m: Message, state: FSMContext):
    await state.clear()
    await home(m)


async def home(m: Message):
    """Экран для тех, кто уже с нами: где он и что умеет бот."""
    await say(m, ui.screen(
        "✈️ <b>Дешёвые билеты</b>",
        ui.rows_block([f"Вылет: <b>{places.name(users.origin(m.chat.id))}</b>",
                       f"Присылаю: <b>{users.summary(users.get(m.chat.id))}</b>"]),
        HOW_TO), kb.main(m.chat.id))


@router.callback_query(F.data.startswith("start:"))
async def cb_start_origin(q: CallbackQuery, state: FSMContext):
    code = q.data.split(":", 1)[1]
    if code == "other":
        await settings.ask_origin(q, state, onboarding=True)
        return
    await q.answer("Проверяю город…")
    # убираем кнопки с приветствия, чтобы не нажали второй раз
    await edit(q.message, ui.screen("✈️ <b>Привет! Я ловлю дешёвые билеты</b>",
                                    f"Вылет: <b>{places.name(code)}</b>"))
    await settings.choose_origin(q.message, q.from_user.full_name, code, onboarding=True)


async def welcome_done(m: Message, code, ready):
    """Город выбран — объясняем в трёх строках, что дальше."""
    tail = ("Загляни в 🔥 Скидки — там уже есть что посмотреть." if ready else
            "Первые находки появятся в течение часа. Обычную цену каждого "
            "маршрута я узнаю за несколько дней — чем дольше слежу, тем точнее.")
    await say(m, ui.screen(
        f"✅ <b>Готово, вылет: {places.name(code)}</b>",
        f"Напишу, когда билет будет дешевле обычного на {C.DEAL_PCT}% и больше. "
        "Поменять — в ⚙️ Настройках.",
        HOW_TO, footer=tail), kb.main(m.chat.id))
