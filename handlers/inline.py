"""
Бот в любом чате: пишешь «@AviaChecker_bot Сочи» — и прямо в переписке
выбираешь цену, чтобы отправить другу. Уходит карточка, как в канале:
картинка с маршрутом, ценой и погодой, под ней подпись и кнопки «Купить»
и «Следить в боте» — так бот находят те, кто о нём не слышал.

  пусто              — лучшие скидки из своего города (из ленты);
  «Сочи»             — самые дешёвые даты из своего города;
  «Киров Москва»     — то же из другого города.

Картинку в ответ инлайн-режима Telegram принимает либо ссылкой (с нашего
IP он их не забирает — проверено), либо как уже загруженную в Telegram.
Поэтому бот загружает карточку в служебный закрытый канал (meta storage_chat,
подключается сам, когда бота делают админом второго канала), берёт её
file_id и сразу удаляет сообщение. file_id помним 6 часов: повторный запрос
отвечает мгновенно. Служебного канала нет — карточка уходит текстом.

Telegram шлёт запрос на каждую набранную букву, поэтому ответы по
направлению держим в памяти и в API ходим только за новым маршрутом.
Режим включается у @BotFather: /setinline.
"""
import asyncio
import io
import time
from datetime import date, timedelta

import aiohttp
from aiogram import Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultCachedPhoto, InlineQueryResultsButton,
                           InputTextMessageContent, LinkPreviewOptions)
from PIL import Image

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
_photos = {}           # карточка -> (когда, file_id)
TTL = 20 * 60
PHOTO_TTL = 6 * 3600
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
    """Погода на каждую дату — параллельно и не дольше 3 секунд на всё."""
    async def one(d):
        if "weather" not in d:
            d["weather"] = await weather.for_trip(d["dest"], d["depart"])
    try:
        await asyncio.wait_for(asyncio.gather(*(one(d) for d in items)), 3)
    except Exception:
        pass                              # не успели — карточки уйдут без погоды


def _cand(d):
    c = post.cand(d)
    return dict(c, kind="found") if c["kind"] == "budget" else c   # в чатах бюджета нет


_inflight = {}         # карточка -> задача загрузки: одна карточка грузится один раз


def _jpeg(png):
    """JPEG втрое легче PNG — грузится быстрее, на вид не отличить."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


async def _upload(bot, store, d, c, key):
    """
    Нарисовать карточку, загрузить в служебный канал, запомнить file_id
    и удалить сообщение. Telegram просит подождать (лимит сообщений
    в канал) — ждём и пробуем ещё раз.
    """
    data = await asyncio.to_thread(lambda: _jpeg(post.image(d, c)))
    for attempt in range(3):
        try:
            msg = await bot.send_photo(store, BufferedInputFile(data, "card.jpg"),
                                       disable_notification=True)
            break
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
    fid = msg.photo[-1].file_id
    _photos[key] = (time.time(), fid)
    try:
        await bot.delete_message(store, msg.message_id)   # file_id живёт и без сообщения
    except Exception as e:
        print(f"  инлайн: не удалил картинку из хранилища — {e}")
    return fid


def _task(bot, store, d):
    """Задача загрузки карточки: готовая из памяти, уже идущая или новая."""
    c = _cand(d)
    key = (d["origin"], d["dest"], d.get("depart"), d["price"], c["kind"], c.get("pct"),
           (d.get("weather") or {}).get("day"))
    hit = _photos.get(key)
    if hit and time.time() - hit[0] < PHOTO_TTL:
        done = asyncio.get_running_loop().create_future()
        done.set_result(hit[1])
        return done
    if key not in _inflight:
        t = asyncio.create_task(_upload(bot, store, d, c, key))

        def finished(task, k=key):
            _inflight.pop(k, None)
            if not task.cancelled() and task.exception():   # догружалась в фоне и упала
                print(f"  инлайн картинка {k[1]}: {task.exception()}")
        t.add_done_callback(finished)
        _inflight[key] = t
    return _inflight[key]


async def _photos_for(bot, items):
    """
    file_id карточек по порядку, None — не успели или нет хранилища.
    Не успели за 6 секунд — отвечаем текстом, но загрузку не бросаем:
    она доедет в фоне, и следующий запрос уже будет с картинками.
    """
    store = db.meta_get("storage_chat")
    if not store or not items:
        return [None] * len(items)
    tasks = [_task(bot, int(store), d) for d in items]
    await asyncio.wait(tasks, timeout=6)
    out = []
    for d, t in zip(items, tasks):
        if t.done() and not t.cancelled() and t.exception() is None:
            out.append(t.result())
        else:
            if t.done() and not t.cancelled():
                print(f"  инлайн картинка {d.get('dest')}: {t.exception()}")
            out.append(None)
    if None in out:
        print(f"  инлайн: картинок готово {len(out) - out.count(None)} из {len(out)}, "
              "остальные догружаются в фоне")
    return out


def _text(d):
    """Подпись карточки: то же, что в канале, плюс откуда она."""
    lines = [f"✈️ <b>{channel.route(d)} — {render.money(d['price'])}</b>", channel.info(d)]
    if d.get("discount") and d.get("usual"):
        lines.append(f"обычно от {render.money(d['usual'])}, скидка {d['discount']}%")
    if d.get("weather"):
        lines.append(weather.line(d["weather"], places.name(d["dest"])))
    lines += ["", f"<i>Нашёл @{C.BOT_USERNAME} — следит за скидками на авиабилеты</i>"]
    return "\n".join(lines)


def _result(i, d, photo=None, title_city=False):
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
    if photo:
        return InlineQueryResultCachedPhoto(
            id=rid, photo_file_id=photo, title=f"✈️ {head}", description=desc,
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
                           key=lambda d: -d["discount"])[:6]
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
    photos = await _photos_for(q.bot, items)
    results = []
    for i, (d, ph) in enumerate(zip(items, photos)):
        try:
            results.append(_result(i, d, ph, title_city))
        except Exception as e:
            print(f"  инлайн карточка {d.get('dest')}: {e}")
    print(f"  инлайн «{text}» от {q.from_user.id}: карточек {len(results)}, "
          f"с картинкой {sum(1 for p in photos if p)}")
    # Telegram хранит ответ cache_time секунд и не спрашивает заново:
    # долгий кэш прятал бы свежие цены и правки карточек
    await q.answer(results, cache_time=30, is_personal=True,
                   button=None if results else hint)
