"""
Рассылка находок подписчикам.

Сбор один на всех, фильтры у каждого свои: город, даты, бюджет и режим
(суперскидки / все скидки / всё дешевле бюджета). И потолок сообщений
на человека за проход — лучше три отличные находки, чем десять средних.
"""
import asyncio
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import card
import channel
import config as C
import db
import detect
import keyboards as kb
import places
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
                db.log_sent(s["chat_id"], d, "alert")
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


def morning_post(items):
    """
    Утренняя сводка в стиле подборок канала: картинка со списком
    «−45% · Москва → Сочи · 3 200 ₽» и под ней те же находки текстом.
    """
    n = len(items)
    found = f"{n} {render.plural(n, 'находку', 'находки', 'находок')}"
    origins = {d["origin"] for d in items}
    subtitle = f"за ночь нашёл {found}" + (
        f" · вылет из города {places.name(origins.pop())}" if len(origins) == 1 else "")
    rows = [(d["discount"] if d.get("discount") else "бюджет", channel.route(d),
             render.money(d["price"])) for d in items]
    png = card.digest("Пока ты спал", subtitle, rows, footnote="цены на момент находки")
    body = "\n".join(render.feed_item(i, d) if d.get("discount") else
                     f"{i}. {places.name(d['dest'])} — <b>{render.money(d['price'])}</b>"
                     f" · {render.when_wd(d['depart'])}"
                     for i, d in enumerate(items, 1))
    text = "\n\n".join([f"☀️ <b>Пока ты спал</b>\nза ночь нашёл {found}", body,
                        "<i>Цены могли измениться — нажми, покажу билет по свежим ценам.</i>"])
    return png, text


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
                png, text = morning_post(items)
                await channel.send_post(bot, chat, png, text, kb.morning(items),
                                        rich_ok=(s["style"] or C.STYLE) == ui.NEW)
            for d in items:
                db.log_sent(chat, d, "morning")
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


MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")


def recap_post(chat_id, since, until, title):
    """
    Итоги месяца человека: (картинка, текст) или None, если меньше трёх находок.
    Считаем по журналу sent_log — ровно то, что бот прислал.
    """
    rows = db.sent_between(chat_id, since, until)
    if len(rows) < 3:
        return None
    best = {}
    for r in rows:                       # одно направление — одна строка, лучшая скидка
        k = (r["origin"], r["dest"])
        if r["discount"] and (k not in best or r["discount"] > best[k]["discount"]):
            best[k] = r
    top = sorted(best.values(), key=lambda r: -r["discount"])[:5]
    saved = sum(r["usual"] - r["price"] for r in best.values()
                if r["usual"] and r["usual"] > r["price"])
    watch_n = sum(1 for r in rows if r["kind"] == "watch")
    n = len(rows)
    found = f"{n} {render.plural(n, 'находку', 'находки', 'находок')}"
    lines = [f"За месяц прислал тебе <b>{found}</b>"]
    if top:
        t = top[0]
        lines.append(f"🔥 самая большая скидка — <b>−{t['discount']}%</b>: "
                     f"{places.name(t['origin'])} → {places.name(t['dest'])} "
                     f"за {render.money(t['price'])}")
    if watch_n:
        lines.append(f"🔔 по твоим направлениям: {watch_n}")
    if saved:
        lines.append(f"💰 всё вместе — на {render.money(saved)} дешевле обычных цен")
    parts = [f"📊 <b>{title}</b>", "\n".join(lines)]
    if top:
        parts.append("<b>Самые выгодные:</b>\n" + "\n".join(
            f"{i}. −{r['discount']}% {places.name(r['origin'])} → {places.name(r['dest'])}"
            f" — {render.money(r['price'])}" for i, r in enumerate(top, 1)))
    parts.append("<i>Хочешь больше или меньше сообщений — ⚙️ Настройки → 🔔 Что присылать.</i>")
    subtitle = f"{found}" + (f" · самая большая скидка −{top[0]['discount']}%" if top else "")
    png = card.digest(title, subtitle,
                      [(r["discount"], f"{places.name(r['origin'])} → {places.name(r['dest'])}",
                        render.money(r["price"])) for r in top],
                      footnote="цены на момент находки")
    return png, "\n\n".join(parts)


async def recap_due(force=False, only=None):
    """
    Итоги месяца — первого числа после 12:00, за прошлый месяц, тем, кому
    за месяц пришло хотя бы три находки. force + only — посмотреть свои
    итоги прямо сейчас (из админки): тогда за текущий месяц на сегодня.
    """
    now = datetime.now()
    if force:
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since, until, month = first, now + timedelta(minutes=1), now.month
    else:
        if now.day != 1 or now.hour < 12:
            return 0
        until = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = (until - timedelta(days=1)).replace(day=1)
        month = since.month
        if db.meta_get("recap_month") == since.strftime("%Y-%m"):
            return 0
        db.meta_set("recap_month", since.strftime("%Y-%m"))
    a, b = since.isoformat(timespec="seconds"), until.isoformat(timespec="seconds")
    chats = [only] if only else db.sent_chats(a, b)
    bot, sent = make_bot(), 0
    title = f"Итоги {MONTHS_GEN[month - 1]}"
    for chat in chats:
        s = db.get_sub(chat)
        if not s or not s["active"]:
            continue
        got = recap_post(chat, a, b, title)
        if not got:
            if only:
                await bot.send_message(chat, "📊 За этот месяц пока меньше трёх находок — "
                                             "итогам не из чего собраться.")
            continue
        png, text = got
        markup = kb._kb([[kb._b("🔔 Что присылать", "set:mode")]])
        try:
            await channel.send_post(bot, chat, png, text, markup,
                                    rich_ok=(s["style"] or C.STYLE) == ui.NEW)
            sent += 1
        except TelegramForbiddenError:
            db.stop_sub(chat)
        except Exception as e:
            print(f"  итоги месяца {chat}: {e}")
        await asyncio.sleep(0.05)
    if sent:
        print(f"  итоги месяца: {sent}")
    return sent
