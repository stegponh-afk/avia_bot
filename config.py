# ---------- ЕДИНЫЙ КОНФИГ. Правишь только этот файл. ----------
#
# Любое значение ниже можно переопределить переменной окружения — это нужно,
# чтобы в Docker не пересобирать образ ради смены цены или прокси.
# Имя переменной указано в скобках рядом с настройкой.
import os


def _s(name, default):
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()                 # хвостовые пробелы из .env ломают токены молча
    return v if v else default


def _i(name, default):
    v = os.getenv(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _b(name, default):
    v = os.getenv(name)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "да", "on")


def _list(name, default):
    v = os.getenv(name)
    if v in (None, ""):
        return default
    return [x.strip() for x in v.split(",") if x.strip()]


def _proxy(name, default):
    """socks5://host:port  |  mtproxy://server:port:secret  |  пусто = напрямую."""
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v in ("", "none", "нет"):
        return None
    if v.startswith("mtproxy://"):
        host, port, secret = v[len("mtproxy://"):].split(":", 2)
        return {"server": host, "port": int(port), "secret": secret}
    return v


# 1. Токен бота от @BotFather.                                   (AVIA_BOT_TOKEN)
BOT_TOKEN = _s("AVIA_BOT_TOKEN", "")

# Кому разрешено пользоваться ботом. Пустой список = открыт всем.  (AVIA_ADMIN_IDS)
# Свой id бот покажет на /start, даже если доступа нет. Несколько — через запятую.
ADMIN_IDS = [int(x) for x in _list("AVIA_ADMIN_IDS", []) if x.lstrip("-").isdigit()]

# Кому видна служебная панель /admin: опрос вручную, связки, оформление.
# Пусто = всем, кто знает команду; в меню её нет.               (AVIA_OWNER_IDS)
OWNER_IDS = [int(x) for x in _list("AVIA_OWNER_IDS", []) if x.lstrip("-").isdigit()]

# ---------- 2. ГЛАВНОЕ: что считать дешёвым ----------
# Коридор цен для оповещения, рубли.          (AVIA_PRICE_MIN / AVIA_PRICE_MAX)
PRICE_MIN = _i("AVIA_PRICE_MIN", 5000)
PRICE_MAX = _i("AVIA_PRICE_MAX", 10000)

# Главное в боте — скидка к обычной цене маршрута, как «суперскидки» на
# Aviasales. Обычную цену бот знает сам: копит историю, сравнивает с соседними
# датами и с тем, сколько стоит перелёт на такое расстояние.
SUPER_PCT = _i("AVIA_SUPER_PCT", 50)     # от стольких % — «суперскидка»
DEAL_PCT  = _i("AVIA_DEAL_PCT", 25)      # от стольких % — просто скидка
FLASH_PCT = _i("AVIA_FLASH_PCT", 80)     # от стольких % — предупредить, что исчезнет
# Что присылать новым подписчикам: super — только суперскидки,
# deals — все скидки, budget — любые билеты дешевле бюджета.  (AVIA_MODE)
MODE = _s("AVIA_MODE", "deals")
NO_LIMIT = 1000000                       # «бюджет без ограничения»
USER_ALERTS_PER_RUN = _i("AVIA_USER_ALERTS", 3)   # не больше сообщений человеку за проход
# Ночью бот не пишет: находки копятся и утром уходят одним сообщением
BOT_QUIET = tuple(int(x) for x in _s("AVIA_BOT_QUIET", "23-8").split("-"))
NIGHT_KEEP = _i("AVIA_NIGHT_KEEP", 5)              # сколько находок за ночь показать утром

ORIGIN       = _s("AVIA_ORIGIN", "KZN")      # откуда. KZN = Казань
ONE_WAY      = _b("AVIA_ONE_WAY", True)      # False = цены туда-обратно
MONTHS_AHEAD = _i("AVIA_MONTHS", 6)          # горизонт поиска, месяцев
DIRECT_ONLY  = _b("AVIA_DIRECT", False)      # True = только прямые рейсы
CURRENCY     = "rub"

# ---------- 3. Источники ----------

