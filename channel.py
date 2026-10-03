"""
Канал: большие скидки и падения цен из Москвы и Питера.

Путь находки:
  1. сборщик размечает цены как обычно (detect.evaluate);
  2. candidate() отбирает то, что годится в канал: скидка от 40% к обычной
     цене маршрута или падение той же даты за сутки от 25%;
  3. владельцу приходит черновик — ровно тот пост, что уйдёт в канал,
     с кнопками «Опубликовать / Пропустить»;
  4. по «Опубликовать» цена перепроверяется на ту же дату. Подорожала больше
     чем на CHANNEL_RECHECK_TOL% или билет пропал — не публикуем и говорим.

Повторы: то же направление снова — только если дешевле прошлого черновика
на CHANNEL_REPOST_PCT% или прошло CHANNEL_REPOST_DAYS дней. Пропущенный
черновик считается так же: раз не понравилось, не надо предлагать его завтра.
"""
import asyncio
import json
import re
from datetime import date, datetime, timedelta

import aiohttp
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, InputRichBlockDivider, InputRichBlockPhoto,
                           InputRichMessage)

import card
import config as C
import db
import hotels
import places
import render
import tp
import ui
import weather

ORIGIN_TAG = {"MOW": "изМосквы", "LED": "изПитера"}
ICON = {"super": "🔥", "deal": "💸", "drop": "📉"}


# ---------- отбор ----------

def candidate(d, deal_pct=None, drop_pct=None):
    """
    Годится ли находка в канал. Возвращает {kind, pct, usual, basis} или None.

    Обычную цену берём по надёжности, а не по щедрости: если история
    маршрута говорит «−30%», а соседние даты — «−45%», верим истории
    и в канал не пишем. Лучше пропустить пост, чем соврать в нём.
    """
    deal_pct = C.CHANNEL_DEAL_PCT if deal_pct is None else deal_pct
    drop_pct = C.CHANNEL_DROP_PCT if drop_pct is None else drop_pct
    if d.get("origin") not in C.CHANNEL_ORIGINS or d.get("legs"):
        return None
    if not d.get("depart") or d.get("dest") in (None, "?"):
        return None
    # рейс сегодня вечером — не новость для канала: пока пост прочтут,
    # самолёт улетит. Такие находки остаются в боте, где их видят сразу.
    soonest = (date.today() + timedelta(days=C.CHANNEL_MIN_DAYS)).isoformat()
    if d["depart"] < soonest:
        return None
    price = d["price"]

    ref = next(((f, d[f]) for f in ("hist_usual", "near_median") if d.get(f)), None)
    if ref and ref[1] > price:
        pct = round((1 - price / ref[1]) * 100)
        if pct >= deal_pct:
            return {"kind": "super" if pct >= C.SUPER_PCT else "deal", "pct": pct,
                    "usual": int(ref[1]), "basis": ref[0]}

    was = d.get("was")
    if was and was > price:
        pct = round((1 - price / was) * 100)
        if pct >= drop_pct:
            return {"kind": "drop", "pct": pct, "usual": int(was), "basis": "was"}
    return None


def key(d):
    return f"{d['origin']}-{d['dest']}"


def repost_ok(k, price):
    """Правило повторов: дешевле прошлого на 15% или прошла неделя."""
    last = db.last_post(k)
    if not last:
        return True
    if price <= last["price"] * (1 - C.CHANNEL_REPOST_PCT / 100):
        return True
    age = datetime.now() - datetime.fromisoformat(last["created_at"])
    return age.days >= C.CHANNEL_REPOST_DAYS


# ---------- вёрстка ----------

def _tag(text):
    """«Шри-Ланка» -> «ШриЛанка»: в хэштеге только буквы и цифры."""
    return re.sub(r"\W+", "", text or "")


def hashtags(d):
    tags = [ORIGIN_TAG.get(d["origin"], "из" + _tag(places.name(d["origin"])))]
    country = places.country(d["dest"])
    # по России тег страны ничего не скажет — ставим город
    tags.append(_tag(places.name(d["dest"]) if country in (None, "Россия") else country))
    if d.get("weekend"):
        tags.append("наВыходные")
    if d["price"] <= C.CHANNEL_CHEAP_TAG:
        tags.append(f"до{C.CHANNEL_CHEAP_TAG}")
    return " ".join("#" + t for t in tags if t)


# ---------- обратный билет ----------

