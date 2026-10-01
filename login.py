"""
Разовая авторизация Telegram для чтения каналов.

В обычном запуске бот не может спросить код — у контейнера нет терминала.
Поэтому один раз делаем так:

    docker compose run --rm bot python login.py

Код придёт В TELEGRAM, не в SMS. Сессия ляжет в /data и переживёт пересборку.
"""
import asyncio
import sys

import config as C
import tgwatch


async def main():
    if not (C.API_ID and C.API_HASH):
        sys.exit("Не заданы TG_API_ID / TG_API_HASH — возьми их на my.telegram.org")

    client = tgwatch.make_client()
    await client.start()
    me = await client.get_me()
    print(f"\nГотово: вошёл как {me.first_name} (@{me.username or me.id})")
    print(f"Сессия: {C.TG_SESSION}.session")

    if C.TG_CHANNELS:
        print("\nПроверяю каналы:")
        for ch in C.TG_CHANNELS:
            try:
                e = await client.get_entity(ch)
                print(f"  OK    @{ch} — {getattr(e, 'title', '?')}")
            except Exception as e:
                print(f"  ПЛОХО @{ch} — {e}")
    else:
        print("\nСписок каналов пуст: заполни AVIA_TG_CHANNELS в .env")

    await client.disconnect()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
