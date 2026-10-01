"""
Телеграм-бот: собирает обработчики и запускает опрос Telegram.

Сами экраны — в handlers/, рассылка — в notify.py. Здесь только запуск,
чтобы по этому файлу было видно, из чего бот состоит.
"""
from aiogram import Dispatcher
from aiogram.types import BotCommand

import config as C
import handlers
from notify import make_bot, notify   # noqa: F401 — их берут run.py и check.py

dp = Dispatcher()
handlers.setup(dp)


async def start():
    if not C.BOT_TOKEN:
        raise SystemExit("Не задан токен бота: получи у @BotFather и положи "
                         "в переменную окружения AVIA_BOT_TOKEN")
    bot = make_bot()
    me = await bot.get_me()
    # В списке ☰ — одна команда. Всё остальное делается кнопками,
    # а десяток команд только пугает.
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    print("бот: @{} на связи".format(me.username))
    await dp.start_polling(bot, handle_signals=False)
