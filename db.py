"""
База: история цен, дедуп оповещений, подписчики.

Важное про origin: город вылета теперь у каждого свой, поэтому во всех запросах
статистики он обязателен. Смешать историю из Казани с историей из Москвы —
значит получить бессмысленную медиану и ложные аномалии.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(          -- все увиденные цены, сырьё для статистики
  id       INTEGER PRIMARY KEY,
  origin   TEXT,
  dest     TEXT,
  depart   TEXT,      -- дата вылета YYYY-MM-DD
  ret      TEXT,      -- дата обратно, NULL для one-way
  price    INTEGER,
  airline  TEXT,
  flight   TEXT,
  transfers INTEGER,
  link     TEXT,
  src      TEXT,      -- откуда узнали: tp / amadeus / airline / tg
  found_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_dir  ON prices(origin, dest, found_at);
CREATE INDEX IF NOT EXISTS ix_when ON prices(found_at);
CREATE INDEX IF NOT EXISTS ix_dep  ON prices(origin, dest, depart);

CREATE TABLE IF NOT EXISTS seen(            -- о чём уже писали, чтобы не спамить
  key     TEXT PRIMARY KEY,
  price   INTEGER,
  sent_at TEXT
);

CREATE TABLE IF NOT EXISTS subs(            -- подписчики бота
  chat_id    INTEGER PRIMARY KEY,
  active     INTEGER DEFAULT 1,
  pmin       INTEGER,
  pmax       INTEGER,
  title      TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS pages(           -- состояние страниц акций авиакомпаний
  url        TEXT PRIMARY KEY,
  hash       TEXT,
  prices     TEXT,
  checked_at TEXT
);

CREATE TABLE IF NOT EXISTS combos(          -- найденные связки, для кнопки в боте
  origin       TEXT,
  dest         TEXT,
  via          TEXT,
  price        INTEGER,
  direct_price INTEGER,
  saving       INTEGER,
  depart       TEXT,
  payload      TEXT,      -- всё предложение целиком, чтобы отрисовать как есть
  found_at     TEXT,
  PRIMARY KEY(origin, dest)
);

CREATE TABLE IF NOT EXISTS feed(            -- скидки последнего прохода, для кнопки 🔥
  origin   TEXT,
  dest     TEXT,
  discount INTEGER,
  price    INTEGER,
  depart   TEXT,
  payload  TEXT,      -- предложение целиком, чтобы показать карточку как есть
  found_at TEXT,
  PRIMARY KEY(origin, dest)
);

CREATE TABLE IF NOT EXISTS posts(           -- черновики и публикации в канал
  id           INTEGER PRIMARY KEY,
  key          TEXT,      -- откуда-куда: по нему правило повторов
  kind         TEXT,      -- super / deal / drop
  pct          INTEGER,
  price        INTEGER,
  payload      TEXT,      -- предложение целиком, чтобы перепроверить и перерисовать
  status       TEXT,      -- draft / published / skipped / stale
  created_at   TEXT,
  published_at TEXT,
  message_id   INTEGER
);
CREATE INDEX IF NOT EXISTS ix_posts_key ON posts(key, created_at);

CREATE TABLE IF NOT EXISTS watches(         -- свои направления: «Киров → Москва»
  id           INTEGER PRIMARY KEY,
  chat_id      INTEGER,
  origin       TEXT,
  dest         TEXT,
  created_at   TEXT,
  last_price   INTEGER,   -- цена, которую человек уже знает: от неё меряем падение
  last_sent_at TEXT,      -- когда последний раз писали про это направление
  cur_price    INTEGER,   -- лучшая цена последней проверки — для списка
  cur_depart   TEXT,
  checked_at   TEXT,
  UNIQUE(chat_id, origin, dest)
);

CREATE TABLE IF NOT EXISTS links(           -- ссылки-отслеживания: t.me/бот?start=slug
  id         INTEGER PRIMARY KEY,
  slug       TEXT UNIQUE,
  name       TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS visits(          -- каждый /start с меткой, даже без ссылки в списке
  id      INTEGER PRIMARY KEY,
  slug    TEXT,
  chat_id INTEGER,
  is_new  INTEGER,   -- 1 = человек пришёл в бота впервые
  at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_visits ON visits(slug, at);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

# колонки, появившиеся позже базы: дотягиваются молча, данные не теряются
MIGRATIONS = {
    "prices": {
        "depart_at": "TEXT",     # полное время вылета с часовым поясом
        "duration":  "INTEGER",  # в пути, минут
    },
    "subs": {
        "origin":    "TEXT",     # свой город вылета, NULL = из конфига
        "date_from": "TEXT",     # окно дат вылета, NULL = без ограничения
        "date_to":   "TEXT",
        "style":     "TEXT",     # оформление: old / new, NULL = из конфига
        "mode":      "TEXT",     # что присылать: super / deals / budget
    },
    "posts": {
        "fmt": "TEXT",           # rich = новый вид, NULL/photo = картинка с подписью
    },
}

_lock = threading.Lock()
_db = None


def connect():
    """Одно соединение на процесс, запись под замком: пишут и бот, и сборщик."""
    global _db
    if _db is None:
        _db = sqlite3.connect(C.DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in _db.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    _db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    print(f"  база: добавлена колонка {table}.{col}")
        _db.commit()
    return _db


def now():
    return datetime.now().isoformat(timespec="seconds")


def _ago(days=0, hours=0):
    return (datetime.now() - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


# ---------- история цен ----------

def save_prices(deals):
    """Пишет пачку предложений в историю. Возвращает сколько записал."""
    db = connect()
    rows = [(d["origin"], d["dest"], d.get("depart"), d.get("ret"), int(d["price"]),
             d.get("airline"), d.get("flight"), d.get("transfers"),
             d.get("link"), d.get("src"), now(),
             d.get("depart_at"), d.get("duration")) for d in deals if d.get("price")]
    if not rows:
        return 0
    with _lock:
        db.executemany(
            "INSERT INTO prices(origin,dest,depart,ret,price,airline,flight,"
            "transfers,link,src,found_at,depart_at,duration) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
    return len(rows)


def history(origin, dest, days, month=None, exclude=None, direct=False):
    """
    Самая дешёвая цена на каждую дату вылета за последние days дней.

    Именно минимумы по датам, а не все цены подряд. В кэше на одну дату лежат
    и прямой за 5 000, и три пересадки за 40 000; медиана всего этого —
    не «обычная цена», а середина случайной выдачи.

    month   — только вылеты в этом месяце ('12'), для сезонности;
    exclude — без этой даты: сравниваем с ДРУГИМИ днями, иначе находка
              сама тянет «обычную цену» вниз и прячет свою же скидку;
    direct  — только прямые. У многих направлений прямой рейс летает по
              определённым дням за 10 000, а в остальные дни с пересадками
              за 40 000. Сравнивать прямой с пересадочными — рисовать −75%
              на обычном расписании.
    """
    sql = ("SELECT MIN(price) p FROM prices WHERE origin=? AND dest=? "
           "AND found_at>=? AND depart IS NOT NULL")
    args = [origin, dest, _ago(days=days)]
    if month:
        sql += " AND substr(depart,6,2)=?"
        args.append(month)
    if exclude:
        sql += " AND depart<>?"
        args.append(exclude)
    if direct:
        sql += " AND transfers=0"
    sql += " GROUP BY depart ORDER BY p"
    return [r[0] for r in connect().execute(sql, args)]


def history_days(origin, dest, days):
    """За сколько РАЗНЫХ дней набрана история.

    Нужно, чтобы один прогон не создавал видимость статистики: 300 цен,
    собранных за минуту, сравнивать не с чем.
    """
    cur = connect().execute(
        "SELECT COUNT(DISTINCT substr(found_at,1,10)) FROM prices "
        "WHERE origin=? AND dest=? AND found_at>=?", (origin, dest, _ago(days=days)))
    return cur.fetchone()[0]


def min_price_between(origin, dest, hours_from, hours_to, depart=None):
    """
    Минимальная цена в окне «от hours_from до hours_to часов назад».

    Для признака «упало за сутки». depart — та же дата вылета: сравнивать
    минимум направления сегодня с минимумом вчера нельзя, это разные даты.
    Вчера в кэше просто могло не оказаться дешёвых дней, и выйдет «−76%»
    на ровном месте — так и было на живых данных.
    """
    sql = ("SELECT MIN(price) FROM prices WHERE origin=? AND dest=? "
           "AND found_at>=? AND found_at<?")
    args = [origin, dest, _ago(hours=hours_from), _ago(hours=hours_to)]
    if depart:
        sql += " AND depart=?"
        args.append(depart)
    return connect().execute(sql, args).fetchone()[0]


def min_by_direction(origin, days):
    """[(направление, минимальная цена), ...] — сырьё для кривой цена/расстояние."""
    return connect().execute(
        "SELECT dest, MIN(price) FROM prices WHERE origin=? AND found_at>=? "
        "GROUP BY dest", (origin, _ago(days=days))).fetchall()


def top_cheap(origin, hours=24, limit=15, pmax=None, date_from=None, date_to=None):
    """Минимальная цена по каждому направлению за последние hours часов."""
    sql = ("SELECT dest, MIN(price) p, depart, airline, link FROM prices "
           "WHERE origin=? AND found_at>=?")
    args = [origin, _ago(hours=hours)]
    if date_from:
        sql += " AND depart>=?"
        args.append(date_from)
    if date_to:
        sql += " AND depart<=?"
        args.append(date_to)
    sql += " GROUP BY dest"
    if pmax:
        sql += " HAVING p<=?"
        args.append(pmax)
    sql += " ORDER BY p LIMIT ?"
    args.append(limit)
    return connect().execute(sql, args).fetchall()


def count_prices():
    return connect().execute("SELECT COUNT(*) FROM prices").fetchone()[0]


def destinations(origin, days=60):
    """Все направления, что встречались у города за days дней."""
    return [r[0] for r in connect().execute(
        "SELECT DISTINCT dest FROM prices WHERE origin=? AND found_at>=?",
        (origin, _ago(days=days)))]


def save_daily(deals):
    """
    Календари обхода — не больше одной записи в день на дату вылета.

    Обход приносит сотни тысяч цен за круг, и почти все повторяют вчерашние.
    Для статистики нужен дневной минимум по каждой дате, а не каждый замер;
    ссылку не храним — она нужна только живой находке, а та в ленте.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    db = connect()
    have = {}
    for origin in {d["origin"] for d in deals}:
        for dest, depart, p in db.execute(
                "SELECT dest, depart, MIN(price) FROM prices WHERE origin=? "
                "AND found_at>=? AND src='tp/sweep' GROUP BY dest, depart",
                (origin, today)):
            have[(origin, dest, depart)] = p
    rows = []
    for d in deals:
        k = (d["origin"], d["dest"], d.get("depart"))
        if k in have and have[k] <= d["price"]:
            continue
        have[k] = d["price"]
        rows.append((d["origin"], d["dest"], d.get("depart"), None, int(d["price"]),
                     d.get("airline"), d.get("flight"), d.get("transfers"), None,
                     "tp/sweep", now(), d.get("depart_at"), d.get("duration")))
    if rows:
        with _lock:
            db.executemany(
                "INSERT INTO prices(origin,dest,depart,ret,price,airline,flight,"
                "transfers,link,src,found_at,depart_at,duration) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
    return len(rows)


def prune(days=100):
    """
    Чистит историю глубже days дней, чтобы база не пухла.
    Статистика смотрит на 90 дней назад, дальше хранить незачем.
    """
    db = connect()
    with _lock:
        db.execute("DELETE FROM prices WHERE found_at<?", (_ago(days=days),))
        db.commit()


# ---------- дедуп оповещений ----------

def was_sent(key):
    r = connect().execute("SELECT price FROM seen WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def mark_sent(key, price):
    db = connect()
    with _lock:
        db.execute("INSERT OR REPLACE INTO seen(key,price,sent_at) VALUES(?,?,?)",
                   (key, int(price), now()))
        db.commit()


def forget_old():
    db = connect()
    with _lock:
        db.execute("DELETE FROM seen WHERE sent_at<?", (_ago(days=C.SEEN_TTL_DAYS),))
        db.commit()


# ---------- подписчики ----------

def add_sub(chat_id, title=""):
    """
    Новый подписчик или возвращение старого.

    Новым бюджет не ставим: главное теперь скидка, а не порог в рублях.
    Режим пишем явно — у старых записей он пустой и выводится из их коридора.
    """
    db = connect()
    with _lock:
        db.execute(
            "INSERT INTO subs(chat_id,active,pmin,pmax,title,created_at,mode) "
            "VALUES(?,1,0,NULL,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET active=1",
            (chat_id, title, now(), C.MODE))
        db.commit()


def stop_sub(chat_id):
    db = connect()
    with _lock:
        db.execute("UPDATE subs SET active=0 WHERE chat_id=?", (chat_id,))
        db.commit()


def _set(chat_id, **fields):
    db = connect()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        db.execute(f"UPDATE subs SET {cols} WHERE chat_id=?",
                   (*fields.values(), chat_id))
        db.commit()


def set_range(chat_id, pmin, pmax):
    _set(chat_id, pmin=pmin, pmax=pmax)


def set_origin(chat_id, origin):
    _set(chat_id, origin=origin)


def set_dates(chat_id, date_from, date_to):
    _set(chat_id, date_from=date_from, date_to=date_to)


def set_style(chat_id, style):
    _set(chat_id, style=style)


def set_mode(chat_id, mode):
    _set(chat_id, mode=mode)


def has_recent(origin, hours):
    """Есть ли свежие цены по городу — чтобы не опрашивать его второй раз."""
    r = connect().execute("SELECT 1 FROM prices WHERE origin=? AND found_at>=? LIMIT 1",
                          (origin, _ago(hours=hours))).fetchone()
    return bool(r)


def get_sub(chat_id):
    return connect().execute("SELECT * FROM subs WHERE chat_id=?", (chat_id,)).fetchone()


def active_subs():
    return connect().execute("SELECT * FROM subs WHERE active=1").fetchall()


def origins():
    """
    Какие города вылета нужно опрашивать: все, что выбрали подписчики,
    плюс город из конфига как значение по умолчанию.
    """
    out = {C.ORIGIN}
    if channel_id():                    # города канала опрашиваются всегда
        out.update(C.CHANNEL_ORIGINS)
    try:
        for s in active_subs():
            if s["origin"]:
                out.add(s["origin"])
    except Exception:
        pass
    return sorted(out)


# ---------- свои направления ----------

def add_watch(chat_id, origin, dest, price=None, depart=None):
    """Следить за направлением. Уже следит — ничего не меняем, отдаём id."""
    db = connect()
    with _lock:
        db.execute(
            "INSERT OR IGNORE INTO watches(chat_id,origin,dest,created_at,last_price,"
            "last_sent_at,cur_price,cur_depart,checked_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (chat_id, origin, dest, now(), price, now(), price, depart,
             now() if price else None))
        db.commit()
    return get_watch(chat_id, origin, dest)["id"]


def get_watch(chat_id, origin, dest):
    return connect().execute(
        "SELECT * FROM watches WHERE chat_id=? AND origin=? AND dest=?",
        (chat_id, origin, dest)).fetchone()


def watch_by_id(watch_id):
    return connect().execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()


def del_watch(chat_id, watch_id):
    db = connect()
    with _lock:
        n = db.execute("DELETE FROM watches WHERE id=? AND chat_id=?",
                       (watch_id, chat_id)).rowcount
        db.commit()
    return n


def watches_of(chat_id):
    return connect().execute("SELECT * FROM watches WHERE chat_id=? ORDER BY id",
                             (chat_id,)).fetchall()


def all_watches():
    """Все направления тех, кто не отписался от бота."""
    return connect().execute(
        "SELECT w.* FROM watches w JOIN subs s ON s.chat_id=w.chat_id "
        "WHERE s.active=1 ORDER BY w.origin, w.dest").fetchall()


def set_watch(watch_id, **fields):
    db = connect()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        db.execute(f"UPDATE watches SET {cols} WHERE id=?", (*fields.values(), watch_id))
        db.commit()


# ---------- ссылки-отслеживания ----------

def add_link(slug, name):
    db = connect()
    with _lock:
        cur = db.execute("INSERT INTO links(slug,name,created_at) VALUES(?,?,?)",
                         (slug, name, now()))
        db.commit()
    return cur.lastrowid


def get_link(link_id):
    return connect().execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()


def links():
    return connect().execute("SELECT * FROM links ORDER BY id").fetchall()


def slug_taken(slug):
    return bool(connect().execute("SELECT 1 FROM links WHERE slug=?", (slug,)).fetchone())


def add_visit(slug, chat_id, is_new):
    db = connect()
    with _lock:
        db.execute("INSERT INTO visits(slug,chat_id,is_new,at) VALUES(?,?,?,?)",
                   (slug, chat_id, int(bool(is_new)), now()))
        db.commit()


def link_stats(slug, since):
    """(переходов, новых людей, из новых остались) с момента since."""
    db = connect()
    visits, new = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT CASE WHEN is_new=1 THEN chat_id END) "
        "FROM visits WHERE slug=? AND at>=?", (slug, since)).fetchone()
    stayed = db.execute(
        "SELECT COUNT(DISTINCT v.chat_id) FROM visits v JOIN subs s ON s.chat_id=v.chat_id "
        "WHERE v.slug=? AND v.at>=? AND v.is_new=1 AND s.active=1",
        (slug, since)).fetchone()[0]
    return visits, new, stayed


