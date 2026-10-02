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
                           InputMediaPhoto, InputTextMessageContent,
                           LinkPreviewOptions)
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


_inflight = {}         # карточка -> future с file_id: одна карточка грузится один раз


def _jpeg(png):
    """JPEG втрое легче PNG — грузится быстрее, на вид не отличить."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def _key(d, c):
    return (d["origin"], d["dest"], d.get("depart"), d["price"], c["kind"], c.get("pct"),
            (d.get("weather") or {}).get("day"))


async def _send(bot, store, datas):
    """
    Загрузить картинки одним запросом: альбомом, если их больше одной.
    Альбом — одно обращение к Telegram вместо шести, и лимит на сообщения
    в канал не тормозит. Просит подождать — ждём и пробуем ещё раз.
    """
    for attempt in range(3):
        try:
            if len(datas) == 1:
                return [await bot.send_photo(store, BufferedInputFile(datas[0], "c.jpg"),
                                             disable_notification=True)]
            return await bot.send_media_group(
                store, [InputMediaPhoto(media=BufferedInputFile(x, f"c{i}.jpg"))
                        for i, x in enumerate(datas)], disable_notification=True)
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 0.5)


async def _batch(bot, store, jobs):
    """
    jobs — [(ключ, находка, разметка)]: нарисовать все параллельно, загрузить
    альбомами по 10, разложить file_id по ожидающим и убрать сообщения.
    """
    try:
        datas = await asyncio.gather(*(asyncio.to_thread(lambda d=d, c=c: _jpeg(post.image(d, c)))
                                       for _, d, c in jobs))
        for i in range(0, len(jobs), 10):
            part, msgs = jobs[i:i + 10], await _send(bot, store, datas[i:i + 10])
            for (key, _, _), m in zip(part, msgs):
                fid = m.photo[-1].file_id
                _photos[key] = (time.time(), fid)
                fut = _inflight.pop(key, None)
                if fut and not fut.done():
                    fut.set_result(fid)
            try:                                  # file_id живёт и без сообщения
                await bot.delete_messages(store, [m.message_id for m in msgs])
            except Exception as e:
                print(f"  инлайн: не удалил картинки из хранилища — {e}")
    except Exception as e:
        print(f"  инлайн: картинки не загрузились — {type(e).__name__}: {e}")
        for key, _, _ in jobs:
            fut = _inflight.pop(key, None)
            if fut and not fut.done():
                fut.set_result(None)


async def _photos_for(bot, items):
    """
    file_id карточек по порядку, None — нет картинки. Ждём до 7 секунд;
    не успели — загрузка не бросается, доедет в фоне, и следующий запрос
    уже будет с картинками.
    """
    store = db.meta_get("storage_chat")
    if not store or not items:
        return [None] * len(items)
    loop = asyncio.get_running_loop()
    futs, jobs = [], []
    for d in items:
        c = _cand(d)
        key = _key(d, c)
        hit = _photos.get(key)
        if hit and time.time() - hit[0] < PHOTO_TTL:
            f = loop.create_future()
            f.set_result(hit[1])
        elif key in _inflight:
            f = _inflight[key]
        else:
            f = _inflight[key] = loop.create_future()
            jobs.append((key, d, c))
        futs.append(f)
    if jobs:
        asyncio.create_task(_batch(bot, int(store), jobs))
    await asyncio.wait(futs, timeout=7)
    out = [f.result() if f.done() else None for f in futs]
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
    if any(photos):
        # без смеси: картинки и текст вперемешку на телефоне выглядят как
        # «не то не сё» — показываем только готовые картинки, остальные
        # догрузятся и появятся при следующем запросе
        pairs = [(d, ph) for d, ph in zip(items, photos) if ph]
    else:
        pairs = list(zip(items, photos))
    results = []
    for i, (d, ph) in enumerate(pairs):
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
