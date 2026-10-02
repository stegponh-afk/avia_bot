"""
Обработчики Telegram, по файлу на экран.

    start     знакомство: один вопрос «откуда летаешь»
    menu      три кнопки нижнего меню (и кнопки старых версий)
    feed      🔥 Скидки / 💰 Дешевле всего
    search    🔍 Найти билет: календарь, даты, пересадка
    watch     🔔 Мои направления: слежка за конкретным маршрутом
    settings  ⚙️ откуда, что присылать, бюджет, когда
    admin     /admin и кнопка «🛠 Админка», служебное
    links     ссылки-отслеживания: создать, переходы по дням
    channel   черновики для канала: опубликовать / пропустить
    fallback  свободный текст

Порядок подключения важен: меню раньше состояний ввода (нажатая кнопка
главнее недописанного ответа), свободный текст — последним.
"""
from . import (admin, channel, fallback, feed, inline, links, menu, search, settings,
               start, watch)


def setup(dp):
    for module in (start, menu, feed, search, watch, settings, admin, links, channel,
                   inline, fallback):
        dp.include_router(module.router)
