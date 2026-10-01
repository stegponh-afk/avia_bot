"""
Рассылка находок подписчикам.

Сбор один на всех, фильтры у каждого свои: город, даты, бюджет и режим
(суперскидки / все скидки / всё дешевле бюджета). И потолок сообщений
на человека за проход — лучше три отличные находки, чем десять средних.
"""
import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import config as C
import db
import post
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
    from datetime import datetime
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


async def notify(alerts, only=None):
    """Рассылает находки. Возвращает сколько сообщений ушло."""
    bot = make_bot()
    subs = db.active_subs()
    if only is not None:
        subs = [s for s in subs if s["chat_id"] == only]

    sent = 0
    cache = {}                       # одна картинка на находку, дальше — по file_id
    for s in subs:
        mine = [d for d in alerts if users.wants(s, d)]
        mine.sort(key=lambda d: (-(d.get("discount") or 0), d["price"]))
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
