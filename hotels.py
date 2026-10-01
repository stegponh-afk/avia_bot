"""
Отели к билету: кнопка «🏨 Отели» под постом и под находкой в боте.

По России — Отелло (у него комиссия выше), по остальному миру — Островок.
Ссылка ведёт на поиск отелей в городе прилёта на даты поездки: заезд в день
вылета, выезд в день обратного билета, если он известен. Цен на кнопке нет:
API цен на отели у Travelpayouts больше не работает.

Адреса городов:
  • Отелло — готовый список otello.json. Сайт пускает только браузер (вход
    через Сбер ID), проверить адрес с сервера нельзя, поэтому список собран
    и проверен заранее. Города нет в списке — идём в Островок;
  • Островок — по названию города через его подсказки, ответ храним в базе.
"""
import json
import os
from datetime import date
from urllib.parse import quote

import aiohttp

import config as C
import db
import places
import render
import tp

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(_HERE, "otello.json"), encoding="utf-8") as f:
        OTELLO = json.load(f)
except OSError:
    OTELLO = {}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
GUESTS = quote(json.dumps([{"adults": 2}], separators=(",", ":")))


async def ostrovok_slug(code):
    """Адрес города у Островка ('turkey/antalya'). Не нашёлся — None, помним и это."""
    key = f"ostrovok:{code}"
    have = db.meta_get(key)
    if have is not None:
        return have or None
    name = places.name(code)
    slug = ""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA}) as s:
            async with s.get("https://ostrovok.ru/api/site/multicomplete.json",
                             params={"query": name, "locale": "ru"}, timeout=15,
                             proxy=C.API_PROXY) as r:
                data = await r.json(content_type=None)
        cities = [x for x in data.get("regions") or [] if x.get("type") == "City"]
        country = places.country(code)
        same = [x for x in cities if x.get("country") == country and x.get("name") == name]
        pick = (same or [x for x in cities if x.get("country") == country] or cities[:1])
        slug = pick[0]["slug"] if pick else ""
    except Exception as e:
        print(f"  островок {code}: {e}")
        return None                      # сеть — не повод запоминать «не найдено»
    db.meta_set(key, slug)
    return slug or None


async def find(d):
    """
    Отели к находке: {brand, url, city, checkin, checkout} или None.
    Даты — только когда известен выезд: иначе ссылка на город без дат.
    """
    dest = d.get("dest")
    if not dest or dest == "?" or len(dest) != 3 or not d.get("depart"):
        return None
    checkin = d["depart"][:10]
    checkout = d.get("ret") or (d.get("back") or {}).get("depart")
    if checkout and checkout[:10] <= checkin:
        checkout = None
    stay = {"city": places.name(dest), "checkin": checkin,
            "checkout": checkout[:10] if checkout else None}

    if places.country(dest) == "Россия" and dest in OTELLO:
        url = f"https://otello.ru/hotels/{OTELLO[dest]}"
        if stay["checkout"]:
            url += f"?checkin={checkin}&checkout={stay['checkout']}&guest_groups={GUESTS}"
        return dict(stay, brand="otello", url=url)

    slug = await ostrovok_slug(dest)
    if not slug:
        return None
    url = f"https://ostrovok.ru/hotel/{slug}/"
    if stay["checkout"]:
        url += f"?dates={_ddmmyyyy(checkin)}-{_ddmmyyyy(stay['checkout'])}&guests=2"
    return dict(stay, brand="ostrovok", url=url)


def _ddmmyyyy(iso):
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def label(stay):
    """«🏨 Отели: Сочи, 14–19 окт»."""
    text = f"🏨 Отели: {stay['city']}"
    if stay.get("checkout"):
        a, b = date.fromisoformat(stay["checkin"]), date.fromisoformat(stay["checkout"])
        text += (f", {a.day}–{b.day} {render.MONTHS[b.month - 1]}" if a.month == b.month
                 else f", {a.day} {render.MONTHS[a.month - 1]} – {b.day} "
                      f"{render.MONTHS[b.month - 1]}")
    return text


def link(stay, sub):
    """Партнёрская ссылка программы этого отеля, с меткой источника."""
    return tp.tagged(stay["url"], sub, stay["brand"])