async def enrich(d):
    """
    Дописать в находку обратный билет: самый дешёвый через CHANNEL_BACK_MIN–
    CHANNEL_BACK_MAX дней, и отдельно — на выходные, если вылет в пт или сб.

    Один запрос: календарь обратного направления на год. Не ответил —
    пост выйдет без строки «обратно», это не повод его задерживать.
    """
    dep = date.fromisoformat(d["depart"])
    last = dep + timedelta(days=C.CHANNEL_BACK_MAX)
    async with aiohttp.ClientSession() as s:
        rows = await tp.Travelpayouts(s, d["dest"]).route_dates(d["origin"],
                                                                 until=last.isoformat())
    by_day = {}
    for r in rows:
        day = date.fromisoformat(r["depart"])
        if dep < day <= last and (day not in by_day or r["price"] < by_day[day]["price"]):
            by_day[day] = r

    def pack(r):
        return {"price": r["price"], "depart": r["depart"], "link": r.get("link")}

    near = [r for day, r in by_day.items()
            if (day - dep).days >= C.CHANNEL_BACK_MIN]
    d["back"] = pack(min(near, key=lambda r: r["price"])) if near else None

    # выходные: пт → вс/пн или сб → вс/пн, не дольше трёх дней
    d["weekend"] = None
    if dep.weekday() in (4, 5):
        wk = [r for day, r in by_day.items()
              if day.weekday() in (6, 0) and (day - dep).days <= 3]
        if wk:
            d["weekend"] = pack(min(wk, key=lambda r: r["price"]))

    # отели в городе прилёта на даты поездки — кнопкой под постом
    try:
        d["stay"] = await hotels.find(d)
    except Exception as e:
        print(f"  отели {d['dest']}: {e}")
        d["stay"] = None
    # погода там же и на те же даты — строкой в посте и на карточке
    back = d.get("ret") or (d.get("back") or {}).get("depart")
    d["weather"] = await weather.for_trip(d["dest"], d["depart"], back)
    return d


def back_text(d, short=False):
    """«обратно от 6 200 ₽ — 5 окт, вс · туда-обратно 13 800 ₽»."""
    b = d.get("back")
    if not b:
        return None
    text = f"обратно от {render.money(b['price'])} — {render.when_wd(b['depart'])}"
    if short:
        return text
    return text + f" · туда-обратно {render.money(d['price'] + b['price'])}"


def info(d):
    """«26 сен, сб · 21:50 · прямой · в одну сторону»."""
    bits = [render.when_wd(d["depart"]), render.clock(d.get("depart_at") or ""),
            render.stops(d),
            "в одну сторону" if not d.get("ret") else "обратно " + render.when_wd(d["ret"])]
    return " · ".join(b for b in bits if b)


def route(d):
    return f"{places.name(d['origin'])} → {places.name(d['dest'])}"


def usual_line(c):
    """«обычно от 9 800 ₽, скидка 45%» — с чем сравнили и насколько дешевле."""
    money = render.money
    if c["kind"] == "drop":
        return f"ещё вчера {money(c['usual'])}, подешевело на {c['pct']}%"
    if c["basis"] == "near_median":
        return f"на соседние даты от {money(c['usual'])}, скидка {c['pct']}%"
    return f"обычно от {money(c['usual'])}, скидка {c['pct']}%"


def return_lines(d):
    """Строки про обратный билет, выходные и погоду — если enrich их нашёл."""
    out = []
    if back_text(d):
        out.append(f"↩️ {back_text(d)}")
    w = d.get("weekend")
    if w and (not d.get("back") or w["depart"] != d["back"]["depart"]):
        out.append(f"🏖 на выходные: обратно {render.when_wd(w['depart'])} "
                   f"за {render.money(w['price'])}")
    if d.get("weather"):
        out.append(weather.line(d["weather"], places.name(d["dest"])))
    return out


def country_label(d):
    """Строка под маршрутом на карточке: «Турция · днём +24°»."""
    bits = [places.country(d["dest"]) or "", weather.short(d.get("weather"))]
    return " · ".join(b for b in bits if b)


def caption(d, c, updated=None):
    """Текст поста: три строки фактов и хэштеги. updated — когда цену обновили."""
    extra = "".join("\n" + x for x in return_lines(d))
    tail = f"\n🔄 <i>цена обновлена {_stamp(updated)}</i>" if updated else ""
    return (f"{ICON[c['kind']]} <b>{route(d)} — {render.money(d['price'])}</b>\n"
            f"{usual_line(c)}\n{info(d)}{extra}{tail}\n\n{hashtags(d)}{ad_label()}")


def ad_label():
    """
    Пометка «Реклама…» в конце поста: в посте партнёрские ссылки, по закону
    о маркировке это реклама. Текст — ровно тот, что даёт Travelpayouts, erid
    берём из параметров партнёрской ссылки: он обновляется раз в сутки.
    """
    label = render.ad_label(C.TP_TRS_CHANNEL)
    return f"\n\n{label}" if label else ""


def _stamp(iso):
    """'2026-09-29T15:40:12' -> '29 сен, 15:40'."""
    t = datetime.fromisoformat(iso)
    return f"{t.day} {render.MONTHS[t.month - 1]}, {t:%H:%M}"


