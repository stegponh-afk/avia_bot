"""
Картинки карточек по ссылке — для бота в чатах (инлайн).

Карточку-картинку в ответ на «@AviaChecker_bot Сочи» Telegram берёт только
по адресу в интернете: загрузить файл в ответ нельзя. Поэтому бот держит
маленький веб-сервер, который отдаёт ровно эти картинки и больше ничего.

Ответ инлайн-режима кладёт находку сюда (put) и отдаёт Telegram адрес
/c/<метка>.jpg; рисуем, только когда Telegram за ней пришёл, и запоминаем.
Через час метки забываются: Telegram к этому времени картинку уже скачал.

Адрес снаружи — AVIA_WEB_URL (http://IP:порт), порт внутри — AVIA_WEB_PORT.
Не задан адрес — сервер не поднимается, инлайн отвечает текстом.
"""
import asyncio
import hashlib
import io
import json
import time

from aiohttp import web
from PIL import Image

import config as C

TTL = 3600
_store = {}            # метка -> {"at", "d", "c", "full", "thumb"}


def put(d, c):
    """Запомнить находку для картинки; вернуть метку для адреса."""
    raw = json.dumps([d.get("origin"), d.get("dest"), d.get("depart"), d.get("price"),
                      c.get("kind"), c.get("pct")], ensure_ascii=False)
    token = hashlib.sha1(raw.encode()).hexdigest()[:16]
    if token not in _store:
        _store[token] = {"at": time.time(), "d": d, "c": c}
    now = time.time()
    for k in [k for k, v in _store.items() if now - v["at"] > TTL]:
        _store.pop(k, None)
    return token


def url(token, thumb=False):
    return f"{C.WEB_URL.rstrip('/')}/{'t' if thumb else 'c'}/{token}.jpg"


def _jpeg(png, size=None):
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if size:
        img.thumbnail(size)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


def _render(item):
    import post
    png = post.image(item["d"], item["c"])
    item["full"], item["thumb"] = _jpeg(png), _jpeg(png, (320, 180))


async def _handle(request, thumb):
    item = _store.get(request.match_info["token"])
    if not item:
        raise web.HTTPNotFound()
    if "full" not in item:
        await asyncio.to_thread(_render, item)
        print(f"  картинка для чата: {item['d'].get('origin')}-{item['d'].get('dest')} "
              f"{item['d'].get('depart')}")
    return web.Response(body=item["thumb" if thumb else "full"], content_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


async def serve():
    """
    Вечная задача: отдаёт картинки, пока жив бот. Не поднялся (порт занят) —
    пишем и живём дальше: инлайн тогда ответит текстом, а бот не должен
    падать из-за картинок.
    """
    global _up
    try:
        app = web.Application()
        app.router.add_get("/c/{token}.jpg", lambda r: _handle(r, False))
        app.router.add_get("/t/{token}.jpg", lambda r: _handle(r, True))
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", C.WEB_PORT).start()
        _up = True
        print(f"  картинки для чатов: порт {C.WEB_PORT}, снаружи {C.WEB_URL}")
    except Exception as e:
        print(f"  картинки для чатов не поднялись: {e}")
    await asyncio.Event().wait()


_up = False


def ready():
    """Можно ли отвечать в чатах картинками."""
    return bool(C.WEB_URL) and _up