# Travelpayouts (Aviasales) — основной. Токен бесплатный:
# travelpayouts.com -> регистрация -> Инструменты -> API -> токен.
TP_TOKEN  = _s("TP_TOKEN", "")               # без него цен не будет
TP_MARKER = _s("TP_MARKER", "")              # партнёрский маркер для ссылок
TP_DELAY  = 1.5                              # пауза между запросами к API, сек
# Проекты в кабинете Travelpayouts: кнопки канала и бота считаются раздельно.
# Заданы — ссылки идут через tp.media, и в кабинете видны клики. Пусто —
# прямые ссылки на aviasales с маркером (покупки считаются, клики нет).
TP_TRS_CHANNEL = _i("TP_TRS_CHANNEL", 0)     # (TP_TRS_CHANNEL)
TP_TRS_BOT     = _i("TP_TRS_BOT", 0)         # (TP_TRS_BOT)

# Amadeus Self-Service (developers.amadeus.com).
# Внутренние рейсы РФ и российских перевозчиков почти не отдаёт — по умолчанию выкл.
AMADEUS_ENABLED = _b("AMADEUS_ENABLED", False)
AMADEUS_KEY     = _s("AMADEUS_KEY", "")
AMADEUS_SECRET  = _s("AMADEUS_SECRET", "")
AMADEUS_HOST    = _s("AMADEUS_HOST", "test.api.amadeus.com")   # боевой: api.amadeus.com
EUR_RUB         = float(_s("AMADEUS_EUR_RUB", "105"))

# Страницы акций авиакомпаний.
# Ограничение, которое надо знать: сайты S7, Азимута и большинства остальных —
# это SPA, цены подставляет JavaScript, в HTML их нет. Без headless-браузера
# оттуда ничего не достать, поэтому источник выключен по умолчанию и оставлены
# только страницы, которые реально отдают цифры в разметке.
# Если включаешь — проверь свою ссылку: python airlines.py https://...
AIRLINES_ENABLED = _b("AVIA_AIRLINES", False)
AIRLINE_PAGES = [
    ("Победа",             "https://www.flypobeda.ru/"),
    ("Уральские авиалинии", "https://www.uralairlines.ru/"),
    ("Red Wings",          "https://flyredwings.com/"),
]
# Рабочая альтернатива этому источнику — официальные каналы авиакомпаний
# в Telegram, они ниже в TG_CHANNELS. Акции туда попадают тем же днём.

# Telegram-каналы про горящие билеты. Бот читает их твоим аккаунтом (Telethon)
# и пересылает посты, где есть Казань и цена в коридоре.
# Хэндлы без @, в переменной окружения — через запятую.        (AVIA_TG_CHANNELS)
TG_WATCH_ENABLED = _b("AVIA_TG_WATCH", True)
TG_CHANNELS = _list("AVIA_TG_CHANNELS", [
    # каналы-агрегаторы горящих билетов — вписывай свои, проверив, что живые:
    # "travelfree",
    # официальные каналы перевозчиков, оттуда идут анонсы распродаж:
    # "pobeda_airlines",
    # "s7airlines",
])
TG_SESSION = _s("AVIA_TG_SESSION", "avia_session")   # путь к файлу сессии без .session
API_ID     = _i("TG_API_ID", 0)        # ключи с my.telegram.org
API_HASH   = _s("TG_API_HASH", "")

# ---------- 4. Прокси ----------
# Провайдер режет Telegram — значит нужен. Для бота и для источников он разный:
# api.telegram.org обычно недоступен, а travelpayouts открыт напрямую.
#
# ВАЖНО про Docker: внутри контейнера 127.0.0.1 — это сам контейнер, а не твой
# v2rayN на компьютере. В docker-compose.yml подставлено host.docker.internal.
BOT_PROXY = _proxy("AVIA_BOT_PROXY", "socks5://127.0.0.1:10808")   # None = напрямую
API_PROXY = _proxy("AVIA_API_PROXY", None)      # для travelpayouts/amadeus/сайтов
TG_PROXY  = _proxy("AVIA_TG_PROXY", ("socks5", "127.0.0.1", 10808))  # для читалки каналов
#   форматы переменной окружения:
#     пусто / none                          — напрямую
#     socks5://127.0.0.1:10808              — SOCKS5
#     mtproxy://1.2.3.4:443:ee00ff..        — MTProxy
if isinstance(TG_PROXY, str):                   # строку из окружения -> кортеж Telethon
    from urllib.parse import urlparse as _u
    _p = _u(TG_PROXY)
    TG_PROXY = (_p.scheme or "socks5", _p.hostname, _p.port or 1080)

