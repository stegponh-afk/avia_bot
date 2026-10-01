"""
Travelpayouts (Aviasales) Data API — основной источник цен.

Важно про природу данных: это кэш реальных поисков пользователей, он обновляется
не мгновенно и цена индикативная. Для «поймать дешёвый билет» годится, для
бронирования — нет, финальную цену показывает уже сайт по ссылке.
"""
import asyncio
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import date, datetime

import aiohttp

import config as C

BASE = "https://api.travelpayouts.com"
SITE = "https://www.aviasales.ru"


class ApiError(Exception):
    """
    Источник не ответил: сеть, лимит, ошибка на их стороне.

    Отдельное исключение, а не пустой список, потому что «билетов нет» и
    «спросить не удалось» ведут к разным действиям. Перепутаешь — и пост
    в канале помечается «не актуально», хотя билет на месте.
    """

    def __str__(self):
        return "источник цен сейчас не отвечает, попробуй через минуту"


# Остаток лимита из последнего ответа. Общий на весь процесс: бот, сборщик
# и обход ходят в API одновременно, и считать надо всех вместе.
_limit = {"left": None, "reset_at": 0.0}
last_error = None        # последняя ошибка источника — для сигнала владельцу


async def _respect_limit():
    """Остаток кончается — ждём, пока окно лимита обнулится."""
    left, reset_at = _limit["left"], _limit["reset_at"]
    wait = reset_at - time.monotonic()
    if left is not None and left <= C.API_RESERVE and wait > 0:
        print(f"  лимит API почти исчерпан ({left}), жду {wait:.0f} с")
        await asyncio.sleep(min(wait, 61))
        _limit["left"] = None


def _note_limit(headers):
    try:
        _limit["left"] = int(headers.get("X-Rate-Limit-Remaining"))
        _limit["reset_at"] = time.monotonic() + int(headers.get("X-Rate-Limit-Reset", 60))
    except (TypeError, ValueError):
        pass


def months_ahead(n):
    """['2026-09', '2026-10', ...] начиная с текущего месяца."""
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _ddmm(s):
    try:
        return datetime.fromisoformat(s[:10]).strftime("%d%m")
    except Exception:
        return None


def search_link(origin, dest, depart, ret=None):
    """Ссылка на поиск вида KZN1410AER1: коды, даты ддмм и число пассажиров."""
    d = _ddmm(depart or "")
    if not d:
        return f"{SITE}/?origin_iata={origin}&destination_iata={dest}"
    tail = _ddmm(ret) if ret else ""
    url = f"{SITE}/search/{origin}{d}{dest}{tail}1"
    return url + (f"?marker={C.TP_MARKER}" if C.TP_MARKER else "")


# В ссылке на билет зашит весь маршрут: t=TK1810714800...001305KZNISTNQZ_хэш_цена.
# Перед первым «_» — перевозчик, время вылета и прилёта, длительность и подряд
# коды аэропортов. Самого города пересадки API отдельным полем не отдаёт.
_T_PARAM = re.compile(r"[?&]t=([^&]+)")
_AIRPORTS = re.compile(r"([A-Z]{6,})$")


def itinerary(link):
    """Аэропорты маршрута из ссылки: ['KZN', 'IST', 'NQZ'] или None."""
    m = _T_PARAM.search(link or "")
    if not m:
        return None
    a = _AIRPORTS.search(m.group(1).split("_")[0])
    if not a or len(a.group(1)) % 3:
        return None
    s = a.group(1)
    return [s[i:i + 3] for i in range(0, len(s), 3)]


def via(d):
    """
    Города пересадок: [] у прямого, ['IST'] у одной пересадки, None — неизвестно.

    Аэропорты сводим к городам и склеиваем соседние: Шереметьево → Внуково —
    это одна пересадка в Москве со сменой аэропорта, а не две.
    """
    if d.get("via") is not None:
        return d["via"]
    airports = itinerary(d.get("link"))
    if not airports or len(airports) < 2:
        return None
    import places
    cities = []
    for a in airports:
        c = places.city_of(a)
        if not cities or cities[-1] != c:
            cities.append(c)
    return cities[1:-1]


