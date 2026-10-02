"""
Ссылки-отслеживания: откуда приходят люди.

Ссылка — это t.me/бот?start=метка. Человек открывает её, жмёт «Запустить»,
и бот получает /start с меткой: записываем переход и пришёл ли человек
впервые. Сам Telegram клики не считает, поэтому «переход» здесь — именно
открыл бота по ссылке, а не просто нажал на неё где-то в YouTube.

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

# метка из кнопки «Свои уведомления» в постах канала — считаем её всегда
BUILTIN = {"channel": "📣 Кнопка в постах канала",
           "inline": "📤 Пересылки из чатов (@бот в переписке)"}


def slugify(name):
    s = "".join(TRANSLIT.get(ch, ch) for ch in name.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40]
    return s or "link"


def create(name):
    """Новая ссылка. Метка занята — добавляем номер: youtube, youtube_2…"""
    base = slugify(name)
    slug, n = base, 1
    while db.slug_taken(slug):
        n += 1
        slug = f"{base}_{n}"
    return db.add_link(slug, name)


def seed():
    """Встроенные метки — в список при первом запуске, дальше как обычные."""
    for slug, name in BUILTIN.items():
        if not db.slug_taken(slug):
            db.add_link(slug, name)


def url(slug):
    return f"https://t.me/{C.BOT_USERNAME}?start={slug}"


def track(param, chat_id, is_new):
    """Записать переход по /start с меткой. Мусор длиннее метки не пишем."""
    if param and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", param):
        db.add_visit(param, chat_id, is_new)


def periods():
    """Сегодня с полуночи, неделя и месяц — скользящие, от текущего момента."""
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (("Сегодня", today), ("Неделя", now - timedelta(days=7)),
            ("Месяц", now - timedelta(days=30)))


def stats_lines(slug):
    out = []
    for title, since in periods():
        v, new, stayed = db.link_stats(slug, since.isoformat(timespec="seconds"))
        out.append(f"{title}: <b>{v}</b> {render.plural(v,'переход', 'перехода', 'переходов')}"
                   f" · новых {new} · остались {stayed}")
    return out


def today_count(slug):
    return db.link_stats(slug, periods()[0][1].isoformat(timespec="seconds"))[0]


def chart(link):
    """Картинка: переходы по дням за 30 дней, зелёным — новые люди."""
    first = date.today() - timedelta(days=29)
    by_day = db.link_daily(link["slug"], first.isoformat())
    days = []
    for i in range(30):
        day = (first + timedelta(days=i)).isoformat()
        total, new = by_day.get(day, (0, 0))
        days.append((day, total, new))
    total = sum(t for _, t, _ in days)
    return card.bars(link["name"], f"переходы за 30 дней: {total} · ?start={link['slug']}",
                     days)
