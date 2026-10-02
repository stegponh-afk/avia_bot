"""Общее для всех экранов: состояния ввода, доступ, показ в нужном стиле."""
from aiogram.fsm.state import State, StatesGroup

import config as C
import db
import ui
import users


class Ask(StatesGroup):
    """Когда ждём от человека текст, а не нажатие кнопки."""
    origin = State()      # свой город вылета
    budget = State()      # своя сумма бюджета
    dates = State()       # свой период
    rt = State()          # даты туда-обратно, город уже выбран кнопкой
    watch = State()       # откуда и куда — своё направление для слежки
    link_name = State()   # название новой ссылки-отслеживания (админка)
    watch_price = State() # своя цена для направления: «напиши, когда дешевле N»


def allowed(uid):
    return not C.ADMIN_IDS or uid in C.ADMIN_IDS


def is_owner(uid):
    return not C.OWNER_IDS or uid in C.OWNER_IDS


def ensure(chat_id, name=""):
    """Завести подписчика, если его ещё нет. Выключенного НЕ включает."""
    if not db.get_sub(chat_id):
        db.add_sub(chat_id, name)


async def say(m, text, markup=None):
    """Показать экран в оформлении этого человека."""
    return await ui.send(m, text, markup, users.style(m.chat.id))


async def edit(m, text, markup=None):
    """Заменить экран на месте — для переходов внутри одного сообщения."""
    return await ui.edit(m, text, markup, users.style(m.chat.id))