_MARKER = re.compile(r"([?&]marker=)[^&#]*")


# Партнёрские программы, на которые бот делает ссылки: у каждой своя кампания
# и свой erid, поэтому параметры tp.media храним по паре (проект, программа).
BRANDS = {"aviasales": SITE + "/", "otello": "https://otello.ru/",
          "ostrovok": "https://ostrovok.ru/"}

# Параметры партнёрской ссылки tp.media: {(trs, программа): {campaign_id, erid, …}}.
# Узнаём у API раз в сутки (refresh_partner) и храним в базе на случай рестарта.
_partner = {}


def _meta_key(trs, brand):
    # у Aviasales ключ прежний — чтобы не потерять сохранённое до появления отелей
    return f"partner:{trs}" if brand == "aviasales" else f"partner:{trs}:{brand}"


def _partner_params(trs, brand="aviasales"):
    if not trs:
        return None
    if (trs, brand) not in _partner:
        import db
        raw = db.meta_get(_meta_key(trs, brand))
        _partner[(trs, brand)] = json.loads(raw) if raw else None
    return _partner[(trs, brand)]


def _without_marker(link):
    """Адрес без marker=: маркер и метку несёт сама партнёрская ссылка."""
    parts = urlsplit(link)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "marker"]
    return urlunsplit(parts._replace(query=urlencode(q)))


async def refresh_partner():
    """
    Узнать у Travelpayouts формат партнёрской ссылки для проектов канала и бота
    по каждой программе из BRANDS.

    Ответ API — готовая ссылка tp.media/r?campaign_id=…&erid=…&marker=…&p=…
    &sub_id=…&trs=…&u=…; из неё берём всё, кроме адреса и метки. erid —
    токен маркировки рекламы, он может смениться, поэтому спрашиваем раз
    в сутки, а не зашиваем в код. Не ответило — живём на сохранённом.
    Программа не подключена в кабинете — её ссылки идут без партнёрки.
    """
    import db
    if not (C.TP_TOKEN and C.TP_MARKER):
        return
    async with aiohttp.ClientSession() as s:
        for trs in sorted({C.TP_TRS_CHANNEL, C.TP_TRS_BOT} - {0}):
            body = {"trs": trs, "marker": int(C.TP_MARKER), "shorten": False,
                    "links": [{"url": url, "sub_id": "probe"} for url in BRANDS.values()]}
            try:
                async with s.post(f"{BASE}/links/v1/create", json=body, timeout=30,
                                  headers={"X-Access-Token": C.TP_TOKEN},
                                  proxy=C.API_PROXY) as r:
                    data = await r.json(content_type=None)
                got = data["result"]["links"]
            except Exception as e:
                print(f"  партнёрские ссылки проекта {trs}: не обновились — {e}")
                continue
            for brand, item in zip(BRANDS, got):
                try:
                    if item.get("code") != "success":
                        raise ValueError(item.get("code"))
                    params = {k: v for k, v in parse_qsl(urlsplit(item["partner_url"]).query)
                              if k not in ("u", "sub_id")}
                    if params.get("trs") != str(trs):
                        raise ValueError(f"в ответе другой проект: {params}")
                    _partner[(trs, brand)] = params
                    db.meta_set(_meta_key(trs, brand), json.dumps(params))
                    print(f"  партнёрские ссылки {brand}, проект {trs}: erid {params.get('erid')}")
                except Exception as e:
                    print(f"  партнёрские ссылки {brand}, проект {trs}: нет — {e}")


