"""
🔗 Ссылки в админке — в канал и в бота, с ценой рекламы.

Создание по шагам: название → куда ведёт (📣 канал / 🤖 бот) → сколько
заплатили (0 — бесплатно). Карточка ссылки — график за 30 дней и цифры за
сегодня, неделю и месяц: пришли, новые, остались, и цена за оставшегося.
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

NOTE_BOT = ("<i>Переход — открыл бота по ссылке. Новые — пришли в бота впервые, "
            "остались — из новых подписались и не заблокировали бота.</i>")
NOTE_CHANNEL = ("<i>Вступили — по этой ссылке в канал. Остались — из них до сих пор "
                "подписаны.</i>")


def list_screen():
    items = db.links()
    if not items:
        return (ui.screen("🔗 <b>Ссылки</b>",
                          "Пока ни одной. Создай ссылку в канал или в бота, отдай её "
                          "в рекламу, YouTube или сторис — и здесь будет видно, сколько "
                          "людей по ней пришло, сколько осталось и во сколько обошлись."),
                kb.links_list([]))
    rows = [(l["id"], f"{links.TARGET.get(l['target'] or 'bot')} {l['name']} · "
                      f"сегодня {links.today_count(l)}") for l in items]
    return (ui.screen("🔗 <b>Ссылки</b>",
                      "📣 — в канал, 🤖 — в бота. Нажми на ссылку — покажу, сколько "
                      "пришло за сегодня, неделю и месяц."),
            kb.links_list(rows))


def caption(link):
    where = "📣 в канал" if links.is_channel(link) else "🤖 в бота"
    return "\n".join(x for x in [
        f"🔗 <b>{html.escape(link['name'])}</b> · {where}",
        f"<code>{links.url(link)}</code>",
        "",
        *links.stats_lines(link),
        links.cost_line(link),
        "",
        NOTE_CHANNEL if links.is_channel(link) else NOTE_BOT,
    ] if x is not None)


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


# ---------- новая ссылка: название → куда → цена ----------

@router.callback_query(F.data == "links:new")
async def cb_new(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    await state.set_state(Ask.link_name)
    await q.answer()
    await say(q.message, ui.screen(
        "➕ <b>Как назвать ссылку?</b>",
        "Название видишь только ты — пиши так, чтобы потом узнать: "
        "<code>реклама в Путешествия дёшево 10 окт</code>, <code>youtube</code>, "
        "<code>сторис 1 октября</code>."))


@router.message(Ask.link_name)
async def got_name(m: Message, state: FSMContext):
    if not is_owner(m.from_user.id):
        return
    name = " ".join((m.text or "").split())[:60]
    if not name:
        await m.answer("Нужно название текстом, например <code>youtube</code>.")
        return
    await state.update_data(link_name=name)
    await state.set_state(None)
    await say(m, ui.screen(f"🔗 <b>{html.escape(name)}</b>", "Куда ведёт ссылка?"),
              kb._kb([[kb._b("📣 В канал", "links:to:channel"), kb._b("🤖 В бота", "links:to:bot")]]))


@router.callback_query(F.data.startswith("links:to:"))
async def cb_target(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    if not (await state.get_data()).get("link_name"):
        await q.answer("Начни заново: ➕ Новая ссылка")
        return
    await state.update_data(link_target=q.data.split(":")[2])
    await state.set_state(Ask.link_cost)
    await q.answer()
    await q.message.answer("Сколько заплатил за рекламу с этой ссылкой? Число в рублях, "
                           "<code>0</code> — бесплатно или бартер.")


@router.message(Ask.link_cost)
async def got_cost(m: Message, state: FSMContext):
    if not is_owner(m.from_user.id):
        return
    digits = "".join(ch for ch in (m.text or "") if ch.isdigit())
    if not digits:
        await m.answer("Нужна сумма цифрами, например <code>3000</code>, или <code>0</code>.")
        return
    d = await state.get_data()
    await state.clear()
    try:
        link_id = await links.create(m.bot, d["link_name"], d.get("link_target", "bot"), int(digits))
    except Exception as e:
        await m.answer("Не получилось создать ссылку в канал: дай боту в канале право "
                       f"«Пригласительные ссылки».\n<i>{html.escape(str(e)[:150])}</i>")
        return
    link = db.get_link(link_id)
    await m.answer(f"✅ Ссылка «{html.escape(link['name'])}» готова — нажми, чтобы скопировать, "
                   f"и отдай в рекламу:\n<code>{links.url(link)}</code>")
    await show_card(m, link_id)
