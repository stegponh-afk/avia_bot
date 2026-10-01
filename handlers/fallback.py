"""
Свободный текст — последним. Сперва пробуем прочесть как маршрут с датами,
потом как название города. Кнопки кнопками, а написать быстрее.
"""
from aiogram import F, Router
from aiogram.types import Message

import keyboards as kb
import parse
import places
import ui
import users
from . import search
from .common import allowed, say

router = Router()


@router.message(F.text)
async def free_text(m: Message):
    if not allowed(m.from_user.id):
        return
    parsed = parse.parse_trip(m.text or "", users.origin(m.chat.id))
    if parsed:
        await search.run_trip(m, *parsed)
        return
    codes = parse._cities(m.text or "")
    if len(codes) == 2 and codes[0] != codes[1]:        # «Киров Москва»
        await search.show_dest(m, codes[1], codes[0])
        return
    code = places.find(m.text or "")
    if code:
        await search.show_dest(m, code)
        return
    await say(m, ui.screen(
        "🤔 <b>Не понял</b>",
        "Напиши город, можно сразу с датами:",
        "<code>Сочи</code> или <code>Сочи 18.10 25.10</code>"), kb.main(m.chat.id))
