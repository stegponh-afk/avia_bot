"""
/admin — служебное: состояние сборщика, опрос вручную, оформление.

Обычному человеку этого видеть не нужно, поэтому в меню команды нет.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import backup
import channel
import config as C
import db
import detect
import keyboards as kb
import notify
import poll
import ui
import users
from .common import edit, ensure, is_owner, say

router = Router()


def text(chat_id):
    last = db.meta_get("last_poll", "ещё не было")
    src = ["Travelpayouts" if C.TP_TOKEN else "⚠️ Travelpayouts — нет токена"]
    if C.AMADEUS_ENABLED:
        src.append("Amadeus")
    if C.AIRLINES_ENABLED:
        src.append("акции ({})".format(len(C.AIRLINE_PAGES)))
    if C.TG_WATCH_ENABLED and C.TG_CHANNELS:
        src.append("каналы ({})".format(len(C.TG_CHANNELS)))
    subs = db.active_subs()
    modes = {}
    for s in subs:
        for m in users.alerts_of(s) or {"ничего"}:
            modes[m] = modes.get(m, 0) + 1
    return ui.screen(
        "🛠 <b>Служебное</b>",
        ui.rows_block([
            f"Источники: {', '.join(src)}",
            f"Опрос каждые {C.POLL_EVERY_MIN} мин, последний: {last}",
            f"Цен в базе: {db.count_prices()}",
            "Бэкапы: " + (", ".join(f"{n[5:15]} ({mb} МБ)" for n, mb in backup.listing())
                          or "ещё не было"),
            f"Города вылета: {', '.join(db.origins())}",
            f"Подписчиков: {len(subs)} · "
            + ", ".join(f"{k} {v}" for k, v in sorted(modes.items())),
        ]),
        ui.rows_block([
            f"Скидка от {C.DEAL_PCT}%, суперскидка от {C.SUPER_PCT}%",
            f"Человеку не больше {C.USER_ALERTS_PER_RUN} сообщений за проход",
        ]),
        ui.rows_block([
            f"📣 Канал: {', '.join(C.CHANNEL_ORIGINS)}, скидка от {C.CHANNEL_DEAL_PCT}%, "
            f"падение от {C.CHANNEL_DROP_PCT}%",
            (f"Автопубликация: до {C.CHANNEL_DAY_MAX} в день, сегодня вышло "
             f"{channel.published_today()}; ночью только от {C.CHANNEL_NIGHT_PCT}%, без звука"
             if C.CHANNEL_AUTO else "Публикация по кнопке в черновике"),
            f"За сутки: в очереди {db.count_posts('queued')}, "
            f"опубликовано {db.count_posts('published')}, "
            f"снято {db.count_posts('stale')}",
        ]) if db.channel_id() else None)


@router.message(Command("admin"))
async def cmd_admin(m: Message):
    if not is_owner(m.from_user.id):
        return
    await show(m)


async def show(m: Message):
    await say(m, text(m.chat.id), kb.admin(users.style(m.chat.id)))


@router.callback_query(F.data == "admin:poll")
async def cb_poll(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer()
    bar = await ui.Progress(q.message, "🔎 <b>Опрашиваю источники</b>").start()
    try:
        alerts = await poll.run_once(progress=bar.step, on_found=channel.on_poll)
    finally:
        await bar.stop()
    # находки уже помечены отправленными — значит слать надо всем, не только себе
    sent = await notify.notify(alerts) if alerts else 0
    await q.message.answer(f"Находок: {len(alerts)}, сообщений разослано: {sent}.")


@router.callback_query(F.data == "admin:combo")
async def cb_combo(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer()
    bar = await ui.Progress(q.message, "🔀 <b>Ищу связки</b>").start()
    try:
        alerts = detect.pick(await poll.combos_due(force=True, progress=bar.step))
    finally:
        await bar.stop()
    sent = await notify.notify(alerts) if alerts else 0
    await q.message.answer(f"Связок к отправке: {len(alerts)}, разослано: {sent}.")


@router.callback_query(F.data == "admin:example")
async def cb_example(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer("Рисую пример")
    if not await channel.example():
        await q.message.answer("В ленте Москвы и Питера пока пусто — "
                               "появится после ближайшего опроса.")


@router.callback_query(F.data == "admin:nav")
async def cb_nav(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer()
    url, note = await channel.pin_navigation(q.bot)
    await q.message.answer(f"📌 {note.capitalize()}.", reply_markup=channel.done_kb("Открыть", url))


@router.callback_query(F.data == "admin:digest")
async def cb_digest(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer("Собираю")
    ok = await channel.digest_due(force=True)
    await q.message.answer("🏆 Дайджест опубликован." if ok else
                           "За неделю меньше трёх постов — дайджест не из чего собрать.")


@router.callback_query(F.data == "admin:weekend")
async def cb_weekend(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer("Собираю, это до минуты")
    ok = await channel.weekend_due(force=True)
    await q.message.answer("🏖 Подборка на выходные опубликована." if ok else
                           "На ближайшие выходные меньше трёх вариантов туда-обратно — "
                           "подборку не из чего собрать.")


@router.callback_query(F.data == "admin:style")
async def cb_style(q: CallbackQuery):
    chat = q.message.chat.id
    style = ui.OLD if users.style(chat) == ui.NEW else ui.NEW
    ensure(chat, q.from_user.full_name)
    db.set_style(chat, style)
    await edit(q.message, text(chat), kb.admin(style))
    await q.answer("Оформление: " + ("новое" if style == ui.NEW else "старое"))
