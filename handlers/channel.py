"""Кнопки под черновиком для канала: опубликовать или пропустить."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated

import channel
import config as C
import db
from .common import is_owner

router = Router()


@router.callback_query(F.data.startswith("ch:pub:"))
async def cb_publish(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        await q.answer("Публиковать может только владелец")
        return
    await q.answer("Проверяю цену и публикую…")
    post_id = int(q.data.split(":")[2])
    try:
        ok, note, url = await channel.publish(q.bot, post_id)
    except Exception as e:
        await q.message.answer(f"Не опубликовалось: {str(e)[:200]}")
        return
    if ok:
        await q.message.edit_reply_markup(reply_markup=channel.done_kb("✅ Опубликовано — открыть", url))
        if note:
            await q.message.reply(f"Опубликовано, {note}.")
    else:
        await q.message.edit_reply_markup(reply_markup=channel.done_kb("⛔ Не опубликовано"))
        await q.message.reply(f"Не опубликовал: {note}.")


@router.callback_query(F.data.startswith("ch:del:"))
async def cb_delete(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    try:
        ok = await channel.delete(q.bot, int(q.data.split(":")[2]))
    except Exception as e:
        await q.answer(f"Не удалилось: {str(e)[:150]}", show_alert=True)
        return
    await q.message.edit_reply_markup(
        reply_markup=channel.done_kb("🗑 Удалено из канала" if ok else "Поста уже нет"))
    await q.answer("Удалено" if ok else "Поста уже нет")


@router.callback_query(F.data.startswith("ch:skip:"))
async def cb_skip(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    db.set_post(int(q.data.split(":")[2]), status="skipped")
    await q.message.edit_reply_markup(reply_markup=channel.done_kb("❌ Пропущено"))
    await q.answer("Пропущено")


@router.my_chat_member()
async def on_channel_rights(ev: ChatMemberUpdated):
    """
    Бота добавили в канал, убрали или поменяли права.

    Если это сделал владелец и публиковать можно — запоминаем канал.
    В любом случае пишем владельцам, что произошло: id канала руками
    переписывают с ошибками, а права бота из клиента не видно.
    """
    if ev.chat.type != "channel":
        return
    st = ev.new_chat_member
    can_post = st.status == "creator" or (
        st.status == "administrator" and bool(getattr(st, "can_post_messages", False)))
    title = ev.chat.title or ev.chat.username or ev.chat.id
    main = db.channel_id()
    if can_post and is_owner(ev.from_user.id) and main and str(main) != str(ev.chat.id):
        # основной канал уже есть — второй канал служебный: в него бот
        # загружает картинки карточек для ответов в чатах (@бот Сочи)
        db.meta_set("storage_chat", ev.chat.id)
        text = (f"📦 Канал «{title}» подключён как хранилище картинок для бота в чатах. "
                "Подписчиков туда звать не нужно, публиковать в него ничего не буду — "
                "картинки загружаются и сразу удаляются.")
    elif can_post and is_owner(ev.from_user.id):
        db.meta_set("channel_id", ev.chat.id)
        text = (f"📣 Канал «{title}» подключён (id <code>{ev.chat.id}</code>). "
                "Черновики буду присылать сюда, публиковать — туда.")
    elif st.status == "administrator":
        text = (f"📣 Я админ в «{title}», но без права публиковать сообщения. "
                "Включи его в правах администратора.")
    elif st.status in ("left", "kicked"):
        if db.meta_get("channel_id") == str(ev.chat.id):
            db.meta_set("channel_id", "")
        if db.meta_get("storage_chat") == str(ev.chat.id):
            db.meta_set("storage_chat", "")
        text = f"📣 Меня убрали из «{title}» — публиковать туда больше не могу."
    else:
        text = f"📣 «{title}»: мой статус {st.status}. Чтобы публиковать, сделай меня админом."
    for owner in C.OWNER_IDS:
        try:
            await ev.bot.send_message(owner, text)
        except Exception:
            pass


@router.callback_query(F.data == "ch:noop")
async def cb_noop(q: CallbackQuery):
    await q.answer()
