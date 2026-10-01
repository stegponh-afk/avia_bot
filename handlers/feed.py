"""
🔥 Скидки: что сейчас дешевле обычного — главный экран бота.

Две вкладки в одном сообщении:
  🔥 Скидки        — отсортировано по проценту к обычной цене маршрута;
  💰 Дешевле всего — просто самые дешёвые билеты за сутки.

Первое отвечает на «где выгодно», второе — на «куда слетать подешевле».
Это разные вопросы, и людям нужны оба.
"""
from datetime import date

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import config as C
import db
import detect
import keyboards as kb
import places
import post
import render
import ui
import users
from . import search
from .common import edit, say

router = Router()


def deals_for(chat_id):
    """Скидки из ленты и связки, отфильтрованные под этого человека."""
    s = users.get(chat_id)
    origin = users.origin(chat_id)
    today = date.today().isoformat()
    items = list(db.load_feed(origin))
    for c in db.load_combos(origin, limit=20):
        c["why"] = ["связка"]
        detect.discount(c)
        items.append(c)
    items = [d for d in items
             if d.get("discount") and (d.get("depart") or today) >= today
             and users.fits(s, d)]
    items.sort(key=lambda d: -d["discount"])
    return items


def screen(chat_id, tab):
    """Текст и кнопки вкладки."""
    origin = places.name(users.origin(chat_id))
    if tab == "cheap":
        a, b = users.dates(chat_id)
        today = date.today().isoformat()
        rows = db.top_cheap(users.origin(chat_id), hours=24, limit=10,
                            pmax=users.budget(chat_id),
                            date_from=max(a or today, today), date_to=b)
        if not rows:
            return (ui.screen("💰 <b>Дешевле всего</b>",
                              "За сутки ничего не накопилось — загляни попозже."),
                    kb.feed(tab, []))
        body = "\n".join(f"{i}. {render.row(r)}" for i, r in enumerate(rows, 1))
        return (ui.screen("💰 <b>Дешевле всего</b>",
                          f"вылет из города {origin}, в одну сторону", body,
                          footer="Нажми номер — покажу все даты 👇"),
                kb.feed(tab, [(places.name(r["dest"]), f"dest:{r['dest']}")
                              for r in rows]))

    items = deals_for(chat_id)[:10]
    if not items:
        waiting = ("Я проверяю цены каждые {} минут и сразу напишу, как появятся."
                   .format(C.POLL_EVERY_MIN) if users.mode(chat_id) != "off"
                   else "Уведомления выключены — включить можно в ⚙️ Настройках.")
        return (ui.screen("🔥 <b>Скидки сейчас</b>",
                          f"вылет из города {origin}",
                          "Больших скидок сейчас нет — они бывают не каждый день.",
                          waiting, footer="А пока — самые дешёвые билеты во вкладке 💰"),
                kb.feed(tab, []))
    body = "\n".join(render.feed_item(i, d) for i, d in enumerate(items, 1))
    return (ui.screen("🔥 <b>Скидки сейчас</b>",
                      f"вылет из города {origin}, к обычной цене маршрута", body,
                      footer="Нажми номер — покажу билет 👇"),
            kb.feed(tab, [(f"{places.name(d['dest'])} −{d['discount']}%",
                           f"deal:{d['origin']}:{d['dest']}") for d in items]))


async def show(m: Message, tab="deals"):
    await say(m, *screen(m.chat.id, tab))


@router.callback_query(F.data.startswith("feed:"))
async def cb_tab(q: CallbackQuery):
    await edit(q.message, *screen(q.message.chat.id, q.data.split(":", 1)[1]))
    await q.answer()


@router.callback_query(F.data.startswith("deal:"))
async def cb_deal(q: CallbackQuery):
    """Карточка находки из ленты: как пришла бы уведомлением."""
    _, origin, dest = q.data.split(":")
    d = db.get_deal(origin, dest)
    if d and d.get("legs"):
        d["why"] = ["связка"]
        detect.discount(d)
    if not d or not d.get("discount"):
        await q.answer("Эта скидка уже закончилась, смотрю текущие цены")
        await search.show_dest(q.message, dest)
        return
    await q.answer()
    await post.send(q.bot, q.message.chat.id, d, "bot_feed", users.style(q.message.chat.id))