def link_daily(slug, since):
    """{день 'YYYY-MM-DD': (переходов, новых)} с момента since."""
    return {r[0]: (r[1], r[2]) for r in connect().execute(
        "SELECT substr(at,1,10), COUNT(*), COUNT(DISTINCT CASE WHEN is_new=1 THEN chat_id END) "
        "FROM visits WHERE slug=? AND at>=? GROUP BY 1", (slug, since))}


# ---------- связки ----------

def save_combos(origin, combos):
    """Перезаписывает связки по городу: старые уже неактуальны."""
    import json
    db = connect()
    with _lock:
        db.execute("DELETE FROM combos WHERE origin=?", (origin,))
        db.executemany(
            "INSERT OR REPLACE INTO combos(origin,dest,via,price,direct_price,"
            "saving,depart,payload,found_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [(c["origin"], c["dest"], c["via"], c["price"], c["direct_price"],
              c["saving"], c["depart"], json.dumps(c, ensure_ascii=False), now())
             for c in combos])
        db.commit()


def load_combos(origin, limit=10):
    """Связки по городу, самые выгодные первыми."""
    import json
    rows = connect().execute(
        "SELECT payload, found_at FROM combos WHERE origin=? "
        "ORDER BY saving DESC LIMIT ?", (origin, limit)).fetchall()
    out = []
    for r in rows:
        try:
            c = json.loads(r[0])
            c["found_at"] = r[1]
            out.append(c)
        except Exception:
            pass
    return out