# ---------- 5. Темп и защита от спама ----------
POLL_EVERY_MIN     = _i("AVIA_POLL_MIN", 45)    # период опроса источников, минуты
MAX_ALERTS_PER_RUN = _i("AVIA_MAX_ALERTS", 25)  # на город вылета за проход; человеку — USER_ALERTS_PER_RUN
RESEND_DROP_PCT    = _i("AVIA_RESEND_DROP", 15) # повтор — только если упало на столько %
SEEN_TTL_DAYS      = _i("AVIA_SEEN_TTL", 7)     # через сколько дней «уже присылали» забывается

# Слежка за своими направлениями («Киров → Москва»): проверяется каждый опрос,
# один запрос на направление. Порог ниже общего — человек сам попросил.
WATCH_MAX         = _i("AVIA_WATCH_MAX", 10)       # направлений на человека
WATCH_PCT         = _i("AVIA_WATCH_PCT", 20)       # скидка к обычной цене маршрута, от
WATCH_DROP_PCT    = _i("AVIA_WATCH_DROP", 10)      # дешевле прошлого уведомления, от
WATCH_REPEAT_DAYS = _i("AVIA_WATCH_REPEAT", 7)     # скидку без нового падения — не чаще

# ---------- 6. Второй повод написать: аномально дёшево ----------
# Считается по собственной истории наблюдений, независимо от коридора цен.
ANOMALY_ENABLED     = _b("AVIA_ANOMALY", True)
ANOMALY_PCTL        = _i("AVIA_ANOMALY_PCTL", 25)     # «обычная цена» — этот перцентиль по другим датам
ANOMALY_MIN_POINTS  = _i("AVIA_ANOMALY_MIN", 10)      # меньше дат вылета в истории — не судим
ANOMALY_MIN_DAYS    = _i("AVIA_ANOMALY_DAYS_N", 3)    # и минимум столько разных дней
ANOMALY_MIN_DROP    = _i("AVIA_ANOMALY_DROP", 25)     # насколько % ниже медианы, иначе это не новость
ANOMALY_WINDOW_DAYS = _i("AVIA_ANOMALY_DAYS", 90)     # глубина истории
ANOMALY_HARD_CAP    = _i("AVIA_ANOMALY_CAP", 60000)   # дороже — не интересно даже со скидкой

# ---------- 7. Третий повод написать: выгодно для такого расстояния ----------
# Сравнивает предложение не с его же историей, а с кривой «цена от расстояния»,
# построенной по всем направлениям. Ловит то, что в коридор не попадает:
# билет за 15 000 туда, куда обычно летают за 30 000.
VALUE_ENABLED        = _b("AVIA_VALUE", True)
VALUE_RATIO          = float(_s("AVIA_VALUE_RATIO", "0.55"))  # не дороже 55% от нормы
VALUE_PRICE_CAP      = _i("AVIA_VALUE_CAP", 40000)    # дороже — не интересно даже выгодное
VALUE_MIN_DIRECTIONS = _i("AVIA_VALUE_MIN_DIRS", 25)  # меньше направлений — кривую не строим
VALUE_MIN_KM         = _i("AVIA_VALUE_MIN_KM", 200)   # ближние подскоки искажают кривую
VALUE_WINDOW_DAYS    = _i("AVIA_VALUE_DAYS", 30)      # глубина данных для подгонки
VALUE_FIT_TTL_MIN    = _i("AVIA_VALUE_TTL", 60)       # как часто пересчитывать кривую

# ---------- 8. Ещё три повода написать ----------

# «упало за сутки»: сравниваем с тем, что было вчера, а не с медианой за месяцы.
# Ловит начало распродажи за часы, а не за дни.
DROP_ENABLED   = _b("AVIA_DROP", True)
DROP_PCT       = _i("AVIA_DROP_PCT", 25)      # на сколько % должно упасть
DROP_FROM_H    = _i("AVIA_DROP_FROM_H", 48)   # окно «вчера»: от стольких часов назад
DROP_TO_H      = _i("AVIA_DROP_TO_H", 6)      # и до стольких

