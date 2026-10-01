"""
Прогон всех экранов бота без Telegram.

Подменяет сетевую сессию бота: вместо отправки запросы записываются, а в ответ
отдаётся правдоподобная заглушка. Нажимает кнопки и пишет текст так, как это
делал бы человек, и падает на первой же ошибке в обработчике.

    AVIA_DB=/data/test.db python selftest.py     на копии базы!

Источник цен при этом настоящий: календари и маршруты идут в API.
"""
import asyncio
import sys
import time
import traceback

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import db

CHAT = 777000111          # вымышленный новый пользователь
LOG = []


class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        text = getattr(method, "text", None)
        rich = getattr(method, "rich_message", None)
        markup = getattr(method, "reply_markup", None)
        LOG.append((name, text, rich, markup))
        ret = method.__returning__
        if ret is bool or ret == bool:
            return True
        if getattr(ret, "__name__", "") == "User":
            return User(id=1, is_bot=True, first_name="bot", username="test_bot")
        try:
            return Message(message_id=len(LOG), date=int(time.time()),
                           chat=Chat(id=CHAT, type="private"), text=text or "…")
        except Exception:
            return True

    async def stream_content(self, *a, **k):
        if False:
            yield b""

    async def close(self):
        pass


user = User(id=CHAT, is_bot=False, first_name="Тест")
_n = [0]


def msg(text):
    _n[0] += 1
    return Message(message_id=1000 + _n[0], date=int(time.time()),
                   chat=Chat(id=CHAT, type="private"), from_user=user, text=text)


def cb(data):
    _n[0] += 1
    return CallbackQuery(id=str(_n[0]), from_user=user, chat_instance="x", data=data,
                         message=msg("экран"))


def shown(since):
    """Что бот показал: первые строки каждого сообщения и подписи кнопок."""
    out = []
    for name, text, rich, markup in LOG[since:]:
        if name in ("SendChatAction", "AnswerCallbackQuery", "DeleteMessage"):
            continue
        body = text
        if rich is not None:
            body = "[rich] " + " | ".join(
                str(getattr(b, "text", "") or type(b).__name__)[:60]
                for b in rich.blocks[:4])
        if body and body.startswith("🔎") or (body or "").startswith("🎫 <b>Считаю"):
            continue            # кадры индикатора загрузки
        buttons = []
        if markup is not None and hasattr(markup, "inline_keyboard"):
            buttons = [b.text for row in markup.inline_keyboard for b in row]
        elif rich is not None:
            for b in rich.blocks:
                for x in getattr(b, "buttons", []) or []:
                    buttons.append(x.text)
        first = (body or "").replace("\n", " ⏎ ")[:150]
        out.append(f"   {name}: {first}" + (f"\n      кнопки: {buttons[:10]}" if buttons else ""))
    return "\n".join(out)


async def main():
    import bot as B
    import notify
    notify._bot = Bot("1:TEST", session=FakeSession())
    tg = notify._bot
    dp = B.dp

    steps = [
        ("/start нового по ссылке", msg("/start test_youtube")),
        ("город Казань", cb("start:KZN")),
        ("🔥 Скидки", msg("🔥 Скидки")),
        ("вкладка дешевле", cb("feed:cheap")),
        ("вкладка скидки", cb("feed:deals")),
        ("карточка Минск", cb("deal:KZN:MSQ")),
        ("карточка устаревшая", cb("deal:KZN:ZZZ")),
        ("🔍 Найти билет", msg("🔍 Найти билет")),
        ("текст: Минск", msg("Минск")),
        ("кнопка направления", cb("dest:AER")),
        ("туда-обратно", cb("rt:AER")),
        ("даты", msg("18.10 25.10")),
        ("соседняя дата", cb("trip:KZN:AER:2026-10-19:2026-10-26")),
        ("текст с датами", msg("Киров Питер 18.10")),
        ("текст: два города", msg("Киров Москва")),
        ("🔔 следить из календаря", cb("watch:add:KVX:MOW")),
        ("календарь чужого города", cb("dest:MOW:KVX")),
        ("🔔 Мои направления", msg("🔔 Мои направления")),
        ("добавить", cb("watch:new")),
        ("…два города текстом", msg("Питер → Калининград")),
        ("список кнопкой", cb("watch:list")),
        ("⚙️ Настройки", msg("⚙️ Настройки")),
        ("что присылать", cb("set:mode")),
        ("суперскидки", cb("mode:super")),
        ("бюджет", cb("set:budget")),
        ("до 10 000", cb("budget:10000")),
        ("своя сумма", cb("budget:custom")),
        ("…нажал меню вместо суммы", msg("🔥 Скидки")),
        ("когда", cb("set:dates")),
        ("ноябрь", cb("dates:m:2026-11")),
        ("свой период", cb("dates:custom")),
        ("период текстом", msg("01.12 15.12")),
        ("откуда", cb("set:origin")),
        ("другой город", cb("origin:other")),
        ("текстом: Киров", msg("Киров")),
        ("назад", cb("set:home")),
        ("выключить", cb("mode:off")),
        ("включить все скидки", cb("mode:deals")),
        ("старая кнопка", msg("🏆 Топ за сутки")),
        ("/start снова", msg("/start")),
        ("ерунда", msg("абвгд")),
        ("/admin", msg("/admin")),
        # ниже — только если CHAT в AVIA_OWNER_IDS, иначе кнопки молчат
        ("🛠 Админка", msg("🛠 Админка")),
        ("создать ссылку", cb("links:new")),
        ("…название", msg("Тест YouTube")),
        ("переход по ней", msg("/start test_youtube")),
        ("список ссылок", cb("links:list")),
        ("карточка ссылки", cb("links:show:1")),
        ("обновить", cb("links:upd:1")),
        ("оформление", cb("admin:style")),
    ]
    failed = 0
    for title, obj in steps:
        since = len(LOG)
        upd = Update(update_id=_n[0], **({"callback_query": obj}
                                         if isinstance(obj, CallbackQuery)
                                         else {"message": obj}))
        try:
            await dp.feed_update(tg, upd)
            await asyncio.sleep(0.05)
            print(f"✅ {title}\n{shown(since)}")
        except Exception:
            failed += 1
            print(f"❌ {title}")
            traceback.print_exc()
    s = db.get_sub(CHAT)
    print("\nитог подписчика:", dict(s) if s else None)
    print("ошибок:", failed)
    return failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(1 if asyncio.run(main()) else 0)
