"""
Свои направления: человек следит за конкретным маршрутом, «Киров → Москва».

Общая рассылка отвечает на «куда улететь подешевле», а часто нужен один
рейс: к родителям, на работу, домой. Для него порог ниже и логика проще:

  • каждый опрос — один запрос на направление: самая дешёвая цена на каждую
    дату на год вперёд (с учётом окна дат из настроек человека);
  • пишем, если лучшая цена стала на WATCH_DROP_PCT% дешевле той, что
    человек уже знает (из прошлого уведомления или с момента подписки),
    или появилась скидка от WATCH_PCT% к обычной цене маршрута;
  • цена ушла вверх — «известная» цена тихо поднимается следом, иначе
    после подорожания падение обратно никто бы не заметил.

Цены из проверки пишутся в историю маршрута: у «Киров → Москва» нет
общего обхода, и обычную цену бот узнаёт как раз отсюда.
"""
import traceback
from datetime import date, datetime, timedelta

import aiohttp
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import config as C
import db
import detect
import places
import render
import tp
import users


# ---------- цены направления ----------

async def fetch(session, origin, dest):
    """Самая дешёвая цена в одну сторону на каждую дату, на год вперёд."""
    until = (date.today() + timedelta(days=365)).isoformat()
    rows = await tp.Travelpayouts(session, origin).route_dates(dest, until=until)
    db.save_daily(rows)
    return rows