# «выброс среди дат»: на одно число резко дешевле, чем на соседние.
# Обычно это ошибочный тариф или пустой рейс — самый жирный сигнал.
OUTLIER_ENABLED    = _b("AVIA_OUTLIER", True)
OUTLIER_PCT        = _i("AVIA_OUTLIER_PCT", 35)   # насколько ниже соседних дат
OUTLIER_WINDOW     = _i("AVIA_OUTLIER_WINDOW", 4) # ± столько дней считаем соседями
OUTLIER_MIN_NEIGH  = _i("AVIA_OUTLIER_MIN", 3)    # меньше соседей — не судим

# сезонность: декабрьский билет сравниваем с историей декабря, а не с общей
# медианой, иначе новогодние цены вечно выглядят завышенными.
SEASON_ENABLED    = _b("AVIA_SEASON", True)
SEASON_MIN_POINTS = _i("AVIA_SEASON_MIN", 8)      # дат в этом месяце, иначе общая медиана

# «дешевле всех городов страны» — пометка в сообщении, не отдельный повод:
# самый дешёвый город есть у каждой страны, как признак это был бы шум.
COUNTRY_ENABLED    = _b("AVIA_COUNTRY", True)
COUNTRY_MIN_CITIES = _i("AVIA_COUNTRY_MIN", 3)    # меньше городов — сравнивать не с чем

# ---------- 9. Связки: два отдельных билета через третий город ----------
# Иногда A→B и B→C двумя билетами дешевле прямого A→C.
# Поиск точечный: календари по конкретным парам, потому что широкие эндпоинты
# отдают разрозненные даты и круговые тарифы. Подробности в combo.py.
COMBO_ENABLED      = _b("AVIA_COMBO", True)
COMBO_HUBS         = _list("AVIA_COMBO_HUBS", ["MOW", "LED", "SVX", "OVB", "IST", "AER"])
COMBO_SCAN_DESTS   = _i("AVIA_COMBO_DESTS", 8)       # сколько направлений проверять за раз
COMBO_SCAN_HUBS    = _i("AVIA_COMBO_HUBS_N", 3)      # через сколько хабов пробовать
COMBO_PICK_HUBS    = _i("AVIA_COMBO_HUBS_PICK", 6)   # когда город назван вручную — можно больше
COMBO_SCAN_MONTHS  = _i("AVIA_COMBO_MONTHS", 2)      # на сколько месяцев вперёд
COMBO_EVERY_MIN    = _i("AVIA_COMBO_EVERY", 180)     # как часто искать: это ~минута запросов
COMBO_MIN_SAVE_PCT = _i("AVIA_COMBO_SAVE_PCT", 15)   # насколько дешевле прямого
COMBO_MIN_SAVE_RUB = _i("AVIA_COMBO_SAVE_RUB", 1500) # и не меньше стольких рублей
COMBO_DATE_TOL     = _i("AVIA_COMBO_DATE_TOL", 3)    # с чем сравнивать: прямой ±дней
COMBO_PRICE_CAP    = _i("AVIA_COMBO_CAP", 40000)     # дороже — не предлагаем, даже с экономией

# Пересадка на двух отдельных билетах — это риск: опоздал на второй рейс, и его
# никто не обязан менять. Поэтому запас времени больше, чем у обычной стыковки.
COMBO_MIN_LAYOVER_H = _i("AVIA_COMBO_MIN_LAYOVER", 4)      # тот же аэропорт
COMBO_DIFF_AIRPORT_H = _i("AVIA_COMBO_DIFF_AIRPORT", 7)    # если аэропорты разные
COMBO_MAX_LAYOVER_H = _i("AVIA_COMBO_MAX_LAYOVER", 24)     # дольше — это уже не стыковка

