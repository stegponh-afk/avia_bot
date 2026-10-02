"""
Бот в любом чате: пишешь «@AviaChecker_bot Сочи» — и прямо в переписке
выбираешь цену, чтобы отправить другу. Уходит карточка, как в канале:
картинка с маршрутом, ценой и погодой, под ней подпись и кнопки «Купить»
и «Следить в боте» — так бот находят те, кто о нём не слышал.

  пусто              — лучшие скидки из своего города (из ленты);
  «Сочи»             — самые дешёвые даты из своего города;
  «Киров Москва»     — то же из другого города.

Картинку Telegram берёт по адресу — её отдаёт cardweb.py. Сервер картинок
не поднят — уходит та же карточка текстом.

Telegram шлёт запрос на каждую набранную букву, поэтому ответы по
направлению держим в памяти и в API ходим только за новым маршрутом.
Режим включается у @BotFather: /setinline.
"""
import asyncio
import time
from datetime import date, timedelta

import aiohttp
from aiogram import Router
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery,
                           InlineQueryResultArticle, InlineQueryResultPhoto,
                           InlineQueryResultsButton, InputTextMessageContent,
                           LinkPreviewOptions)

import cardweb
import channel
import config as C
import db
import parse
import places
import post
import render
import tp
import users
import weather

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


async def _weather(items):
    """Погода на каждую дату — параллельно и не дольше 4 секунд на всё."""
    async def one(d):
        if "weather" not in d:
            d["weather"] = await weather.for_trip(d["dest"], d["depart"])
    try:
        await asyncio.wait_for(asyncio.gather(*(one(d) for d in items)), 4)
    except Exception:
        pass                              # не успели — карточки уйдут без погоды


def _text(d):
    """Подпись карточки: то же, что в канале, плюс откуда она."""
    lines = [f"✈️ <b>{channel.route(d)} — {render.money(d['price'])}</b>", channel.info(d)]
    if d.get("discount") and d.get("usual"):
        lines.append(f"обычно от {render.money(d['usual'])}, скидка {d['discount']}%")
    if d.get("weather"):
        lines.append(weather.line(d["weather"], places.name(d["dest"])))
    lines += ["", f"<i>Нашёл @{C.BOT_USERNAME} — следит за скидками на авиабилеты</i>"]
    return "\n".join(lines)


def _result(i, d, title_city=False):
    """Одна карточка выдачи: картинка (или текст), подпись и кнопки."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎫 Купить за {render.money(d['price'])}",
                              url=tp.buy_link(d, SUB))],
        [InlineKeyboardButton(text="🔔 Следить за скидками в боте",
                              url=f"https://t.me/{C.BOT_USERNAME}?start=inline")]])
    head = f"{places.name(d['dest'])} — {render.money(d['price'])}" if title_city else \
        f"{render.money(d['price'])} · {render.when_wd(d['depart'])}"
    disc = d.get("discount")
    desc = channel.info(d) + (f" · −{disc}% к обычной" if disc else "")
    rid = f"{i}-{d['origin']}-{d['dest']}-{d.get('depart')}-{d['price']}"
    if cardweb.ready():
        c = post.cand(d)
        if c["kind"] == "budget":          # в чатах бюджета нет — просто дёшево
            c = dict(c, kind="found")
        token = cardweb.put(d, c)
        return InlineQueryResultPhoto(
            id=rid, photo_url=cardweb.url(token), thumbnail_url=cardweb.url(token, thumb=True),
            photo_width=1280, photo_height=720, title=f"✈️ {head}", description=desc,
            caption=_text(d), parse_mode="HTML", reply_markup=kb)
    return InlineQueryResultArticle(
        id=rid, title=f"✈️ {head}", description=desc,
        input_message_content=InputTextMessageContent(
            message_text=_text(d), parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True)),
        reply_markup=kb)


@router.inline_query()
async def on_inline(q: InlineQuery):
    text = " ".join((q.query or "").split())
    home = users.origin(q.from_user.id)
    hint = InlineQueryResultsButton(text="Напиши город — например, Сочи",
                                    start_parameter="inline")
    items, title_city = [], False
    try:
        if not text:
            today = date.today().isoformat()
            items = sorted((dict(d) for d in db.load_feed(home)
                            if d.get("discount") and (d.get("depart") or "") > today),
                           key=lambda d: -d["discount"])[:8]
            title_city = True
        else:
            codes = parse._cities(text)
            origin, dest = (codes[0], codes[1]) if len(codes) >= 2 else \
                (home, codes[0]) if codes else (None, None)
            if dest and dest != origin:
                items = [dict(r) for r in await _dates(origin, dest)]
        await _weather(items)
    except Exception as e:
        print(f"  инлайн «{text}»: {e}")
    results = []
    for i, d in enumerate(items):
        try:
            results.append(_result(i, d, title_city))
        except Exception as e:
            print(f"  инлайн карточка {d.get('dest')}: {e}")
    print(f"  инлайн «{text}» от {q.from_user.id}: карточек {len(results)}")
    # Telegram хранит ответ cache_time секунд и не спрашивает заново:
    # долгий кэш прятал бы свежие цены и правки карточек
    await q.answer(results, cache_time=30, is_personal=True,
                   button=None if results else hint)