def best(rows, s=None, on_date=""):
    """
    Лучший билет для этого человека: самая дешёвая дата в его окне,
    не раньше завтрашнего дня. Со скидкой к обычной цене, если она есть.
    on_date — следит за одним рейсом: берём только эту дату, окно не важно.
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    a, b = (s["date_from"], s["date_to"]) if s else (None, None)
    if on_date:
        mine = [r for r in rows if r.get("depart") == on_date >= tomorrow]
    else:
        mine = [r for r in rows if (r.get("depart") or "") >= tomorrow
                and users.in_window(r["depart"], a, b)]
    if not mine:
        return None
    d = dict(min(mine, key=lambda r: r["price"]))
    rate(d, rows)
    return d


def rate(d, rows):
    """
    Скидка к обычной цене маршрута — как в общей ленте, но без порога:
    насколько считать «дешевле обычного», решает check().

    Сначала история маршрута (обычная цена других дат того же месяца),
    нет истории — соседние даты из этого же ответа.
    """
    direct = d.get("transfers") == 0
    usual, n, seasonal = detect.stats(d["origin"], d["dest"], d["depart"][5:7],
                                      exclude=d["depart"], direct=direct)
    why = []
    if usual and usual > d["price"]:
        d.update(hist_usual=usual, history_n=n, seasonal=seasonal, hist_direct=direct)
        why.append("аномалия")
    near = detect.neighbour_medians(rows).get(
        (d["origin"], d["dest"], direct, date.fromisoformat(d["depart"])))
    if near and near[0] > d["price"]:
        d.update(near_median=near[0], near_n=near[1])
        why.append("выброс")
    d["why"] = why
    detect.discount(d)
    return d


# ---------- решение «писать или нет» ----------

def decide(w, d):
    """
    Что делать с найденной ценой: ("send", d) / ("raise", цена) / (None, None).

    known — цена, которую человек уже знает. Подешевело от неё на
    WATCH_DROP_PCT% — пишем. Скидка от WATCH_PCT% к обычной и цена хоть
    немного ниже известной — тоже пишем, но не чаще раза в WATCH_REPEAT_DAYS.
    """
    price, known = d["price"], w["last_price"]
    if w["target"]:
        return _decide_target(w, d)
    if not known:
        # цен при подписке не было: первая же увиденная становится известной,
        # а написать стоит только если она уже со скидкой
        return ("send", d) if (d.get("discount") or 0) >= C.WATCH_PCT else ("raise", price)
    if price <= known * (1 - C.WATCH_DROP_PCT / 100):
        d["watch_was"] = known
        return "send", d
    if (d.get("discount") or 0) >= C.WATCH_PCT and price < known:
        last = w["last_sent_at"]
        if not last or datetime.now() - datetime.fromisoformat(last) \
                >= timedelta(days=C.WATCH_REPEAT_DAYS):
            d["watch_was"] = known
            return "send", d
    if price >= known * (1 + C.WATCH_DROP_PCT / 100):
        return "raise", price
    return None, None


def _decide_target(w, d):
    """
    Своя цена: пишем, когда билет стал не дороже неё. Один раз — пока цена
    не уйдёт выше и не вернётся; дальше — если подешевело ещё на
    WATCH_DROP_PCT% от того, о чём писали.
    """
    price, known, t = d["price"], w["last_price"], w["target"]
    if price <= t:
        if not known or known > t or price <= known * (1 - C.WATCH_DROP_PCT / 100):
            d["watch_target"] = t
            if known and known > price:
                d["watch_was"] = known
            return "send", d
        return None, None
    if not known or price >= known * (1 + C.WATCH_DROP_PCT / 100):
        return "raise", price
    return None, None


async def check():
    """
    Проверить все направления, за которыми следят, и написать, где надо.
    Направление с десятью подписчиками — всё равно один запрос.
    Возвращает сколько сообщений ушло.
    """
    import notify
    import post
    watches = db.all_watches()
    if not watches or notify.quiet_now():
        # ночью не проверяем: утром первая проверка возьмёт свежие цены
        # и напишет, если есть что, — уже не разбудив
        return 0
    rows = {}
    async with aiohttp.ClientSession() as s:
        for route in sorted({(w["origin"], w["dest"]) for w in watches}):
            try:
                rows[route] = await fetch(s, *route)
            except Exception as e:
                print(f"  слежка {route[0]}-{route[1]}: {e}")
    bot = notify.make_bot()
    sent = 0
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for w in watches:
        if w["on_date"] and w["on_date"] < tomorrow:
            db.del_watch(w["chat_id"], w["id"])     # рейс улетел — следить не за чем
            continue
        got = rows.get((w["origin"], w["dest"]))
        if got is None:                       # источник не ответил — не судим
            continue
        sub = db.get_sub(w["chat_id"])
        d = best(got, sub, w["on_date"])
        db.set_watch(w["id"], cur_price=d["price"] if d else None,
                     cur_depart=d["depart"] if d else None, checked_at=db.now())
        if not d:
            continue
        what, val = decide(w, d)
        if what == "raise":
            db.set_watch(w["id"], last_price=val)
            continue
        if what != "send":
            continue
        if "watch" not in users.alerts_of(sub):
            # уведомления по направлениям выключены: цену видно в списке,
            # «известную» не двигаем — включит снова, узнает о падении
            continue
        d["watch_id"], d["watch_on"] = w["id"], w["on_date"]
        try:
            await post.send(bot, w["chat_id"], d, "bot_watch", sub["style"])
            db.set_watch(w["id"], last_price=d["price"], last_sent_at=db.now())
            db.log_sent(w["chat_id"], d, "watch")
            sent += 1
        except TelegramForbiddenError:
            db.stop_sub(w["chat_id"])
        except TelegramRetryAfter as e:
            print(f"  слежка: Telegram просит подождать {e.retry_after} с")
        except Exception:
            traceback.print_exc()
    print(f"  слежка: направлений {len(rows)}, подписок {len(watches)}, писем {sent}")
    return sent


# ---------- для экранов ----------

async def start(chat_id, origin, dest, on_date=""):
    """
    Начать следить — за направлением или за одной датой (on_date).
    Возвращает (id, лучшая цена сейчас или None, ошибка).
    Лучшая цена сейчас и становится «известной»: писать будем, когда дешевле.
    """
    if db.get_watch(chat_id, origin, dest, on_date):
        return db.get_watch(chat_id, origin, dest, on_date)["id"], None, None
    if len(db.watches_of(chat_id)) >= C.WATCH_MAX:
        return None, None, (f"Следить можно максимум за {C.WATCH_MAX} направлениями — "
                            "удали лишнее в «🔔 Мои направления».")
    d = None
    try:
        async with aiohttp.ClientSession() as s:
            d = best(await fetch(s, origin, dest), db.get_sub(chat_id), on_date)
    except Exception as e:
        print(f"  слежка {origin}-{dest}: {e}")
    wid = db.add_watch(chat_id, origin, dest, d["price"] if d else None,
                       d["depart"] if d else None, on_date)
    return wid, d, None


def route(w):
    """«Киров → Москва» или «Киров → Москва, 18 окт» — у слежки за рейсом."""
    r = f"{places.name(w['origin'])} → {places.name(w['dest'])}"
    return r + (f", {render.when(w['on_date'])}" if w["on_date"] else "")


def line(i, w):
    """Строка списка: маршрут, лучшая цена последней проверки, своя цена."""
    if w["cur_price"]:
        now_ = f"{'' if w['on_date'] else 'от '}<b>{render.money(w['cur_price'])}</b>"
        if not w["on_date"]:
            now_ += f" · {render.when_wd(w['cur_depart'])}"
    else:
        now_ = "<i>цен пока нет — проверяю</i>"
    if w["target"]:
        now_ += f" · 🎯 жду до {render.money(w['target'])}"
    return f"{i}. {route(w)}\n{render.INDENT}{now_}"


RULES = (f"Напишу, когда билет подешевеет на {C.WATCH_DROP_PCT}% от цены, "
         f"которую ты уже видел, или станет на {C.WATCH_PCT}% дешевле обычного "
         f"для этого маршрута. Проверяю каждые {C.POLL_EVERY_MIN} минут.\n"
         "🎯 — своя цена: напишу, когда билет станет не дороже неё. Следить можно "
         "и за одной датой — кнопка «🔔 Следить за этой датой» под билетом.")