# ---------- 9а. Обход направлений ----------
# Обычные запросы отдают по городу фиксированный кусок выдачи: одну цену на
# день и ~30 популярных направлений. Обход берёт по каждому направлению
# календарь на год одним запросом — так видны скидки по всей карте.
# Список направлений: прямые маршруты из справочника Travelpayouts плюс всё,
# что уже встречалось в истории города.
SWEEP_ENABLED   = _b("AVIA_SWEEP", True)
SWEEP_EVERY_MIN = _i("AVIA_SWEEP_EVERY", 90)      # как часто проходить полный круг
SWEEP_DELAY     = float(_s("AVIA_SWEEP_DELAY", "0.4"))  # пауза между запросами, сек
# Лимит API — 600 запросов в минуту (заголовок X-Rate-Limit). Бот читает
# остаток из каждого ответа и сам притормаживает, когда он кончается.
API_RESERVE     = _i("AVIA_API_RESERVE", 50)      # столько запросов держим в запасе

# ---------- 10. Окно дат вылета ----------
# Пусто = без ограничения. У каждого подписчика может быть своё, кнопкой.
DATE_FROM = _s("AVIA_DATE_FROM", "")
DATE_TO   = _s("AVIA_DATE_TO", "")

# ---------- 11. Запрос конкретного маршрута ----------
# «18 сентября Киров → Питер, 20-го обратно»: сколько стоит и нельзя ли дешевле.
TRIP_FLEX_DAYS = _i("AVIA_TRIP_FLEX", 3)    # на сколько дней смотреть вокруг
TRIP_ALT_LIMIT = _i("AVIA_TRIP_ALTS", 5)    # сколько вариантов показывать
TRIP_LEN_TOL   = _i("AVIA_TRIP_LEN_TOL", 1) # на сколько ночей вариант может отличаться

# ---------- 12. Оформление ----------
# "new" — rich-сообщения Bot API: заголовки, линии, кнопки внутри сообщения.
# "old" — обычные сообщения, кнопки под ними. У каждого подписчика свой выбор,
# это лишь значение по умолчанию для новых.
STYLE = _s("AVIA_STYLE", "new")