# ---------- лента скидок ----------

def save_feed(deals):
    """
    Перезаписывает ленту по тем городам, что были в этом проходе.

    Города, по которым источник сейчас не ответил, не трогаем: пусть лучше
    висят скидки часовой давности, чем пустой экран.
    """
    import json
    rows = [d for d in deals if d.get("discount") and d.get("src") != "combo"]
    origins = {d["origin"] for d in deals}
    if not origins:
        return
    db = connect()
    with _lock:
        db.executemany("DELETE FROM feed WHERE origin=?", [(o,) for o in origins])
        db.executemany(
            "INSERT OR REPLACE INTO feed(origin,dest,discount,price,depart,payload,"
            "found_at) VALUES(?,?,?,?,?,?,?)",
            [(d["origin"], d["dest"], d["discount"], d["price"], d.get("depart"),
              json.dumps(d, ensure_ascii=False), now()) for d in rows])
        db.commit()


def load_feed(origin, limit=100):
    """Скидки по городу, самые большие первыми."""
    import json
    out = []
    for r in connect().execute(
            "SELECT payload FROM feed WHERE origin=? ORDER BY discount DESC LIMIT ?",
            (origin, limit)):
        try:
            out.append(json.loads(r[0]))
        except Exception:
            pass
    return out


def get_deal(origin, dest):
    """Карточка из ленты: сперва обычная скидка, потом связка."""
    import json
    r = connect().execute("SELECT payload FROM feed WHERE origin=? AND dest=?",
                          (origin, dest)).fetchone()
    if not r:
        r = connect().execute("SELECT payload FROM combos WHERE origin=? AND dest=?",
                              (origin, dest)).fetchone()
    return json.loads(r[0]) if r else None