def image(d, c):
    return card.render(c["kind"], c["pct"], route(d), country_label(d),
                       render.money(d["price"]), render.money(c["usual"]), info(d),
                       transfers=d.get("transfers"),
                       via=[places.name(v) for v in (tp.via(d) or [])],
                       back=back_text(d, short=True))


def channel_kb(d, post_id=None):
    """Кнопки поста. post_id — в метку ссылки: видно, какой пост продаёт."""
    sub = f"ch_{post_id}" if post_id else "ch"
    rows = []
    if d.get("link"):
        rows.append([InlineKeyboardButton(text=t, url=u) for t, u in tp.buy_buttons(d, sub)])
    b = d.get("back")
    if b:
        back = {"origin": d["dest"], "dest": d["origin"], "depart": b["depart"],
                "link": b.get("link")}
        rows.append([InlineKeyboardButton(
            text=f"↩️ Обратно {render.when(b['depart'])} за {render.money(b['price'])}",
            url=tp.buy_link(back, sub + "_back"))])
    if d.get("stay"):
        rows.append([InlineKeyboardButton(text=hotels.label(d["stay"]),
                                          url=hotels.link(d["stay"], sub + "_hotel"))])
    rows.append([InlineKeyboardButton(
        text="🔔 Свои уведомления о скидках",
        url=f"https://t.me/{C.BOT_USERNAME}?start=channel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_kb(post_id, d):
    rows = [[InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"ch:pub:{post_id}"),
             InlineKeyboardButton(text="❌ Пропустить", callback_data=f"ch:skip:{post_id}")]]
    if d.get("link"):
        rows.append([InlineKeyboardButton(text="🔎 Проверить на сайте",
                                          url=tp.buy_link(d, "ch_draft"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def done_kb(text, url=None):
    """Кнопка-итог вместо «Опубликовать»: чтобы черновик не нажали второй раз."""
    b = (InlineKeyboardButton(text=text, url=url) if url
         else InlineKeyboardButton(text=text, callback_data="ch:noop"))
    return InlineKeyboardMarkup(inline_keyboard=[[b]])


def post_url(message_id):
    """Ссылка на пост закрытого канала: -1001234567890 -> t.me/c/1234567890/N."""
    cid = str(db.channel_id())
    return f"https://t.me/c/{cid[4:] if cid.startswith('-100') else cid.lstrip('-')}/{message_id}"


# ---------- отправка и правка: новый вид или картинка с подписью ----------

def rich(png, text, kb=None):
    """
    Пост в новом виде: карточка, заголовок, текст и кнопки внутри поста.
    Заголовком становится первая строка подписи, кнопки — те же, что под постом.
    """
    blocks = [InputRichBlockPhoto(photo=InputMediaPhoto(media=BufferedInputFile(png, "card.png")))]
    blocks += ui.blocks_from_text(text)
    if kb:
        blocks += [InputRichBlockDivider()] + ui.button_blocks(ui.rows_from_markup(kb))
    return InputRichMessage(blocks=blocks)


async def send_post(bot, chat_id, png, text, kb=None, silent=False, rich_ok=None):
    """
    Отправить пост. Возвращает (сообщение, формат: rich / photo).
    rich_ok — новый вид или нет; не задан — как в канале (CHANNEL_RICH).
    Новый вид не принят — уходит картинкой с подписью: пусть проще, но выйдет.
    """
    if C.CHANNEL_RICH if rich_ok is None else rich_ok:
        try:
            msg = await bot.send_rich_message(chat_id, rich_message=rich(png, text, kb),
                                              disable_notification=silent)
            return msg, "rich"
        except TelegramRetryAfter:
            raise
        except Exception as e:
            print(f"  пост новым видом не ушёл ({e}), шлю картинкой")
    msg = await bot.send_photo(chat_id, BufferedInputFile(png, "card.png"), caption=text,
                               reply_markup=kb, disable_notification=silent)
    return msg, "photo"


async def edit_post(bot, chat_id, message_id, fmt, png, text, kb=None):
    """Поправить пост в том виде, в каком он вышел: тип сообщения Telegram не меняет."""
    if fmt == "rich":
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                    rich_message=rich(png, text, kb))
    else:
        await bot.edit_message_media(
            chat_id=chat_id, message_id=message_id,
            media=InputMediaPhoto(media=BufferedInputFile(png, "card.png"), caption=text,
                                  parse_mode="HTML"),
            reply_markup=kb)


# ---------- черновики ----------

async def send_draft(bot, post_id, d, c, note=None):
    await enrich(d)
    photo = BufferedInputFile(image(d, c), filename="card.png")
    for owner in C.OWNER_IDS:
        if note:
            await bot.send_message(owner, note)
        await bot.send_photo(owner, photo, caption=caption(d, c),
                             reply_markup=draft_kb(post_id, d))


async def propose(found):
    """
    Из размеченного прохода — в очередь на публикацию (или черновики
    владельцу, если автопубликация выключена). Возвращает сколько.
    """
    if not db.channel_id() or not (C.CHANNEL_AUTO or C.OWNER_IDS):
        return 0
    from notify import make_bot
    bot = make_bot()
    picked = [(d, c) for d in found for c in [candidate(d)] if c]
    picked.sort(key=lambda x: -x[1]["pct"])
    n = 0
    for d, c in picked:
        k = key(d)
        if not repost_ok(k, d["price"]):
            continue
        if C.CHANNEL_AUTO:
            db.add_post(k, c["kind"], c["pct"], d["price"], {"deal": d, "cand": c},
                        status="queued")
            n += 1
            continue
        post_id = db.add_post(k, c["kind"], c["pct"], d["price"], {"deal": d, "cand": c})
        try:
            await send_draft(bot, post_id, d, c)
            n += 1
        except Exception as e:
            print(f"  черновик {k}: {e}")
    if picked:
        print(f"  канал: подходит {len(picked)}, "
              f"{'в очередь' if C.CHANNEL_AUTO else 'черновиков'} {n}")
    return n


# ---------- автопубликация ----------

def quiet_now():
    """Ночь по времени сервера: посты выходят, но без звука у подписчиков."""
    start, end = C.CHANNEL_QUIET
    h = datetime.now().hour
    return h >= start or h < end if start > end else start <= h < end


def published_today():
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.published_since(midnight.isoformat(timespec="seconds"))


def gap_now(done):
    """
    Минут до следующего поста: остаток дневного лимита размазываем до
    начала ночи, чтобы вечером было что читать, а не всё ушло к обеду.
    Не реже раза в CHANNEL_GAP_MAX минут — хорошая скидка долго не ждёт.
    """
    if quiet_now():
        return C.CHANNEL_GAP_MIN
    now = datetime.now()
    night = now.replace(hour=C.CHANNEL_QUIET[0], minute=0, second=0, microsecond=0)
    left = max(1, C.CHANNEL_DAY_MAX - done)
    spread = (night - now).total_seconds() / 60 / left
    return max(C.CHANNEL_GAP_MIN, min(spread, C.CHANNEL_GAP_MAX))


async def publish_next():
    """
    Опубликовать лучший пост из очереди, если подошло время (gap_now).

    Не больше CHANNEL_DAY_MAX постов за сутки: сорок постов в день — это
    уведомление каждые двадцать минут, от такого отписываются. Ночью —
    только суперскидки, остальное ждёт утра в очереди или устаревает.

    Не вышло (цена ушла, билет пропал) — пост снимается, следующий пробуем
    на следующем тике. Источник не ответил — пост остаётся в очереди.
    """
    if not C.CHANNEL_AUTO or not db.channel_id():
        return False
    db.expire_queue(C.CHANNEL_QUEUE_HOURS)
    done = published_today()
    if done >= C.CHANNEL_DAY_MAX:
        return False
    last = db.last_published_at()
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() \
            < gap_now(done) * 60:
        return False
    p = db.next_queued(min_pct=C.CHANNEL_NIGHT_PCT if quiet_now() else None)
    if not p:
        return False
    from notify import make_bot
    bot = make_bot()
    ok, note, url = await publish(bot, p["id"])
    data = json.loads(db.get_post(p["id"])["payload"])
    d = data["deal"]
    if not ok:
        print(f"  очередь: {route(d)} снят — {note}")
        return False
    fresh = json.loads(db.get_post(p["id"])["payload"])
    text = (f"📣 Опубликовал: {route(d)} — {render.money(fresh['deal']['price'])} "
            f"(−{fresh['cand']['pct']}%)" + (f"\n{note}" if note else ""))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть", url=url),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ch:del:{p['id']}")]])
    for owner in C.OWNER_IDS:
        try:
            await bot.send_message(owner, text, reply_markup=kb,
                                   disable_notification=True)
        except Exception:
            pass
    return True


async def publisher():
    """Вечный цикл автопубликации: раз в полминуты смотрит в очередь."""
    import traceback
    import notify
    while True:
        for rubric in (digest_due, weekend_due, notify.weekend_due, notify.recap_due,
                       fresh_track_due):
            try:
                await rubric()
            except Exception:
                traceback.print_exc()
        try:
            if await publish_next():
                await notify.resolved("publish", "Публикация в канал снова работает.")
        except Exception as e:
            traceback.print_exc()
            await notify.problem("publish", f"Не публикуется в канал: {type(e).__name__}: "
                                            f"{str(e)[:200]}. Проверь права бота в канале.")
        await asyncio.sleep(30)


# ---------- рубрики ----------

def weekend_dates():
    """Ближайшие выходные: туда в пт или сб, обратно в вс или пн."""
    today = date.today()
    fri = today + timedelta(days=(4 - today.weekday()) % 7)
    return fri, fri + timedelta(days=1)


async def weekend_picks(origin, n=5):
    """
    Самые дешёвые поездки на ближайшие выходные из города: туда по свежим
    ценам обхода, обратно — запросом на направление (enrich). Сумма двух
    билетов, по возрастанию. Обратного на вс/пн нет — направление не берём.
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    outs = [d.isoformat() for d in weekend_dates() if d.isoformat() >= tomorrow]
    rows = db.cheapest_on(origin, outs, since=(datetime.now() - timedelta(hours=24))
                          .isoformat(timespec="seconds"), limit=C.CHANNEL_WEEKEND_CHECK)
    out = []
    for r in rows:
        d = {"origin": origin, "dest": r["dest"], "depart": r["depart"], "price": r["price"]}
        try:
            await enrich(d)
        except Exception as e:
            print(f"  выходные {origin}-{r['dest']}: {e}")
            continue
        if d.get("weekend"):
            d["total"] = d["price"] + d["weekend"]["price"]
            out.append(d)
    out.sort(key=lambda d: d["total"])
    return out[:n]


async def weekend_due(force=False):
    """
    «Куда на выходные» — раз в неделю, в CHANNEL_WEEKEND_DAY после
    CHANNEL_WEEKEND_HOUR: самые дешёвые поездки туда-обратно на ближайшие
    выходные из Москвы и Питера. Меньше трёх вариантов — не публикуем.
    """
    cid = db.channel_id()
    if not cid:
        return False
    now = datetime.now()
    week = now.strftime("%G-%V")
    if not force:
        if now.weekday() != C.CHANNEL_WEEKEND_DAY or now.hour < C.CHANNEL_WEEKEND_HOUR:
            return False
        if db.meta_get("weekend_week") == week:
            return False
    db.meta_set("weekend_week", week)
    picks = {o: await weekend_picks(o) for o in C.CHANNEL_ORIGINS}
    if sum(len(v) for v in picks.values()) < 3:
        print("  выходные: вариантов меньше трёх, подборку пропускаю")
        return False

    from notify import make_bot
    png, text = weekend_post(picks)
    await send_post(make_bot(), cid, png, text, silent=quiet_now())
    return True


def weekend_post(picks, sub="ch_weekend", tags=True):
    """
    Картинка и текст подборки «Куда на выходные». picks — {город вылета:
    [поездки из weekend_picks]}. Общая для канала и для бота (sub, tags).
    """
    # даты в заголовке — от самого раннего вылета до самого позднего возвращения
    every = [d for v in picks.values() for d in v]
    a = date.fromisoformat(min(d["depart"] for d in every))
    b = date.fromisoformat(max(d["weekend"]["depart"] for d in every))
    span = f"{a.day}–{b.day} {render.MONTHS[b.month - 1]}" if a.month == b.month \
        else f"{a.day} {render.MONTHS[a.month - 1]} – {b.day} {render.MONTHS[b.month - 1]}"
    names = {"MOW": "Из Москвы", "LED": "Из Питера"}
    short = {4: "пт", 5: "сб", 6: "вс", 0: "пн"}

    def days(d):
        a, b = date.fromisoformat(d["depart"]), date.fromisoformat(d["weekend"]["depart"])
        return f"{short[a.weekday()]}–{short[b.weekday()]}"

    rows, parts = [], [f"🏖 <b>Куда на выходные</b>\n{span} · туда и обратно"]
    for origin, items in picks.items():
        if not items:
            continue
        rows += [(days(d), route(d), render.money(d["total"])) for d in items[:3]]
        lines = [f"<b>{names.get(origin, 'Из ' + places.name(origin))}</b>"]
        for i, d in enumerate(items, 1):
            url = tp.buy_link(dict(d, ret=d["weekend"]["depart"]), sub)
            lines.append(f'{i}. <a href="{url}">{places.name(d["dest"])}</a> — '
                         f'{render.money(d["total"])} · {days(d)}, '
                         f'{render.when(d["depart"])} → {render.when(d["weekend"]["depart"])}')
        parts.append("\n".join(lines))
    parts.append("<i>Цена — за оба билета на момент публикации. "
                 "Нажми город — откроется поиск туда-обратно на эти даты.</i>")
    if tags:
        parts.append("#наВыходные #подборка")
    png = card.digest("Куда на выходные", f"{span} · туда и обратно, за два билета", rows[:6])
    return png, "\n\n".join(parts)

async def digest_due(force=False):
    """
    «Лучшее за неделю» — раз в неделю, в CHANNEL_DIGEST_DAY после
    CHANNEL_DIGEST_HOUR. Берутся опубликованные за 7 дней, самые большие
    скидки. Меньше трёх — неделя была тихой, дайджест не нужен.
    """
    cid = db.channel_id()
    if not cid:
        return False
    now = datetime.now()
    week = now.strftime("%G-%V")
    if not force:
        if now.weekday() != C.CHANNEL_DIGEST_DAY or now.hour < C.CHANNEL_DIGEST_HOUR:
            return False
        if db.meta_get("digest_week") == week:
            return False
    rows = []
    for p in db.connect().execute(
            "SELECT * FROM posts WHERE status IN ('published','expired') "
            "AND published_at>=? ORDER BY pct DESC LIMIT 6",
            ((now - timedelta(days=7)).isoformat(timespec="seconds"),)):
        rows.append((p, json.loads(p["payload"])["deal"]))
    db.meta_set("digest_week", week)
    if len(rows) < 3:
        return False
    first = now - timedelta(days=7)
    subtitle = (f"{first.day} {render.MONTHS[first.month - 1]} — "
                f"{now.day} {render.MONTHS[now.month - 1]} · из Москвы и Питера")
    png = card.digest("Лучшее за неделю", subtitle,
                      [(p["pct"], route(d), render.money(p["price"])) for p, d in rows])
    lines = [f'{i}. <a href="{post_url(p["message_id"])}">{route(d)} — '
             f'{render.money(p["price"])}</a> (−{p["pct"]}%)'
             + (" · <i>уже ушло</i>" if p["status"] == "expired" else "")
             for i, (p, d) in enumerate(rows, 1)]
    text = ("🏆 <b>Лучшее за неделю</b>\n\n" + "\n".join(lines)
            + "\n\n<i>Цены на момент публикации — актуальные в самих постах.</i>"
            + "\n\n#лучшееЗаНеделю")
    from notify import make_bot
    await send_post(make_bot(), cid, png, text, silent=quiet_now())
    return True


NAV_TEXT = (
    "📌 <b>Как пользоваться каналом</b>\n\n"
    "✈️ <b>Что здесь</b>\n"
    "Авиабилеты из Москвы и Питера, которые сейчас заметно дешевле обычного:\n"
    "🔥 <b>Суперскидка</b> — от {super}% к обычной цене маршрута\n"
    "💸 <b>Скидка</b> — от {deal}%\n"
    "📉 <b>Подешевело</b> — цена упала на {drop}% и больше за сутки\n\n"
    "🎫 <b>Как купить</b>\n"
    "Кнопка «Купить» в посте откроет Aviasales на нужную дату. "
    "«Обратно» — самый дешёвый обратный билет на ближайшие дни.\n\n"
    "🔄 <b>Цены живые</b>\n"
    "Первые {track} дней бот сверяет цену в каждом посте: изменилась — пост обновится сам, "
    "билеты закончились — появится пометка «⛔ Уже не актуально».\n\n"
    "🔎 <b>Поиск по тегам</b>\n"
    "#изМосквы · #изПитера — откуда вылет\n"
    "#Турция, #Сочи — куда: страна, а по России город\n"
    "#наВыходные — туда в пт/сб, обратно в вс/пн; по четвергам — подборка\n"
    "#до{cheap} — билеты до {cheap_money}\n"
    "#лучшееЗаНеделю — подборка по воскресеньям\n"
    "<i>Здесь теги нажимаются, в постах — долгим нажатием.</i>\n\n"
    "🔔 <b>Нужен свой город или конкретный рейс?</b>\n"
    "В @{bot} — скидки из твоего города и слежка за направлением, "
    "например Киров → Москва. Бесплатно.")


async def pin_navigation(bot):
    """
    Закреп с навигацией. Уже есть — правим текст, нет — публикуем и закрепляем.
    Возвращает (ссылка, пояснение). Закрепить боту нужно право редактировать
    сообщения канала; нет его — пост останется, закрепить руками.
    """
    cid = db.channel_id()
    text = NAV_TEXT.format(super=C.SUPER_PCT, deal=C.CHANNEL_DEAL_PCT,
                           drop=C.CHANNEL_DROP_PCT, track=C.CHANNEL_TRACK_DAYS,
                           cheap=C.CHANNEL_CHEAP_TAG,
                           cheap_money=render.money(C.CHANNEL_CHEAP_TAG), bot=C.BOT_USERNAME)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🔔 Свои уведомления о скидках",
        url=f"https://t.me/{C.BOT_USERNAME}?start=channel")]])
    old = db.meta_get("nav_message")
    if old:
        try:
            await bot.edit_message_text(text, chat_id=cid, message_id=int(old),
                                        reply_markup=kb)
            return post_url(old), "обновил текст закрепа"
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return post_url(old), "закреп уже актуален"
    msg = await bot.send_message(cid, text, reply_markup=kb, disable_notification=True)
    db.meta_set("nav_message", msg.message_id)
    try:
        await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
        return post_url(msg.message_id), "опубликовал и закрепил"
    except Exception as e:
        return post_url(msg.message_id), (f"опубликовал, но закрепить не вышло ({e}) — "
                                          "закрепи руками или дай боту право "
                                          "редактировать сообщения")


async def delete(bot, post_id):
    """Убрать пост из канала по кнопке владельца."""
    p = db.get_post(post_id)
    if not p or not p["message_id"]:
        return False
    await bot.delete_message(db.channel_id(), p["message_id"])
    db.set_post(post_id, status="deleted")
    return True


async def on_poll(found):
    """Всё, что канал делает после прохода сборщика."""
    await propose(found)
    await track()


async def example():
    """
    Черновик-пример из лучшего, что есть в ленте, без порогов.
    Чтобы увидеть пост вживую, не дожидаясь настоящей скидки.
    """
    from notify import make_bot
    best = None
    for origin in C.CHANNEL_ORIGINS:
        for d in db.load_feed(origin):
            c = candidate(d, deal_pct=1, drop_pct=1)
            if c and (not best or c["pct"] > best[1]["pct"]):
                best = (d, c)
    if not best:
        return False
    d, c = best
    post_id = db.add_post(key(d), c["kind"], c["pct"], d["price"], {"deal": d, "cand": c})
    await send_draft(make_bot(), post_id, d, c,
                     note=f"👀 Пример: лучшее из текущей ленты, скидка {c['pct']}% — "
                          f"порог канала {C.CHANNEL_DEAL_PCT}%. Опубликуешь — уйдёт как есть.")
    return True


# ---------- публикация ----------

async def recheck(d):
    """Самая дешёвая цена в одну сторону на ту же дату, свежим запросом."""
    async with aiohttp.ClientSession() as s:
        rows = await tp.Travelpayouts(s, d["origin"]).exact(d["dest"], d["depart"], limit=10)
    rows = [r for r in rows if not r.get("ret")]
    return min(rows, key=lambda r: r["price"]) if rows else None


def refresh(d, c, fresh):
    """
    Предложение и отбор, пересчитанные по свежей цене на ту же дату.

    Берём и подробности рейса: самая дешёвая на дату может оказаться уже
    другим рейсом, и время с пересадками на карточке должны быть его.
    Обычная цена не меняется — меняется только скидка к ней.
    """
    d = dict(d, **{k: fresh.get(k) for k in
                   ("price", "link", "depart_at", "transfers", "airline", "via")
                   if fresh.get(k) is not None})
    c = dict(c, pct=round((1 - d["price"] / c["usual"]) * 100))
    if c["kind"] != "drop":
        c["kind"] = "super" if c["pct"] >= C.SUPER_PCT else "deal"
    return d, c


async def publish(bot, post_id):
    """
    Опубликовать черновик. Возвращает (удалось, пояснение, ссылка на пост).

    Перепроверка на ту же дату: берём свежую цену и свежие подробности рейса.
    Самая дешёвая на дату может оказаться уже другим рейсом — тогда и время,
    и пересадки на карточке будут его, а не вчерашние.
    """
    p = db.get_post(post_id)
    if not p or p["status"] not in ("draft", "queued"):
        return False, "этот черновик уже обработан", None
    data = json.loads(p["payload"])
    d, c = data["deal"], data["cand"]

    fresh = await recheck(d)
    if not fresh:
        db.set_post(post_id, status="stale")
        return False, "билета на эту дату в выдаче больше нет", None
    if fresh["price"] > d["price"] * (1 + C.CHANNEL_RECHECK_TOL / 100):
        db.set_post(post_id, status="stale")
        return False, (f"подорожал: было {render.money(d['price'])}, "
                       f"теперь {render.money(fresh['price'])}"), None

    note = None
    if fresh["price"] != d["price"]:
        note = f"цена обновилась: {render.money(d['price'])} → {render.money(fresh['price'])}"
        d, c = refresh(d, c, fresh)
        need = C.CHANNEL_DROP_PCT if c["kind"] == "drop" else C.CHANNEL_DEAL_PCT
        if c["pct"] < need and not p["pct"] < need:        # пример ниже порога не судим
            db.set_post(post_id, status="stale")
            return False, f"{note}, скидка стала {c['pct']}% — ниже порога", None

    await enrich(d)
    msg, fmt = await send_post(bot, db.channel_id(), image(d, c), caption(d, c),
                               channel_kb(d, post_id), silent=quiet_now())
    # в базу — ровно то, что ушло в канал: по этому потом сверяется цена
    db.set_post(post_id, status="published", message_id=msg.message_id, price=d["price"],
                pct=c["pct"], published_at=db.now(), fmt=fmt,
                payload=json.dumps({"deal": d, "cand": c}, ensure_ascii=False))
    return True, note, post_url(msg.message_id)


# ---------- сопровождение опубликованного ----------

_track_lock = None


def _lock():
    global _track_lock
    if _track_lock is None:
        _track_lock = asyncio.Lock()
    return _track_lock


async def fresh_track_due():
    """
    Свежие посты сверяем часто: первые CHANNEL_FRESH_HOURS после публикации —
    раз в CHANNEL_FRESH_EVERY минут, а не раз за круг опроса. Самые большие
    скидки исчезают как раз в первые часы, и пост с ушедшей ценой не должен
    висеть почти час.
    """
    if not db.channel_id():
        return 0
    last = db.meta_get("fresh_track_at")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds()             < C.CHANNEL_FRESH_EVERY * 60:
        return 0
    db.meta_set("fresh_track_at", db.now())
    if not db.active_posts(C.CHANNEL_FRESH_HOURS / 24):
        return 0
    return await track(days=C.CHANNEL_FRESH_HOURS / 24)


async def track(days=None):
    """
    Сверить цены опубликованных постов и поправить их в канале.

    Подешевело или чуть подорожало — перерисовываем карточку с новой ценой.
    Скидка растаяла ниже CHANNEL_STALE_PCT или билет пропал — помечаем пост
    «не актуально» и убираем кнопку покупки. Рейс улетел — больше не следим.
    Возвращает, сколько постов поправлено.
    """
    cid = db.channel_id()
    if not cid:
        return 0
    async with _lock():                 # частая и обычная сверка не правят пост разом
        return await _track(cid, days or C.CHANNEL_TRACK_DAYS)


async def _track(cid, days):
    from notify import make_bot
    bot = make_bot()
    today = date.today().isoformat()
    changed = 0
    for p in db.active_posts(days):
        data = json.loads(p["payload"])
        d, c = data["deal"], data["cand"]
        if d["depart"] < today:
            continue
        if d["price"] != p["price"]:
            # посты, опубликованные до исправления, хранят цену черновика,
            # а в канале стоит перепроверенная — сверяем с той, что в канале
            d = dict(d, price=p["price"])
            c = dict(c, pct=round((1 - p["price"] / c["usual"]) * 100))
        try:
            fresh = await recheck(d)
            if not fresh:
                await _expire(bot, cid, p, d, c, "билетов по этой цене больше нет")
                changed += 1
                continue
            step = abs(fresh["price"] - d["price"])
            if step < max(1, d["price"] * C.CHANNEL_EDIT_MIN_PCT / 100):
                continue
            nd, nc = refresh(d, c, fresh)
            if nc["pct"] < C.CHANNEL_STALE_PCT:
                await _expire(bot, cid, p, d, c, f"сейчас {render.money(nd['price'])}")
                changed += 1
                continue
            when = db.now()
            await edit_post(bot, cid, p["message_id"], p["fmt"], image(nd, nc),
                            caption(nd, nc, updated=when), channel_kb(nd, p["id"]))
            db.set_post(p["id"], price=nd["price"], pct=nc["pct"],
                        payload=json.dumps({"deal": nd, "cand": nc}, ensure_ascii=False))
            arrow = "🔻" if nd["price"] < d["price"] else "🔺"
            await _tell(bot, f"{arrow} Обновил пост: {route(d)}, "
                             f"{render.money(d['price'])} → {render.money(nd['price'])}",
                        p["message_id"])
            changed += 1
        except TelegramRetryAfter as e:
            # Telegram ограничивает частоту правок в канале (~20 в минуту):
            # ждём, сколько просит, а этот пост поправим на следующем проходе
            print(f"  сопровождение: Telegram просит подождать {e.retry_after} с")
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                continue
            if "not found" in str(e):                # пост удалили руками
                db.set_post(p["id"], status="deleted")
                continue
            print(f"  сопровождение поста {p['id']}: {e}")
        except Exception as e:
            print(f"  сопровождение поста {p['id']}: {e}")
    if changed:
        print(f"  канал: поправлено постов {changed}")
    return changed


async def _expire(bot, cid, p, d, c, reason):
    """Пометить пост: картинка с плашкой, строка сверху, без кнопки покупки."""
    text = f"⛔ <b>Уже не актуально</b> — {reason}\n\n" + caption(d, c)
    bot_only = InlineKeyboardMarkup(inline_keyboard=channel_kb(d).inline_keyboard[-1:])
    d = dict(d, back=None, weekend=None)       # «обратно» у ушедшей цены только путает
    await edit_post(bot, cid, p["message_id"], p["fmt"], card.stamp(image(d, c)), text,
                    bot_only)
    db.set_post(p["id"], status="expired")
    await _tell(bot, f"⛔ Пост «{route(d)} — {render.money(d['price'])}» помечен "
                     f"как неактуальный: {reason}.", p["message_id"])


async def _tell(bot, text, message_id):
    for owner in C.OWNER_IDS:
        try:
            await bot.send_message(owner, text, reply_markup=done_kb(
                "Открыть пост", post_url(message_id)))
        except Exception:
            pass