def tagged(link, sub, brand="aviasales"):
    """
    Партнёрская ссылка с меткой источника (SubID): ch_15, bot_alert…

    Проекты заданы — ссылка через tp.media в проект канала (метки ch_…)
    или бота (остальные): так в кабинете Travelpayouts видны клики, поиски
    и покупки с разбивкой по проекту и метке. Не заданы или API ещё ни разу
    не ответило — у Aviasales прямая ссылка marker=728672.метка: покупки
    считаются, клики нет; у отелей — просто ссылка на сайт.
    В метке допустимы только латиница, цифры и «_».
    Ставим при показе, а не при сохранении: в базе ссылки остаются чистыми.
    """
    if not link or not C.TP_MARKER or not sub:
        return link
    sub = re.sub(r"[^A-Za-z0-9_]", "_", str(sub))
    params = _partner_params(C.TP_TRS_CHANNEL if sub.startswith("ch") else C.TP_TRS_BOT,
                             brand)
    if params:
        q = dict(params, sub_id=sub, u=_without_marker(link))
        return "https://tp.media/r?" + urlencode(q)
    if brand != "aviasales":
        return link
    value = f"{C.TP_MARKER}.{sub}"
    if _MARKER.search(link):
        return _MARKER.sub(lambda m: m.group(1) + value, link, count=1)
    return link + ("&" if "?" in link else "?") + f"marker={value}"


def buy_link(d, sub=None):
    """
    Куда ведёт кнопка «Купить»: поиск по маршруту на эту дату, с маркером и меткой.

    Не на конкретный тариф из кэша. Ссылка с t=… открывает ровно тот билет,
    и если его раскупили — человек видит «Этот билет раскупили» и уходит.
    Дешёвые места на рейсе уходят за часы, пост живёт днями. Поиск на дату
    всегда показывает живые цены: остался тариф — он будет первым, нет —
    рядом варианты чуть дороже, а не тупик.

    Кэшевую ссылку при этом храним: из неё берутся города пересадок.
    """
    if not d.get("origin") or not d.get("dest") or not d.get("depart"):
        return tagged(d.get("link"), sub)
    return tagged(search_link(d["origin"], d["dest"], d["depart"], d.get("ret")), sub)


def _link(raw, origin, dest, depart, ret):
    """API отдаёт путь вида /search/...?t=... — достраиваем до полного адреса."""
    if raw:
        url = raw if raw.startswith("http") else SITE + raw
        if C.TP_MARKER:
            url += ("&" if "?" in url else "?") + f"marker={C.TP_MARKER}"
        return url
    return search_link(origin, dest, depart, ret)


def _norm(it, src, origin_default=None):
    """
    Строка ответа API -> единый вид предложения.

    Схемы у эндпоинтов разные, и это не мелочь: get_latest_prices называет цену
    value, дату depart_date, а пересадки number_of_changes. Если этого не знать,
    источник тихо отдаёт ноль строк вместо ошибки.
    """
    origin = it.get("origin") or origin_default or C.ORIGIN
    dest = it.get("destination")
    price = it.get("price") or it.get("value")
    if not dest or not price:
        return None
    if it.get("show_to_affiliates") is False:   # такую цену партнёру показывать нельзя
        return None

    depart_at = it.get("departure_at") or it.get("depart_date") or ""
    depart = depart_at[:10] or None
    ret = (it.get("return_at") or it.get("return_date") or "")[:10] or None
    transfers = it.get("transfers")
    if transfers is None:
        transfers = it.get("number_of_changes")
    link = _link(it.get("link"), origin, dest, depart, ret)
    stops = via({"link": link})
    if transfers is None and stops is not None:
        transfers = len(stops)          # часть источников число пересадок не отдаёт

    return {
        "origin": origin,
        "dest": dest,
        "depart": depart,
        # полное время вылета с поясом и длительность нужны для стыковок:
        # без них «в один день» — не стыковка, а лотерея
        "depart_at": depart_at or None,
        "duration": it.get("duration_to") or it.get("duration"),
        "from_airport": it.get("origin_airport") or origin,
        "to_airport": it.get("destination_airport") or dest,
        "ret": ret,
        "price": int(float(price)),
        "airline": it.get("airline"),
        "flight": str(it.get("flight_number") or ""),
        "transfers": transfers,
        "link": link,
        "via": stops,
        "src": src,
    }