# ---------- 13. Канал ----------
# Бот находит скидки из городов канала, рисует карточку и присылает черновик
# владельцам (AVIA_OWNER_IDS) с кнопками «Опубликовать / Пропустить».
# Пустой AVIA_CHANNEL = канала нет, города не опрашиваются.
CHANNEL_ID      = _i("AVIA_CHANNEL", 0)                       # -100...
CHANNEL_ORIGINS = _list("AVIA_CHANNEL_ORIGINS", ["MOW", "LED"])
CHANNEL_DEAL_PCT = _i("AVIA_CHANNEL_DEAL", 40)     # скидка к обычной цене, от
CHANNEL_DROP_PCT = _i("AVIA_CHANNEL_DROP", 25)     # падение той же даты за сутки, от
CHANNEL_REPOST_PCT  = _i("AVIA_CHANNEL_REPOST", 15)   # повтор — если дешевле прошлого на столько
CHANNEL_REPOST_DAYS = _i("AVIA_CHANNEL_REPOST_DAYS", 7)  # или прошло столько дней
CHANNEL_MIN_DAYS = _i("AVIA_CHANNEL_MIN_DAYS", 2)   # вылет не раньше, чем через столько дней
CHANNEL_RECHECK_TOL = _i("AVIA_CHANNEL_TOL", 3)    # на сколько % может подорожать к публикации
# Опубликованные посты бот сопровождает: цена поменялась — перерисовывает,
# ушла — помечает «не актуально». Каждый проход, пока рейс не улетел.
CHANNEL_TRACK_DAYS = _i("AVIA_CHANNEL_TRACK_DAYS", 7)  # сколько дней следить за постом
CHANNEL_EDIT_MIN_PCT = _i("AVIA_CHANNEL_EDIT_MIN", 2)  # мельче — не дёргаем пост
CHANNEL_STALE_PCT = _i("AVIA_CHANNEL_STALE", 20)       # скидка ниже — «не актуально»
# Автопубликация: находки встают в очередь и выходят по одной, лучшие первыми.
# 19 постов за минуту — верный способ заставить подписчиков выключить звук.
CHANNEL_AUTO = _b("AVIA_CHANNEL_AUTO", True)            # False = черновики владельцу
# Новый вид постов (rich): картинка, заголовок и кнопки внутри поста. Хэштеги
# в нём не нажимаются — только долгим нажатием. False = картинка с подписью.
CHANNEL_RICH = _b("AVIA_CHANNEL_RICH", True)
CHANNEL_GAP_MIN = _i("AVIA_CHANNEL_GAP", 10)            # минут между постами, не чаще
CHANNEL_GAP_MAX = _i("AVIA_CHANNEL_GAP_MAX", 60)        # и не реже, если есть что публиковать
CHANNEL_DAY_MAX = _i("AVIA_CHANNEL_DAY_MAX", 20)        # постов за сутки, не больше
CHANNEL_NIGHT_PCT = _i("AVIA_CHANNEL_NIGHT_PCT", 50)    # ночью — только скидки от стольких %
CHANNEL_QUEUE_HOURS = _i("AVIA_CHANNEL_QUEUE_HOURS", 6) # дольше в очереди — устарело
CHANNEL_QUIET = tuple(int(x) for x in _s("AVIA_CHANNEL_QUIET", "23-8").split("-"))  # без звука
# Обратный билет в посте: самый дешёвый через столько-то дней после вылета.
CHANNEL_BACK_MIN = _i("AVIA_CHANNEL_BACK_MIN", 2)
CHANNEL_BACK_MAX = _i("AVIA_CHANNEL_BACK_MAX", 14)
CHANNEL_CHEAP_TAG = _i("AVIA_CHANNEL_CHEAP_TAG", 5000)   # тег #до5000
# Дайджест «лучшее за неделю»: день недели (0 = пн, 6 = вс) и час.
CHANNEL_DIGEST_DAY  = _i("AVIA_CHANNEL_DIGEST_DAY", 6)
CHANNEL_DIGEST_HOUR = _i("AVIA_CHANNEL_DIGEST_HOUR", 12)
# «Куда на выходные»: в четверг вечером — поездки на ближайшие пт–вс/пн
CHANNEL_WEEKEND_DAY   = _i("AVIA_CHANNEL_WEEKEND_DAY", 3)     # 0 = пн … 3 = чт
CHANNEL_WEEKEND_HOUR  = _i("AVIA_CHANNEL_WEEKEND_HOUR", 18)
CHANNEL_WEEKEND_CHECK = _i("AVIA_CHANNEL_WEEKEND_CHECK", 12)  # направлений проверить обратно, на город
# Маркировка рекламы в конце каждого поста — текст из кабинета Travelpayouts
# («Отказ от ответственности в рекламе»). Пусто = без пометки.
AD_LABEL = _s("AVIA_AD_LABEL", "")
AD_LABEL_ERID = _b("AVIA_AD_LABEL_ERID", True)   # дописывать erid из партнёрской ссылки
CHANNEL_SIGN = _s("AVIA_CHANNEL_SIGN", "@AviaChecker_bot")    # подпись в углу карточки
BOT_USERNAME = _s("AVIA_BOT_USERNAME", "AviaChecker_bot")     # для кнопки «Свои уведомления»
# Картинки карточек для бота в чатах: адрес снаружи (http://IP:порт) и порт в контейнере.
# Пусто — в чатах бот отвечает текстом.
WEB_URL  = _s("AVIA_WEB_URL", "")
WEB_PORT = _i("AVIA_WEB_PORT", 8090)

# ---------- 13а. Обслуживание ----------
BACKUP_EVERY_H = _i("AVIA_BACKUP_EVERY", 24)   # как часто копировать базу, часов
BACKUP_KEEP    = _i("AVIA_BACKUP_KEEP", 3)     # сколько копий хранить
# Сигналы владельцу о поломках: одна и та же проблема — не чаще раза в N часов.
PROBLEM_REPEAT_H = _i("AVIA_PROBLEM_REPEAT", 3)

# ---------- 14. Где лежат данные ----------
# В Docker это /data (том), на своей машине — рядом с кодом.
DB     = _s("AVIA_DB", "avia.db")
PLACES = _s("AVIA_PLACES", "places.json")
ROUTES = _s("AVIA_ROUTES", "routes.json")     # прямые маршруты, для обхода
