"""
🔗 Ссылки-отслеживания в админке: создать по названию, посмотреть переходы.

Создание — один вопрос «как назвать». Карточка ссылки — график за 30 дней
и три цифры за сегодня, неделю и месяц: переходы, новые люди, остались.
Всё только для владельцев.
"""
import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

import db
import keyboards as kb
import links
import ui
from .common import Ask, is_owner, say

router = Router()

NOTE = ("<i>Переход — открыл бота по ссылке. Новые — пришли в бота впервые, "
        "остались — из новых подписались и не заблокировали бота.</i>")


def list_screen():
    items = db.links()
    if not items:
        return (ui.screen("🔗 <b>Ссылки отслеживания</b>",
                          "Пока ни одной. Создай ссылку, дай её в YouTube, сторис или "
                          "рекламу — и здесь будет видно, сколько людей по ней пришло."),
                kb.links_list([]))
    rows = [(l["id"], f"{l['name']} · сегодня {links.today_count(l['slug'])}") for l in items]
    return (ui.screen("🔗 <b>Ссылки отслеживания</b>",
                      "Нажми на ссылку — покажу переходы за сегодня, неделю и месяц."),
            kb.links_list(rows))


def caption(link):
    return "\n".join([
        f"🔗 <b>{html.escape(link['name'])}</b>",
        f"<code>{links.url(link['slug'])}</code>",
        "",
        *links.stats_lines(link["slug"]),
        "",
        NOTE,
    ])


async def show_card(m: Message, link_id):
    link = db.get_link(link_id)
    if not link:
        await m.answer("Такой ссылки нет.")
        return
    await m.answer_photo(BufferedInputFile(links.chart(link), "link.png"),
                         caption=caption(link), reply_markup=kb.link_card(link_id))


@router.callback_query(F.data == "links:list")
async def cb_list(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    await state.clear()
    await q.answer()
    await say(q.message, *list_screen())


@router.callback_query(F.data.startswith("links:show:"))
async def cb_show(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer()
    await show_card(q.message, int(q.data.split(":")[2]))


@router.callback_query(F.data.startswith("links:upd:"))
async def cb_update(q: CallbackQuery):
    """Перерисовать график и цифры в том же сообщении."""
    if not is_owner(q.from_user.id):
        return
    link = db.get_link(int(q.data.split(":")[2]))
    if not link:
        await q.answer("Такой ссылки нет")
        return
    try:
        await q.message.edit_media(
            InputMediaPhoto(media=BufferedInputFile(links.chart(link), "link.png"),
                            caption=caption(link), parse_mode="HTML"),
            reply_markup=kb.link_card(link["id"]))
        await q.answer("Обновил")
    except Exception as e:
        await q.answer("Ничего не изменилось" if "not modified" in str(e) else "Не вышло")


@router.callback_query(F.data == "links:new")
async def cb_new(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    await state.set_state(Ask.link_name)
    await q.answer()
    await say(q.message, ui.screen(
        "➕ <b>Как назвать ссылку?</b>",
        "Название видишь только ты — пиши так, чтобы потом узнать: "
        "<code>youtube</code>, <code>сторис 1 октября</code>, "
        "<code>реклама у блогера</code>."))


@router.message(Ask.link_name)
async def got_name(m: Message, state: FSMContext):
    if not is_owner(m.from_user.id):
        return
    name = " ".join((m.text or "").split())[:60]
    if not name:
        await m.answer("Нужно название текстом, например <code>youtube</code>.")
        return
    await state.clear()
    link_id = links.create(name)
    link = db.get_link(link_id)
    await m.answer(f"✅ Ссылка «{html.escape(name)}» готова — нажми, чтобы скопировать:\n"
                   f"<code>{links.url(link['slug'])}</code>")
    await show_card(m, link_id)