class Travelpayouts:
    """Клиент на один город вылета: у каждого подписчика он может быть свой."""

    def __init__(self, session, origin=None):
        self.s = session
        self.origin = origin or C.ORIGIN

    async def _get(self, path, _delay=None, **params):
        """
        Запрос к API. None — не удалось спросить (не путать с пустым ответом).

        Упёрлись в лимит (429) — ждём, пока окно обнулится, и пробуем ещё раз:
        лучше ответить через минуту, чем соврать «ничего не нашлось».
        """
        if not C.TP_TOKEN:
            return None
        params["token"] = C.TP_TOKEN
        params["currency"] = C.CURRENCY
        for attempt in (1, 2):
            await _respect_limit()
            try:
                async with self.s.get(f"{BASE}{path}", params=params, timeout=40,
                                      proxy=C.API_PROXY) as r:
                    _note_limit(r.headers)
                    if r.status == 429 and attempt == 1:
                        wait = int(r.headers.get("X-Rate-Limit-Reset") or 60)
                        print(f"  tp {path}: лимит, жду {wait} с")
                        await asyncio.sleep(min(wait, 61))
                        continue
                    if r.status != 200:
                        global last_error
                        last_error = f"HTTP {r.status} на {path}"
                        print(f"  tp {path}: HTTP {r.status}")
                        return None
                    data = await r.json(content_type=None)
                    break
            except Exception as e:
                globals()["last_error"] = f"{type(e).__name__} на {path}"
                print(f"  tp {path}: {e}")
                return None
        else:
            return None
        await asyncio.sleep(C.TP_DELAY if _delay is None else _delay)
        if isinstance(data, dict) and data.get("success") is False:
            print(f"  tp {path}: {data.get('error')}")
            return None
        return data

    # --- самое дешёвое в каждом из ближайших месяцев ---
    async def grouped(self):
        """
        group_by принимает только departure_at и month: группировать по
        направлениям API не умеет, эту работу делает city-directions.
        Ключи ответа — месяцы, направление лежит внутри каждой записи.
        """
        d = await self._get("/aviasales/v3/grouped_prices", origin=self.origin,
                            group_by="month", direct=str(C.DIRECT_ONLY).lower())
        if not d:
            return []
        return [n for n in (_norm(it, "tp/grouped", self.origin)
                            for it in (d.get("data") or {}).values()) if n]

    # --- рабочая лошадка: все цены по месяцу, до 1000 строк ---
    async def month(self, ym):
        d = await self._get("/aviasales/v3/prices_for_dates", origin=self.origin,
                            departure_at=ym, one_way=str(C.ONE_WAY).lower(),
                            direct=str(C.DIRECT_ONLY).lower(),
                            sorting="price", limit=1000, page=1,
                            market="ru")
        if not d:
            return []
        return [n for n in (_norm(it, "tp/month", self.origin) for it in (d.get("data") or [])) if n]

    # --- последние найденные цены за год, широкий охват ---
    async def latest(self):
        d = await self._get("/aviasales/v3/get_latest_prices", origin=self.origin,
                            period_type="year", one_way=str(C.ONE_WAY).lower(),
                            limit=1000, page=1)
        if not d:
            return []
        return [n for n in (_norm(it, "tp/latest", self.origin) for it in (d.get("data") or [])) if n]

    # --- спецпредложения Aviasales ---
    async def special(self):
        """
        Подборка Aviasales «выгодные предложения из города».

        Старой цены в ответе нет, так что скидку всё равно считает бот —
        зато сюда попадают рейсы, которых нет в обычном кэше цен.
        """
        d = await self._get("/aviasales/v3/get_special_offers", origin=self.origin,
                            market="ru")
        if not d:
            return []
        return [n for n in (_norm(it, "tp/special", self.origin)
                            for it in (d.get("data") or [])) if n]

    # --- популярные направления города ---
    async def directions(self):
        d = await self._get("/v1/city-directions", origin=self.origin)
        if not d:
            return []
        out = []
        for dest, it in (d.get("data") or {}).items():
            it.setdefault("destination", dest)
            n = _norm(it, "tp/directions", self.origin)
            if n:
                out.append(n)
        return out

    # --- календарь цен по одному направлению, для команды /dest ---
    async def calendar(self, dest, ym):
        d = await self._get("/aviasales/v3/prices_for_dates", origin=self.origin,
                            destination=dest, departure_at=ym,
                            one_way=str(C.ONE_WAY).lower(), sorting="price",
                            limit=100, page=1, market="ru")
        if not d:
            return []
        return [n for n in (_norm(it, "tp/calendar", self.origin) for it in (d.get("data") or [])) if n]

    async def exact(self, dest, depart, ret=None, limit=5):
        """
        Цены на конкретные даты. ret задан — ищем билет туда-обратно
        одним заказом, иначе в одну сторону.
        """
        params = dict(origin=self.origin, destination=dest, departure_at=depart,
                      one_way="false" if ret else "true", sorting="price",
                      limit=limit, page=1, market="ru")
        if ret:
            params["return_at"] = ret
        d = await self._get("/aviasales/v3/prices_for_dates", **params)
        if d is None:
            raise ApiError()
        return [n for n in (_norm(it, "tp/exact", self.origin)
                            for it in (d.get("data") or [])) if n]

    async def route_dates(self, dest, until=None):
        """
        Самая дешёвая цена в одну сторону на каждую дату — на год вперёд,
        одним запросом. until — не дальше этой даты ('2027-03-31').
        """
        d = await self._get("/aviasales/v3/grouped_prices", _delay=C.SWEEP_DELAY,
                            origin=self.origin, destination=dest,
                            group_by="departure_at", market="ru")
        if not d:
            return []
        out = []
        for it in (d.get("data") or {}).values():
            n = _norm(it, "tp/sweep", self.origin)
            if n and not n.get("ret") and (not until or (n["depart"] or "") <= until):
                out.append(n)
        return out

    async def collect(self, months=None, light=False):
        """
        Полный проход по одному городу вылета, около десяти запросов.

        light=True — для хабов: только directions и latest, два запроса.

        Замерено на живом API: prices_for_dates игнорирует limit и отдаёт
        30 строк для Москвы и 3 для Казани, а get_latest_prices — 352 строки,
        все с временем вылета и длительностью. Так что для связок полезен
        именно latest, а помесячный обход хабу только тратит запросы.
        """
        deals = []
        if not light:
            deals += await self.grouped()
            for ym in (months or months_ahead(C.MONTHS_AHEAD)):
                deals += await self.month(ym)
            deals += await self.special()
        deals += await self.directions()
        deals += await self.latest()
        return deals

    async def works(self):
        """Летает ли вообще что-нибудь из этого города. Для проверки ввода."""
        got = await self.directions()
        return len(got)


async def collect(origin=None, months=None):
    async with aiohttp.ClientSession() as s:
        return await Travelpayouts(s, origin).collect(months)


async def validate_origin(code):
    """
    Проверка, что город вылета годится: есть в справочнике и из него летают.

    Одной проверки по справочнику мало — там 10 тысяч кодов, включая аэродромы,
    из которых рейсов нет. Поэтому спрашиваем API, знает ли он направления.
    Возвращает (годится, сколько направлений, подпись).
    """
    import places
    code = (code or "").upper()
    if not places.exists(code):
        return False, 0, None
    async with aiohttp.ClientSession() as s:
        try:
            n = await Travelpayouts(s, code).works()
        except Exception:
            n = 0
    return n > 0, n, places.full(code)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import places
    got = asyncio.run(collect())
    print(f"получено предложений: {len(got)}")
    for d in sorted(got, key=lambda x: x["price"])[:15]:
        print(f"  {d['price']:>7} ₽  {places.name(d['dest']):<20} {d['depart']}  {d['link'][:60]}")
