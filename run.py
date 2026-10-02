"""
Точка входа: поднимает бота, сборщик цен и читалку каналов в одном процессе.

    python run.py                 на своей машине
    docker compose up -d          на сервере

Останавливается по Ctrl+C. Ничего не теряется: вся история в базе.
"""
import asyncio
import sys

import bot
import channel
import config as C
import db
import detect
import places
import poll
import tgwatch
import tp


def banner():
    """Что именно запустилось — первым делом видно в логах контейнера."""
    print("=" * 52)
    print(f"Дешёвые билеты, вылет {C.ORIGIN} — {places.full(C.ORIGIN)}")
    print(f"  скидки        от {C.DEAL_PCT}%, суперскидки от {C.SUPER_PCT}%"
          f"{' (в одну сторону)' if C.ONE_WAY else ' (туда-обратно)'}")
    print(f"  опрос         каждые {C.POLL_EVERY_MIN} мин, "
          f"горизонт {C.MONTHS_AHEAD} мес")
    print(f"  база          {C.DB}")
    print(f"  прокси бота   {C.BOT_PROXY or 'нет, напрямую'}")
    if not C.TP_TOKEN:
        print("  ВНИМАНИЕ: пустой TP_TOKEN — источник цен не работает")
    if C.TG_WATCH_ENABLED and C.TG_CHANNELS:
        print(f"  каналы        {', '.join(C.TG_CHANNELS)}")
    if db.channel_id():
        print(f"  свой канал    {db.channel_id()}: {', '.join(C.CHANNEL_ORIGINS)}, "
              f"скидка от {C.CHANNEL_DEAL_PCT}%, падение от {C.CHANNEL_DROP_PCT}%")
        if C.CHANNEL_AUTO:
            print(f"  автопубликация раз в {C.CHANNEL_GAP_MIN} мин, "
                  f"без звука {C.CHANNEL_QUIET[0]}–{C.CHANNEL_QUIET[1]} ч")
        else:
            print(f"  черновики     {C.OWNER_IDS or 'НИКОМУ: задай AVIA_OWNER_IDS'}")
    print("=" * 52)


async def on_channel_post(deal):
    """Пост из канала приходит поштучно, поэтому прогоняем через ту же логику."""
    alerts = detect.pick([deal])
    if alerts:
        await bot.notify(alerts)


async def main():
    banner()
    # формат партнёрских ссылок — до первой кнопки, иначе первые посты уйдут
    # с прямыми ссылками без учёта кликов
    await tp.refresh_partner()
    import links
    links.seed()
    tasks = [
        asyncio.create_task(bot.start(), name="бот"),
        asyncio.create_task(poll.loop(bot.notify, channel.on_poll), name="опрос цен"),
        asyncio.create_task(channel.publisher(), name="автопубликация"),
    ]
    if C.WEB_URL:
        import cardweb
        tasks.append(asyncio.create_task(cardweb.serve(), name="картинки для чатов"))
    if C.TG_WATCH_ENABLED and C.TG_CHANNELS:
        tasks.append(asyncio.create_task(tgwatch.watch(on_channel_post), name="каналы"))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        if t.exception():
            print(f"задача «{t.get_name()}» упала: {t.exception()}")
    for t in pending:
        t.cancel()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nостановлено")
