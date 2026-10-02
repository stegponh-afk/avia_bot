"""
Бот в любом чате: пишешь «@AviaChecker_bot Сочи» — и прямо в переписке
выбираешь цену, чтобы отправить другу. Каждая такая карточка — с кнопкой
«Купить» и ссылкой на бота: так бот находят те, кто о нём не слышал.

  пусто              — лучшие скидки из своего города (из ленты);
  «Сочи»             — самые дешёвые даты из своего города;
  «Киров Москва»     — то же из другого города.

Telegram шлёт запрос на каждую набранную букву, поэтому ответы по
направлению держим в памяти и в API ходим только за новым маршрутом.
Режим включается у @BotFather: /setinline.
"""
import time
from datetime import date, timedelta

import aiohttp
from aiogram import Router
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery,
                           InlineQueryResultArticle, InlineQueryResultsButton,
                           InputTextMessageContent, LinkPreviewOptions)

import channel
import config as C
import db
import parse
import places
import render
import tp
import users

router = Router()
_cache = {}            # (откуда, куда) -> (когда, строки)
TTL = 20 * 60
SUB = "bot_inline"


async def _dates(origin, dest):
    """Самые дешёвые даты по направлению на 4 месяца вперёд — из кэша или API."""
    key = (origin, dest)
    if key in _cache and time.time() - _cache[key][0] < TTL:
        return _cache[key][1]
    until = (date.today() + timedelta(days=120)).isoformat()
    async with aiohttp.ClientSession() as s:
        rows = await tp.Travelpayouts(s, origin).route_dates(dest, until=until)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    rows = sorted((r for r in rows if (r.get("depart") or "") >= tomorrow),
                  key=lambda r: r["price"])[:6]
    _cache[key] = (time.time(), rows)
    return rows


def _result(i, d, title_city=False):
    """Одна карточка выдачи: заголовок с ценой и сообщение, которое уйдёт в чат."""
    route = channel.route(d)
    info = channel.info(d)
    disc = d.get("discount")
    text = [f"✈️ <b>{route} — {render.money(d['price'])}</b>", info]
    if disc and d.get("usual"):
        text.append(f"обычно от {render.money(d['usual'])}, скидка {disc}%")
    text += ["", f"<i>Нашёл @{C.BOT_USERNAME} — следит за скидками на авиабилеты</i>"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎫 Купить за {render.money(d['price'])}",
                              url=tp.buy_link(d, SUB))],
        [InlineKeyboardButton(text="🔔 Следить за скидками в боте",
                              url=f"https://t.me/{C.BOT_USERNAME}?start=inline")]])
    head = f"{places.name(d['dest'])} — {render.money(d['price'])}" if title_city else \
        f"{render.money(d['price'])} · {render.when_wd(d['depart'])}"
    desc = info + (f" · −{disc}% к обычной" if disc else "")
    return InlineQueryResultArticle(
        id=f"{i}-{d['origin']}-{d['dest']}-{d.get('depart')}-{d['price']}",
        title=f"✈️ {head}", description=desc,
        input_message_content=InputTextMessageContent(
            message_text="\n".join(text), parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True)),
        reply_markup=kb)


@router.inline_query()
async def on_inline(q: InlineQuery):
    text = " ".join((q.query or "").split())
    home = users.origin(q.from_user.id)
    hint = InlineQueryResultsButton(text="Напиши город — например, Сочи",
                                    start_parameter="inline")
    results = []
    try:
        if not text:
            today = date.today().isoformat()
            deals = sorted((d for d in db.load_feed(home)
                            if d.get("discount") and (d.get("depart") or "") > today),
                           key=lambda d: -d["discount"])[:8]
            results = [_result(i, d, title_city=True) for i, d in enumerate(deals)]
        else:
            codes = parse._cities(text)
            origin, dest = (codes[0], codes[1]) if len(codes) >= 2 else \
                (home, codes[0]) if codes else (None, None)
            if dest and dest != origin:
                rows = await _dates(origin, dest)
                results = [_result(i, d) for i, d in enumerate(rows)]
    except Exception as e:
        print(f"  инлайн «{text}»: {e}")
    print(f"  инлайн «{text}» от {q.from_user.id}: карточек {len(results)}")
    await q.answer(results, cache_time=300, is_personal=True,
                   button=None if results else hint)
