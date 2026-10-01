"""
Нижнее меню. Стоит первым после /start: нажатие кнопки всегда значит
«хочу туда», даже если бот в этот момент ждал от человека текст.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config as C
import keyboards as kb
from . import admin, feed, search, settings, watch

router = Router()


@router.message(F.text == kb.DEALS)
async def btn_deals(m: Message, state: FSMContext):
    await state.clear()
    await feed.show(m, "deals")


@router.message(F.text == kb.SEARCH)
async def btn_search(m: Message, state: FSMContext):
    await state.clear()
    await search.show(m)


@router.message(F.text == kb.WATCH)
async def btn_watch(m: Message, state: FSMContext):
    await state.clear()
    await watch.show(m)


@router.message(F.text == kb.ADMIN)
async def btn_admin(m: Message, state: FSMContext):
    if m.from_user.id not in C.OWNER_IDS:      # кнопка есть только у владельцев
        return
    await state.clear()
    await admin.show(m)


@router.message(F.text == kb.SETTINGS)
async def btn_settings(m: Message, state: FSMContext):
    await state.clear()
    await settings.show(m)


@router.message(F.text.in_(kb.LEGACY.keys()))
async def btn_legacy(m: Message, state: FSMContext):
    """Кнопка из прошлой версии меню: выполняем и заодно выдаём новое."""
    await state.clear()
    await m.answer("Меню обновилось 👇",
                   reply_markup=kb.main(m.chat.id))
    where = kb.LEGACY[m.text]
    if where in ("deals", "cheap"):
        await feed.show(m, where)
    elif where == "search":
        await search.show(m)
    else:
        await settings.show(m)
