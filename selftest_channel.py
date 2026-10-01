"""
Прогон канала без Telegram: опрос, отбор, черновики, публикация.

    AVIA_DB=/data/test.db AVIA_CHANNEL=-100... AVIA_OWNER_IDS=1 python selftest_channel.py

Бот поддельный (сессия из selftest.py): сообщения никуда не уходят,
а картинки черновиков сохраняются в /tmp для просмотра.
"""
import asyncio
import re
import sys

from aiogram import Bot

import channel
import db
import detect
import poll
import selftest as T


async def main():
    import notify
    notify._bot = Bot("1:TEST", session=T.FakeSession())
    print("города опроса:", db.origins())
    deals = await poll.gather_all()
    found = detect.evaluate(deals)
    db.save_feed(found)

    picked = [(d, c) for d in found for c in [channel.candidate(d)] if c]
    print(f"размечено {len(found)}, в канал годится {len(picked)}")
    near = sorted(((d, channel.candidate(d, 1, 1)) for d in found
                   if d["origin"] in ("MOW", "LED")), key=lambda x: -(x[1] or {}).get("pct", 0))
    print("лучшее из Москвы и Питера без порогов:")
    for d, c in near[:10]:
        if c:
            print(f"   {d['origin']}→{d['dest']:<4} {c['kind']:<5} {c['pct']:>3}% "
                  f"{d['price']:>6} вместо {c['usual']:>6} {d['depart']} ({c['basis']})")

    since = len(T.LOG)
    n = await channel.propose(found)
    print("черновиков отправлено:", n)
    for name, text, rich, markup in T.LOG[since:]:
        print("  ", name)
    posts = db.connect().execute("SELECT * FROM posts ORDER BY id").fetchall()
    for p in posts[:5]:
        import json
        data = json.loads(p["payload"])
        print("\n" + re.sub("<[^>]+>", "", channel.caption(data["deal"], data["cand"])))
    if posts:
        import json
        data = json.loads(posts[0]["payload"])
        open("/tmp/draft.png", "wb").write(channel.image(data["deal"], data["cand"]))
        ok, note, url = await channel.publish(notify._bot, posts[0]["id"])
        print("\nпубликация первого:", ok, note, url)
        again = await channel.publish(notify._bot, posts[0]["id"])
        print("повторное нажатие:", again[:2])

    print("\nправило повторов на то же направление:",
          channel.repost_ok(posts[0]["key"], posts[0]["price"]) if posts else "-")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
