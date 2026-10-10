"""
Ссылки: откуда приходят люди — в бота и в канал, и во что это обошлось.

Ссылка ведёт в одно из двух мест:
  • в бота — t.me/бот?start=метка. Человек жмёт «Запустить», бот получает
    /start с меткой: записываем переход и пришёл ли человек впервые;
  • в канал — пригласительная ссылка t.me/+…, её создаёт бот (он админ).
    Вступления и выходы Telegram сообщает боту вместе со ссылкой, по которой
    вступили (handlers/channel.on_member, таблица member_events).

У ссылки может быть цена — сколько заплатили за рекламу с ней: тогда видно,
во сколько обошёлся оставшийся человек. Сам Telegram клики не считает,
поэтому «переход» — именно открыл бота или вступил в канал по ссылке.

Метку делаем из названия: «YouTube Shorts» -> youtube_shorts. Telegram
разрешает в ней только латиницу, цифры, _ и -, до 64 символов.
"""
import re
from datetime import date, datetime, timedelta

import card
import config as C
import db
import render

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m",
                     "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch",
                     "", "y", "", "e", "yu", "ya"]))

# метки, которые бот ставит сам, — в списке сразу
BUILTIN = {"channel": "Кнопка «Свои уведомления» в постах канала",
           "inline": "Карточки, отправленные из чатов (@бот Сочи)"}
TARGET = {"bot": "🤖", "channel": "📣"}


def slugify(name):
    s = "".join(TRANSLIT.get(ch, ch) for ch in name.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40]
    return s or "link"


def is_channel(link):
    return (link["target"] or "bot") == "channel"


async def create(bot, name, target="bot", cost=0):
    """
    Новая ссылка. Метка занята — добавляем номер: youtube, youtube_2…
    В канал — бот создаёт пригласительную ссылку с этим названием (видно и
    в списке ссылок канала в самом Telegram).
    """
    base = slugify(name)
    slug, n = base, 1
    while db.slug_taken(slug):
        n += 1
        slug = f"{base}_{n}"
    invite = None
    if target == "channel":
        inv = await bot.create_chat_invite_link(int(db.channel_id()), name=name[:32])
        invite = inv.invite_link
    return db.add_link(slug, name, target, cost, invite)


def seed():
    """Встроенные метки — в список при первом запуске, дальше как обычные."""
    for slug, name in BUILTIN.items():
        if not db.slug_taken(slug):
            db.add_link(slug, name)
        else:                                   # название могло поменяться в коде
            db.connect().execute("UPDATE links SET name=? WHERE slug=?", (name, slug))
            db.connect().commit()


def url(link):
    """Куда ведёт ссылка: пригласительная в канал или в бота с меткой."""
    if is_channel(link):
        return link["invite"]
    return f"https://t.me/{C.BOT_USERNAME}?start={link['slug']}"


def track(param, chat_id, is_new):
    """Записать переход в бота по /start с меткой. Мусор длиннее метки не пишем."""
    if param and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", param):
        db.add_visit(param, chat_id, is_new)


def periods():
    """Сегодня с полуночи, неделя и месяц — скользящие, от текущего момента."""
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (("Сегодня", today), ("Неделя", now - timedelta(days=7)),
            ("Месяц", now - timedelta(days=30)))


def counts(link, since):
    """(пришло, новых, осталось) с момента since. В канал «новые» — все вступившие."""
    s = since if isinstance(since, str) else since.isoformat(timespec="seconds")
    if is_channel(link):
        joined, stayed = db.invite_stats(link["invite"], s)
        return joined, joined, stayed
    return db.link_stats(link["slug"], s)


def stats_lines(link):
    out = []
    for title, since in periods():
        came, new, stayed = counts(link, since)
        if is_channel(link):
            out.append(f"{title}: <b>{came}</b> {render.plural(came, 'вступил', 'вступили', 'вступили')}"
                       f" · остались {stayed}")
        else:
            out.append(f"{title}: <b>{came}</b> {render.plural(came, 'переход', 'перехода', 'переходов')}"
                       f" · новых {new} · остались {stayed}")
    return out


def cost_line(link):
    """«💰 3 000 ₽ · 25 ₽ за оставшегося» — за всё время ссылки."""
    if not link["cost"]:
        return None
    stayed = counts(link, link["created_at"])[2]
    each = f" · {render.money(link['cost'] / stayed)} за оставшегося" if stayed else \
        " · пока никого не осталось"
    return f"💰 Заплачено {render.money(link['cost'])}{each}"


def today_count(link):
    return counts(link, periods()[0][1])[0]


def chart(link):
    """Картинка: по дням за 30 дней — пришли и (зелёным) новые / оставшиеся."""
    first = date.today() - timedelta(days=29)
    if is_channel(link):
        by_day = db.invite_daily(link["invite"], first.isoformat())
        legend, what = ("вступили", "остались"), "вступили"
    else:
        by_day = db.link_daily(link["slug"], first.isoformat())
        legend, what = ("переходы", "новые люди"), "переходы"
    days = []
    for i in range(30):
        day = (first + timedelta(days=i)).isoformat()
        total, new = by_day.get(day, (0, 0))
        days.append((day, total, new))
    total = sum(t for _, t, _ in days)
    where = "в канал" if is_channel(link) else f"в бота · ?start={link['slug']}"
    return card.bars(link["name"], f"{what} за 30 дней: {total} · {where}", days, legend=legend)
