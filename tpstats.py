"""
Клики и продажи из Travelpayouts — по меткам (SubID), которые бот ставит
в ссылки: ch_15 (пост 15, поиск), ch_15_t (билет), ch_15_back, ch_15_hotel,
bot_alert, bot_watch, bot_inline…

API статистики партнёрских программ: каждое событие отдельной строкой.
type=redirect — переход по ссылке (клик), action — покупка/бронь.
Доход — оплаченный (paid_profit) плюс ещё в обработке (processing_profit).
"""
import re

import aiohttp

import config as C

URL = "https://api.travelpayouts.com/statistics/v1/execute_query"
FIELDS = ["type", "sub_id", "campaign_id", "paid_profit_rub", "processing_profit_rub",
          "price_rub", "state", "date"]
_POST = re.compile(r"^ch_(\d+)(?:_(t|back|hotel))?$")
BUTTON = {None: "поиск", "t": "билет", "back": "обратно", "hotel": "отели"}


async def events(since):
    """Все события с даты since ('YYYY-MM-DD'), до 20 000 штук."""
    rows, offset = [], 0
    async with aiohttp.ClientSession() as s:
        while offset < 20000:
            q = {"fields": FIELDS, "filters": [{"field": "date", "op": "ge", "value": since}],
                 "sort": [{"field": "date", "order": "desc"}], "offset": offset, "limit": 1000}
            async with s.post(URL, json=q, headers={"X-Access-Token": C.TP_TOKEN},
                              timeout=aiohttp.ClientTimeout(total=30), proxy=C.API_PROXY) as r:
                got = (await r.json(content_type=None)).get("results") or []
            rows += got
            if len(got) < 1000:
                break
            offset += 1000
    return rows


def _money(r):
    total = 0.0
    for k in ("paid_profit_rub", "processing_profit_rub"):
        try:
            total += float(r.get(k) or 0)
        except ValueError:
            pass
    return total


def summarize(rows):
    """
    {"clicks", "sales", "profit", "by_button": {поиск: n,…}, "by_post": {id: {clicks, sales,
    profit}}, "by_source": {канал|бот|другое: {clicks, sales, profit}}}.
    """
    out = {"clicks": 0, "sales": 0, "profit": 0.0, "by_button": {}, "by_post": {},
           "by_source": {}}
    for r in rows:
        sub, kind = r.get("sub_id") or "", r.get("type")
        click, sale = kind == "redirect", kind == "action"
        if not (click or sale):
            continue
        money = _money(r) if sale else 0.0
        m = _POST.match(sub)
        src = "канал" if sub.startswith("ch") else "бот" if sub.startswith("bot") else "другое"
        for bucket in [out, out["by_source"].setdefault(src, {"clicks": 0, "sales": 0, "profit": 0.0})] + \
                ([out["by_post"].setdefault(int(m.group(1)), {"clicks": 0, "sales": 0, "profit": 0.0})]
                 if m else []):
            bucket["clicks"] += click
            bucket["sales"] += sale
            bucket["profit"] += money
        if m and click:
            b = BUTTON[m.group(2)]
            out["by_button"][b] = out["by_button"].get(b, 0) + 1
    return out
