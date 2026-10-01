"""
Чтение каналов про горящие билеты твоим аккаунтом (Telethon).

Самые сочные цены — ошибочные тарифы и разовые распродажи — в API не попадают
вообще, они живут в каналах. Берём посты, где рядом встречаются Казань и сумма
в рублях, и отдаём как обычное предложение.

Сессия своя (avia_session), с чужими проектами не конфликтует.
"""
import re

from telethon import TelegramClient, events

import config as C

# Казань в любом падеже, KZN как код
KAZAN = re.compile(r"\bказан[ьиюе]\w*|\bKZN\b", re.I)
# 4 990 ₽ / от 5000 руб / 7500р
MONEY = re.compile(r"(\d{1,3}(?:[   ]\d{3})+|\d{3,6})\s*(?:₽|руб|rub|р\b|р\.)", re.I)
# «Казань — Сочи», «Казань -> Стамбул»
ROUTE = re.compile(r"казан\w*\s*(?:—|-|–|->|→|в)\s*([А-ЯЁA-Z][а-яёa-z\-]{2,20})", re.I)


def parse(text):
    """Из текста поста -> (минимальная цена, куда) или (None, None)."""
    if not text or not KAZAN.search(text):
        return None, None
    prices = []
    for m in MONEY.finditer(text):
        try:
            v = int(re.sub(r"\D", "", m.group(1)))
        except ValueError:
            continue
        if 500 <= v <= 200000:
            prices.append(v)
    if not prices:
        return None, None
    r = ROUTE.search(text)
    return min(prices), (r.group(1).capitalize() if r else None)


def make_client():
    """Клиент с прокси из конфига: SOCKS5, MTProxy-словарь или напрямую."""
    kw = dict(connection_retries=5, timeout=20, request_retries=5)
    p = C.TG_PROXY
    if not p:
        return TelegramClient(C.TG_SESSION, C.API_ID, C.API_HASH, **kw)
    if isinstance(p, dict):
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        return TelegramClient(
            C.TG_SESSION, C.API_ID, C.API_HASH,
            connection=ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=(p["server"], int(p["port"]), p["secret"]), **kw)
    return TelegramClient(C.TG_SESSION, C.API_ID, C.API_HASH, proxy=p, **kw)


async def watch(on_deal):
    """
    Держит подписку на каналы и зовёт on_deal(deal) на каждый подходящий пост.
    Работает вечно, поэтому запускается отдельной задачей в run.py.
    """
    if not C.TG_WATCH_ENABLED or not C.TG_CHANNELS:
        return
    if not C.API_ID or not C.API_HASH:
        print("каналы: не заполнены API_ID/API_HASH в config.py — читалка не поднята")
        return

    client = make_client()
    await client.start()
    me = await client.get_me()
    print(f"каналы: вошёл как {me.first_name}, слушаю {len(C.TG_CHANNELS)} шт.")

    chats = []
    for ch in C.TG_CHANNELS:
        try:
            chats.append(await client.get_entity(ch))
        except Exception as e:
            print(f"  канал {ch}: {e}")
    if not chats:
        print("каналы: ни один не открылся, читалка остановлена")
        return

    @client.on(events.NewMessage(chats=chats))
    async def handler(ev):
        price, where = parse(ev.raw_text)
        if not price:
            return
        chan = getattr(ev.chat, "username", None) or "c"
        await on_deal({
            "origin": C.ORIGIN,
            "dest": (where or "?"),
            "depart": None, "ret": None,
            "price": price,
            "airline": None, "flight": "", "transfers": None,
            "link": f"https://t.me/{chan}/{ev.id}",
            "src": "tg",
            "note": f"пост в @{chan}",
            "text": ev.raw_text[:400],
        })

    await client.run_until_disconnected()


async def backfill(limit=50):
    """Разовый проход по последним постам каналов — чтобы не ждать новых."""
    if not (C.TG_WATCH_ENABLED and C.TG_CHANNELS and C.API_ID):
        return []
    client = make_client()
    await client.start()
    out = []
    for ch in C.TG_CHANNELS:
        try:
            async for msg in client.iter_messages(ch, limit=limit):
                price, where = parse(msg.raw_text or "")
                if price:
                    out.append({
                        "origin": C.ORIGIN, "dest": where or "?", "depart": None,
                        "ret": None, "price": price, "airline": None, "flight": "",
                        "transfers": None, "link": f"https://t.me/{ch}/{msg.id}",
                        "src": "tg", "note": f"пост в @{ch}",
                        "text": (msg.raw_text or "")[:400],
                    })
        except Exception as e:
            print(f"  канал {ch}: {e}")
    await client.disconnect()
    return out
