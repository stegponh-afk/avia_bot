"""
Страницы акций авиакомпаний.

Акции появляются здесь раньше, чем прорастают в агрегаторы. Полноценно парсить
шесть разных сайтов смысла нет — они меняются. Поэтому просто: скачали страницу,
выкинули теги, собрали все суммы в рублях, сравнили с прошлым разом.
Новая сумма в нужном коридоре — повод прислать ссылку и посмотреть глазами.
"""
import hashlib
import re

import aiohttp

import config as C
import db

# 4 990 ₽ / 4990 руб. / 4 990 рублей
MONEY = re.compile(r"(\d{1,3}(?:[   ]\d{3})+|\d{3,6})\s*(?:₽|руб|р\.)", re.I)
TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def amounts(html):
    """Все суммы в рублях со страницы, без мусора вроде телефонов и годов."""
    text = TAGS.sub(" ", html)
    out = set()
    for m in MONEY.finditer(text):
        try:
            v = int(re.sub(r"\D", "", m.group(1)))
        except ValueError:
            continue
        if 500 <= v <= 200000:
            out.add(v)
    return out


async def check_one(s, title, url):
    try:
        async with s.get(url, timeout=30, proxy=C.API_PROXY,
                         headers={"User-Agent": UA}) as r:
            if r.status != 200:
                print(f"  {title}: HTTP {r.status}")
                return []
            html = await r.text(errors="ignore")
    except Exception as e:
        print(f"  {title}: {e}")
        return []

    found = amounts(html)
    h = hashlib.sha1(html.encode("utf-8", "ignore")).hexdigest()

    prev = db.page_state(url)
    old = set()
    if prev and prev["prices"]:
        old = {int(x) for x in prev["prices"].split(",") if x.isdigit()}
    db.save_page(url, h, found)

    if not prev:                      # первый прогон: запомнили, но не шумим
        return []

    fresh = sorted(p for p in found - old if C.PRICE_MIN <= p <= C.PRICE_MAX)
    return [{
        "origin": C.ORIGIN, "dest": "?", "depart": None, "ret": None,
        "price": p, "airline": title, "flight": "", "transfers": None,
        "link": url, "src": "airline",
        "note": f"новая цена на странице акций {title}",
    } for p in fresh]


async def collect(session=None):
    if not C.AIRLINES_ENABLED:
        return []
    own = session is None
    s = session or aiohttp.ClientSession()
    try:
        out = []
        for title, url in C.AIRLINE_PAGES:
            out += await check_one(s, title, url)
        return out
    finally:
        if own:
            await s.close()


if __name__ == "__main__":
    # проверка своей ссылки: python airlines.py [url]
    import asyncio
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    async def probe():
        urls = sys.argv[1:] or [u for _, u in C.AIRLINE_PAGES]
        async with aiohttp.ClientSession() as s:
            for url in urls:
                try:
                    async with s.get(url, timeout=25, proxy=C.API_PROXY,
                                     headers={"User-Agent": UA}) as r:
                        html = await r.text(errors="ignore")
                    a = sorted(amounts(html))
                    good = [x for x in a if C.PRICE_MIN <= x <= C.PRICE_MAX]
                    print(f"HTTP {r.status}  {len(html)//1024} КБ  сумм: {len(a)}  "
                          f"в коридоре: {good[:10]}  {url}")
                    if len(html) < 5000:
                        print("   страница почти пустая — цены рисует JavaScript, "
                              "источник бесполезен")
                except Exception as e:
                    print(f"{type(e).__name__}: {str(e)[:70]}  {url}")

    asyncio.run(probe())
