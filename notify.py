"""
Рассылка находок подписчикам.

Сбор один на всех, фильтры у каждого свои: город, даты, бюджет и режим
(суперскидки / все скидки / всё дешевле бюджета). И потолок сообщений
на человека за проход — лучше три отличные находки, чем десять средних.
"""
import asyncio
from datetime import date, datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import config as C
import db
import detect
import keyboards as kb
import post
import render
import ui
import users

_bot = None


def make_bot():
    global _bot
    if _bot is None:
        session = AiohttpSession(proxy=C.BOT_PROXY) if C.BOT_PROXY else None
        _bot = Bot(C.BOT_TOKEN, session=session,
                   default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                link_preview_is_disabled=True))
    return _bot


async def problem(key, text):
    """
    Сообщить владельцам о поломке. Одна и та же проблема (key) — не чаще
    раза в PROBLEM_REPEAT_H часов: сломанный опрос каждые 45 минут не должен
    превращаться в 30 одинаковых сообщений за ночь.
    """
    last = db.meta_get(f"problem:{key}")
    if last:
        age = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
        if age < C.PROBLEM_REPEAT_H:
            return
    db.meta_set(f"problem:{key}", datetime.now().isoformat(timespec="seconds"))
    print(f"  ПРОБЛЕМА {key}: {text}")
    for owner in C.OWNER_IDS:
        try:
            await make_bot().send_message(owner, f"⚠️ {text}")
        except Exception:
            pass


async def resolved(key, text):
    """Проблема ушла — сказать один раз и забыть."""
    if not db.meta_get(f"problem:{key}"):
        return
    db.meta_set(f"problem:{key}", "")
    for owner in C.OWNER_IDS:
        try:
            await make_bot().send_message(owner, f"✅ {text}")
        except Exception:
            pass


def quiet_now():
    """Ночь по времени сервера (BOT_QUIET): людей не будим."""
    start, end = C.BOT_QUIET
    h = datetime.now().hour
    return h >= start or h < end if start > end else start <= h < end


async def notify(alerts, only=None):
    """
    Рассылает находки. Возвращает сколько сообщений ушло.
    Ночью не шлёт, а откладывает: утром morning() пришлёт одной сводкой.
    """
    bot = make_bot()
    subs = db.active_subs()
    if only is not None:
        subs = [s for s in subs if s["chat_id"] == only]

    sent = 0
    night = quiet_now()
    cache = {}                       # одна картинка на находку, дальше — по file_id
    for s in subs:
        mine = [d for d in alerts if users.wants(s, d)]
        mine.sort(key=lambda d: (-(d.get("discount") or 0), d["price"]))
        if night:
            for d in mine[:C.USER_ALERTS_PER_RUN]:
                db.add_pending(s["chat_id"], detect.key(d), d)
            continue
        for d in mine[:C.USER_ALERTS_PER_RUN]:
            try:
                await post.send(bot, s["chat_id"], d, "bot_alert", s["style"], cache)
                sent += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError:
                db.stop_sub(s["chat_id"])          # бота заблокировали
                break
            except Exception as e:
                print("  отправка {}: {}".format(s["chat_id"], e))
            await asyncio.sleep(0.05)
    return sent


async def morning():
    """
    Утро: ночные находки — каждому одним сообщением. Одна — сразу карточкой,
    несколько — списком с кнопками; кнопка откроет карточку по свежей ленте,
    а ушедшую скидку честно покажет календарём направления.
    """
    if quiet_now():
        return 0
    bot, today, sent = make_bot(), date.today().isoformat(), 0
    for chat in db.pending_chats():
        items = [d for d in db.take_pending(chat, C.NIGHT_KEEP)
                 if (d.get("depart") or today) >= today]
        s = db.get_sub(chat)
        if not items or not s or not s["active"]:
            continue
        try:
            if len(items) == 1:
                await post.send(bot, chat, items[0], "bot_alert", s["style"])
            else:
                body = "\n".join(render.feed_item(i, d) for i, d in enumerate(items, 1))
                await ui.push(bot, chat, ui.screen(
                    "☀️ <b>Пока ты спал</b>",
                    f"За ночь нашёл {len(items)} {render.plural(len(items), 'скидку', 'скидки', 'скидок')}:",
                    body, footer="Нажми — покажу билет. Цены могли измениться 👇"),
                    kb.morning(items), s["style"] or C.STYLE)
            sent += 1
        except TelegramForbiddenError:
            db.stop_sub(chat)
        except Exception as e:
            print(f"  утренняя сводка {chat}: {e}")
        await asyncio.sleep(0.05)
    if sent:
        print(f"  утренние сводки: {sent}")
    return sent


async def weekend_due(force=False):
    """
    «Куда на выходные» в личку — тем, у кого включена эта галочка, в тот же
    день и час, что и в канале. Подборка своя для каждого города вылета:
    считаем один раз на город, рассылаем всем из него. Меньше двух
    вариантов — этому городу в эту неделю не пишем.
    """
    import channel
    now = datetime.now()
    week = now.strftime("%G-%V")
    if not force:
        if now.weekday() != C.CHANNEL_WEEKEND_DAY or now.hour < C.CHANNEL_WEEKEND_HOUR:
            return 0
        if db.meta_get("weekend_bot_week") == week:
            return 0
    db.meta_set("weekend_bot_week", week)
    subs = [s for s in db.active_subs() if "weekend" in users.alerts_of(s)]
    if not subs:
        return 0
    bot, posts, sent = make_bot(), {}, 0
    for s in subs:
        origin = s["origin"] or C.ORIGIN
        if origin not in posts:
            picks = await channel.weekend_picks(origin)
            posts[origin] = (channel.weekend_post({origin: picks}, sub="bot_weekend", tags=False)
                             if len(picks) >= 2 else None)
        if not posts[origin]:
            continue
        png, text = posts[origin]
        try:
            await channel.send_post(bot, s["chat_id"], png, text,
                                    rich_ok=(s["style"] or C.STYLE) == ui.NEW)
            sent += 1
        except TelegramForbiddenError:
            db.stop_sub(s["chat_id"])
        except Exception as e:
            print(f"  выходные в бот {s['chat_id']}: {e}")
        await asyncio.sleep(0.05)
    print(f"  выходные в бот: городов {len(posts)}, сообщений {sent}")
    return sent