# ---------- канал ----------

def channel_id():
    """
    Куда публиковать. Бот запоминает канал сам, когда владелец делает его
    админом, — это надёжнее, чем id, переписанный руками из клиента.
    """
    v = meta_get("channel_id")
    return int(v) if v else C.CHANNEL_ID


def last_post(key):
    """
    Последний пост по направлению — для правила повторов.

    Устаревшие в очереди (stale) и снятые как неактуальные (expired) не в счёт:
    они в канал не дошли или уже не висят как предложение. Пропущенные
    и удалённые руками — в счёт: раз не понравилось, не надо предлагать снова.
    """
    return connect().execute(
        "SELECT * FROM posts WHERE key=? AND status NOT IN ('stale','expired') "
        "ORDER BY id DESC LIMIT 1", (key,)).fetchone()


def add_post(key, kind, pct, price, payload, status="draft"):
    import json
    db = connect()
    with _lock:
        cur = db.execute(
            "INSERT INTO posts(key,kind,pct,price,payload,status,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (key, kind, pct, price, json.dumps(payload, ensure_ascii=False), status, now()))
        db.commit()
        return cur.lastrowid


def get_post(post_id):
    return connect().execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def set_post(post_id, **fields):
    db = connect()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        db.execute(f"UPDATE posts SET {cols} WHERE id=?", (*fields.values(), post_id))
        db.commit()


def active_posts(days):
    """Опубликованные за последние days дней — за ними бот следит."""
    return connect().execute(
        "SELECT * FROM posts WHERE status='published' AND published_at>=? ORDER BY id",
        (_ago(days=days),)).fetchall()


def next_queued():
    """Следующий в очередь на публикацию: самая большая скидка, потом старший."""
    return connect().execute(
        "SELECT * FROM posts WHERE status='queued' ORDER BY pct DESC, id LIMIT 1").fetchone()


def expire_queue(hours):
    """Очередь не копится бесконечно: пролежавшее дольше hours — устарело."""
    db = connect()
    with _lock:
        n = db.execute("UPDATE posts SET status='stale' WHERE status='queued' AND created_at<?",
                       (_ago(hours=hours),)).rowcount
        db.commit()
    return n


def last_published_at():
    r = connect().execute("SELECT MAX(published_at) FROM posts WHERE published_at IS NOT NULL"
                          ).fetchone()
    return r[0]


def count_posts(status, days=1):
    return connect().execute(
        "SELECT COUNT(*) FROM posts WHERE status=? AND created_at>=?",
        (status, _ago(days=days))).fetchone()[0]


# ---------- страницы акций ----------

def page_state(url):
    return connect().execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()


def save_page(url, h, prices):
    db = connect()
    with _lock:
        db.execute("INSERT OR REPLACE INTO pages(url,hash,prices,checked_at) VALUES(?,?,?,?)",
                   (url, h, ",".join(str(p) for p in sorted(prices)), now()))
        db.commit()


# ---------- разное ----------

def meta_get(k, default=None):
    r = connect().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(k, v):
    db = connect()
    with _lock:
        db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
        db.commit()
