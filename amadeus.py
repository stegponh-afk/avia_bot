"""
Amadeus Self-Service API — дополнение по международным направлениям.

Российских перевозчиков и внутренние рейсы почти не покрывает, поэтому в конфиге
выключен по умолчанию. Из Казани реально отдаёт Стамбул, Дубай, Ташкент и подобное.
Цены приходят в евро — пересчитываем по курсу из конфига.
"""
import time

import aiohttp

import config as C

_token = {"value": None, "exp": 0}


async def _auth(s):
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    if not (C.AMADEUS_KEY and C.AMADEUS_SECRET):
        return None
    try:
        async with s.post(f"https://{C.AMADEUS_HOST}/v1/security/oauth2/token",
                          data={"grant_type": "client_credentials",
                                "client_id": C.AMADEUS_KEY,
                                "client_secret": C.AMADEUS_SECRET},
                          timeout=30, proxy=C.API_PROXY) as r:
            d = await r.json(content_type=None)
    except Exception as e:
        print(f"  amadeus auth: {e}")
        return None
    if "access_token" not in d:
        print(f"  amadeus auth: {d.get('error_description', d)}")
        return None
    _token["value"] = d["access_token"]
    _token["exp"] = time.time() + int(d.get("expires_in", 1700))
    return _token["value"]


async def collect(session=None):
    """Flight Inspiration Search: куда из Казани можно улететь и почём."""
    if not C.AMADEUS_ENABLED:
        return []
    own = session is None
    s = session or aiohttp.ClientSession()
    try:
        tok = await _auth(s)
        if not tok:
            return []
        url = f"https://{C.AMADEUS_HOST}/v1/shopping/flight-destinations"
        params = {"origin": C.ORIGIN, "oneWay": str(C.ONE_WAY).lower(),
                  "maxPrice": int(C.PRICE_MAX / C.EUR_RUB) + 1}
        try:
            async with s.get(url, params=params, timeout=40, proxy=C.API_PROXY,
                             headers={"Authorization": f"Bearer {tok}"}) as r:
                if r.status != 200:
                    # для KZN пустой ответ или 500 — обычное дело, это не поломка
                    print(f"  amadeus: HTTP {r.status}, пропускаю")
                    return []
                d = await r.json(content_type=None)
        except Exception as e:
            print(f"  amadeus: {e}")
            return []

        out = []
        for it in d.get("data", []):
            try:
                price = int(float(it["price"]["total"]) * C.EUR_RUB)
            except Exception:
                continue
            dest = it.get("destination")
            out.append({
                "origin": C.ORIGIN, "dest": dest,
                "depart": it.get("departureDate"), "ret": it.get("returnDate"),
                "price": price, "airline": None, "flight": "", "transfers": None,
                "link": it.get("links", {}).get("flightOffers")
                        or f"https://www.aviasales.ru/?origin_iata={C.ORIGIN}"
                           f"&destination_iata={dest}",
                "src": "amadeus",
            })
        return out
    finally:
        if own:
            await s.close()
