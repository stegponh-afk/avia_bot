"""Диагностика. Запускать при любой ошибке — показывает, что именно не поднялось."""
import asyncio
import socket
import sys
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")

import config as C

OK, BAD = "  OK  ", " ПЛОХО"


def line(n, title, good, note=""):
    """Подсказку показываем только когда пункт красный — иначе она сбивает."""
    print(f"{OK if good else BAD}  {n}. {title}" + (f" — {note}" if note and not good else ""))
    return good


def port_open(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=5):
            return True
    except Exception:
        return False


async def main():
    print("Проверка окружения\n")

    line(1, "токен бота задан", bool(C.BOT_TOKEN),
         "" if C.BOT_TOKEN else "пусто: @BotFather -> /newbot -> в config.py")
    line(2, "токен Travelpayouts задан", bool(C.TP_TOKEN),
         "" if C.TP_TOKEN else "пусто: без него цен не будет")

    if C.BOT_PROXY:
        u = urlparse(C.BOT_PROXY)
        line(3, f"прокси для бота {u.hostname}:{u.port} открыт",
             port_open(u.hostname, u.port), "подними v2rayN/nekoray")
    else:
        line(3, "прокси для бота не используется", True, "подключение напрямую")

    # источник цен
    import aiohttp
    import tp
    async with aiohttp.ClientSession() as s:
        got = await tp.Travelpayouts(s).grouped()
    line(4, f"Travelpayouts отвечает, направлений: {len(got)}", bool(got),
         "" if got else "проверь TP_TOKEN и доступ в сеть")

    # справочник
    import places
    n = len(places.load())
    line(5, f"справочник направлений загружен: {n}", n > 1000)

    # телеграм
    if C.BOT_TOKEN:
        try:
            me = await bot_me()
            line(6, f"бот авторизован: @{me}", True)
        except Exception as e:
            line(6, "бот не авторизован", False, str(e)[:120])
    else:
        line(6, "проверка авторизации бота пропущена", False, "нет токена, см. пункт 1")

    if C.TG_WATCH_ENABLED and C.TG_CHANNELS:
        line(7, "читалка каналов настроена", bool(C.API_ID and C.API_HASH),
             "" if C.API_ID else "заполни API_ID/API_HASH с my.telegram.org")
    else:
        line(7, "читалка каналов выключена", True, "TG_WATCH_ENABLED / TG_CHANNELS")

    if got:
        import render
        print("\nСамое дешёвое прямо сейчас:")
        for d in sorted(got, key=lambda x: x["price"])[:5]:
            print("   " + render.deal(d).split("\n")[0]
                  .replace("<b>", "").replace("</b>", ""))


async def bot_me():
    import bot as B
    me = await B.make_bot().get_me()
    await B.make_bot().session.close()
    return me.username


if __name__ == "__main__":
    asyncio.run(main())
