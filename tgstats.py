"""
Статистика канала через живой аккаунт Telegram (не бота).

Бот видит только то, что ему положено: свои сообщения и вступления. Просмотры
постов, пересылки, реакции, источники подписчиков и графики Telegram отдаёт
только аккаунту-админу канала. Поэтому для аналитики подключается отдельный
аккаунт (не основной владельца): он админ канала и больше ничего не делает.

Ключи (api_id, api_hash с my.telegram.org) и сессия лежат в отдельном файле
рядом с базой (stats_account.json, права 600), а не в базе: в бэкапы базы они
не попадают. Сессия — это полный доступ к аккаунту, поэтому аккаунт отдельный.

Вход — по шагам из админки бота (handlers/analytics.py): ключи → телефон →
код из Telegram (присылать через дефисы, иначе Telegram отменит вход) →
облачный пароль, если есть. Клиент живёт между шагами в памяти.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.stats import GetBroadcastStatsRequest, LoadAsyncGraphRequest
from telethon.tl.types import StatsGraph, StatsGraphAsync

import config as C
import db

PATH = os.path.join(os.path.dirname(os.path.abspath(C.DB)), "stats_account.json")
_pending = {}          # chat_id владельца -> (клиент, телефон, phone_code_hash) между шагами входа


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(PATH, 0o600)


def connected():
    a = _load()
    return bool(a.get("session") and a.get("api_id") and a.get("api_hash"))


def account_name():
    return _load().get("name")


def forget():
    """Отключить аккаунт: файл удаляется. В самом Telegram сессию лучше
    тоже завершить: Настройки → Устройства."""
    try:
        os.remove(PATH)
    except OSError:
        pass


def _client(session, api_id, api_hash):
    return TelegramClient(StringSession(session), int(api_id), api_hash,
                          connection_retries=3, timeout=20, request_retries=3)


# ---------- вход по шагам ----------

async def login_start(chat_id, api_id, api_hash, phone):
    """Шаг «телефон»: Telegram пришлёт код в приложение этого аккаунта."""
    await login_cancel(chat_id)
    cl = _client("", api_id, api_hash)
    await cl.connect()
    sent = await cl.send_code_request(phone)
    _pending[chat_id] = (cl, phone, sent.phone_code_hash, api_id, api_hash)


async def login_code(chat_id, code):
    """Шаг «код». Возвращает "ok" или "password" — нужен облачный пароль."""
    cl, phone, h, api_id, api_hash = _pending[chat_id]
    try:
        await cl.sign_in(phone=phone, code=code, phone_code_hash=h)
    except SessionPasswordNeededError:
        return "password"
    return await _finish(chat_id)


async def login_password(chat_id, password):
    cl = _pending[chat_id][0]
    await cl.sign_in(password=password)
    return await _finish(chat_id)


async def _finish(chat_id):
    cl, _, _, api_id, api_hash = _pending.pop(chat_id)
    me = await cl.get_me()
    name = f"{me.first_name or ''} (@{me.username})" if me.username else (me.first_name or str(me.id))
    _save({"api_id": int(api_id), "api_hash": api_hash, "session": cl.session.save(),
           "name": name.strip()})
    await cl.disconnect()
    return "ok"


async def login_cancel(chat_id):
    got = _pending.pop(chat_id, None)
    if got:
        try:
            await got[0].disconnect()
        except Exception:
            pass


# ---------- сбор ----------

async def _graph(cl, g):
    """График Telegram -> (даты, {серия: значения}) или None."""
    if isinstance(g, StatsGraphAsync):
        g = await cl(LoadAsyncGraphRequest(token=g.token))
    if not isinstance(g, StatsGraph):
        return None
    data = json.loads(g.json.data)
    cols = data.get("columns") or []
    if not cols:
        return None
    names = data.get("names") or {}
    x = [datetime.fromtimestamp(t / 1000, timezone.utc).date() for t in cols[0][1:]]
    return x, {names.get(c[0], c[0]): c[1:] for c in cols[1:]}


def _sum_since(g, since):
    """Сумма каждой серии графика с даты since."""
    if not g:
        return {}
    x, series = g
    return {k: sum(v for d, v in zip(x, vals) if d >= since and v) for k, vals in series.items()}


async def collect(days=7):
    """
    Всё, что видно аккаунту-админу, за days дней:
      subs, posts [{id, date, views, forwards, reactions}], stats (если Telegram
      открыл каналу статистику) — приток/отток, источники, уведомления, языки.
    """
    a = _load()
    if not a:
        raise RuntimeError("аккаунт статистики не подключён")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    cl = _client(a["session"], a["api_id"], a["api_hash"])
    await cl.connect()
    try:
        if not await cl.is_user_authorized():
            raise RuntimeError("сессия аккаунта статистики больше не действует — подключи заново")
        ch = await cl.get_entity(int(db.channel_id()))
        full = (await cl(GetFullChannelRequest(ch))).full_chat
        posts = []
        async for m in cl.iter_messages(ch, limit=1000):
            if m.date < since:
                break
            if m.action:                         # служебные: закрепил, сменил фото
                continue
            reacts = sum(r.count for r in m.reactions.results) if m.reactions else 0
            posts.append({"id": m.id, "date": m.date, "views": m.views or 0,
                          "forwards": m.forwards or 0, "reactions": reacts,
                          "text": (m.message or "")[:80]})
        out = {"subs": full.participants_count, "posts": posts, "stats": None,
               "stats_error": None, "title": getattr(ch, "title", ""),
               "username": getattr(ch, "username", None)}
        if getattr(full, "can_view_stats", False):
            try:
                st = await cl(GetBroadcastStatsRequest(channel=ch))
                d0 = since.date()
                out["stats"] = {
                    "followers": (st.followers.current, st.followers.previous),
                    "notifications": (st.enabled_notifications.part / st.enabled_notifications.total * 100
                                      if st.enabled_notifications.total else None),
                    "growth": _sum_since(await _graph(cl, st.followers_graph), d0),
                    "sources": _sum_since(await _graph(cl, st.new_followers_by_source_graph), d0),
                    "views_sources": _sum_since(await _graph(cl, st.views_by_source_graph), d0),
                    "languages": _sum_since(await _graph(cl, st.languages_graph), d0),
                }
            except Exception as e:
                out["stats_error"] = f"{type(e).__name__}: {e}"
        else:
            out["stats_error"] = "Telegram ещё не открыл каналу статистику (обычно от 50 подписчиков)"
        return out
    finally:
        await cl.disconnect()
