"""
Сводки по каналу — по кнопке в админке.

Три источника, и сводка работает с тем, что есть:
  • живой аккаунт-админ (tgstats) — подписчики, просмотры, пересылки,
    реакции, источники подписчиков, уведомления;
  • Travelpayouts (tpstats) — клики по кнопкам постов и покупки по меткам;
  • сам бот — вступления и выходы по пригласительным ссылкам и переходы
    в бота по ссылкам (links.py) — с ценой рекламы, если она указана.

owner_report — для владельца: что происходит и что заходит.
mediakit     — для рекламодателей: цифры, которые спрашивают при закупке.
"""
import html
import json
import statistics
from datetime import datetime, timedelta

import card
import channel
import config as C
import db
import links
import places
import render
import tgstats
import tpstats

KIND = {"super": "Суперскидки", "deal": "Скидки", "drop": "Подешевело"}
DAYPART = ((8, 12, "Утро"), (12, 17, "День"), (17, 23, "Вечер"), (23, 32, "Ночь"))


def _n(v):
    return f"{int(round(v)):,}".replace(",", " ")


def _clicks(n):
    return f"{n} {render.plural(n, 'клик', 'клика', 'кликов')}"


def _sales(n):
    return f"{n} {render.plural(n, 'покупка', 'покупки', 'покупок')}"


async def gather(days):
    """Собрать всё за days дней. Чего нет — пишем причину, сводка не падает."""
    since = datetime.now() - timedelta(days=days)
    data = {"days": days, "since": since, "tg": None, "tp": None,
            "tg_error": None, "tp_error": None}
    if tgstats.connected():
        try:
            data["tg"] = await tgstats.collect(days)
        except Exception as e:
            data["tg_error"] = f"{type(e).__name__}: {e}"
    else:
        data["tg_error"] = "аккаунт статистики не подключён — 🔐 в админке"
    try:
        data["tp"] = tpstats.summarize(await tpstats.events(since.date().isoformat()))
    except Exception as e:
        data["tp_error"] = f"{type(e).__name__}: {e}"
    s = since.isoformat(timespec="seconds")
    data["flow"] = db.member_flow(s)
    data["bot"] = db.link_stats("channel", s)
    data["links"] = [(l, links.counts(l, s)) for l in db.links()]
    return data


def _views(tg):
    """Просмотры постов: только тех, кому уже есть сутки — свежие ещё набирают."""
    posts = tg["posts"] if tg else []
    day_ago = datetime.now().astimezone() - timedelta(hours=24)
    ripe = [p for p in posts if p["date"] <= day_ago] or posts
    return [p["views"] for p in ripe]


def _by_post(tg):
    """Посты Telegram, сопоставленные с постами бота: {message_id: (пост tg, находка, разметка)}."""
    if not tg:
        return {}
    mine = {}
    for p in db.connect().execute("SELECT * FROM posts WHERE message_id IS NOT NULL"):
        mine[p["message_id"]] = p
    out = {}
    for t in tg["posts"]:
        p = mine.get(t["id"])
        if p:
            data = json.loads(p["payload"])
            out[t["id"]] = (t, data["deal"], data["cand"], p)
    return out


def what_works(tg):
    """Средние просмотры по видам постов: что заходит лучше."""
    groups = {}

    def add(group, key, views):
        groups.setdefault(group, {}).setdefault(key, []).append(views)
    for t, d, c, _ in _by_post(tg).values():
        add("вид", KIND.get(c.get("kind"), "Другое"), t["views"])
        add("куда", "Россия" if places.country(d["dest"]) == "Россия" else "Заграница", t["views"])
        add("откуда", "Из Москвы" if d["origin"] == "MOW" else "Из Питера"
            if d["origin"] == "LED" else places.name(d["origin"]), t["views"])
        h = t["date"].astimezone().hour
        h = h + 24 if h < 8 else h
        add("время", next(name for a, b, name in DAYPART if a <= h < b), t["views"])
    return {g: {k: (sum(v) / len(v), len(v)) for k, v in items.items()}
            for g, items in groups.items()}


def owner_report(data):
    """(картинка, текст) — сводка для владельца."""
    days, tg, tp = data["days"], data["tg"], data["tp"]
    views = _views(tg)
    avg = statistics.mean(views) if views else 0
    subs = tg["subs"] if tg else None
    err = avg / subs * 100 if subs else None
    joined, left = data["flow"]
    st = (tg or {}).get("stats") or {}
    g = st.get("growth") or {}
    if g:                                      # графики Telegram точнее событий бота
        joined = int(g.get("Joined", g.get("Подписались", joined)) or joined)
        left = int(g.get("Left", g.get("Отписались", left)) or left)

    lines = [f"📊 <b>Статистика канала за {days} {render.plural(days, 'день', 'дня', 'дней')}</b>"]
    if tg:
        lines += [
            f"👥 Подписчиков: <b>{_n(subs)}</b> (+{joined} / −{left})",
            f"👁 Охват поста: в среднем <b>{_n(avg)}</b>, медиана {_n(statistics.median(views)) if views else 0}, "
            f"лучший {_n(max(views)) if views else 0}",
            f"📈 ERR: <b>{err:.0f}%</b> · постов {len(tg['posts'])} "
            f"({len(tg['posts']) / days:.0f} в день)" if err is not None else "",
            f"↗️ Пересылок: {sum(p['forwards'] for p in tg['posts'])} · "
            f"❤️ реакций: {sum(p['reactions'] for p in tg['posts'])}",
        ]
        if st.get("notifications") is not None:
            lines.append(f"🔔 Уведомления включены у {st['notifications']:.0f}% подписчиков")
    else:
        lines += [f"👥 По событиям бота: +{joined} / −{left}",
                  f"<i>Просмотров нет: {html.escape(data['tg_error'] or '')}</i>"]

    works = what_works(tg)
    if works:
        lines += ["", "🔥 <b>Что заходит</b> — средние просмотры:"]
        for group in ("вид", "куда", "откуда", "время"):
            items = sorted(works.get(group, {}).items(), key=lambda x: -x[1][0])
            if items:
                lines.append("• " + " · ".join(f"{k} {_n(v)}" for k, (v, n) in items))

    if tp:
        b = tp["by_button"]
        lines += ["", "💰 <b>Клики и покупки</b> (Travelpayouts)",
                  f"• Кликов: <b>{tp['clicks']}</b>"
                  + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(b.items(), key=lambda x: -x[1])) + ")"
                     if b else ""),
                  f"• Покупок: <b>{tp['sales']}</b> · доход {render.money(tp['profit'])}"]
        src = tp["by_source"]
        if src:
            lines.append("• " + " · ".join(f"{k}: {_clicks(v['clicks'])}, {_sales(v['sales'])}"
                                           for k, v in src.items()))
        top = sorted(tp["by_post"].items(), key=lambda x: (-x[1]["sales"], -x[1]["clicks"]))[:3]
        for pid, v in top:
            p = db.get_post(pid)
            if p:
                d = json.loads(p["payload"])["deal"]
                lines.append(f"  {channel.route(d)} — {_clicks(v['clicks'])}"
                             + (f", {_sales(v['sales'])}" if v["sales"] else ""))
    else:
        lines.append(f"<i>Клики недоступны: {html.escape(data['tp_error'] or '')}</i>")

    visits, new, stayed = data["bot"]
    lines += ["", f"🤖 Из канала в бота: {visits} "
                  f"{render.plural(visits, 'переход', 'перехода', 'переходов')}, "
                  f"новых {new}, остались {stayed}"]

    if st.get("sources"):
        lines.append("🧭 Откуда подписчики: " + " · ".join(
            f"{k} {int(v)}" for k, v in sorted(st["sources"].items(), key=lambda x: -x[1]) if v))

    used = [(l, c) for l, c in data["links"] if c[0]]
    if used:
        lines += ["", "🔗 <b>Ссылки</b> — пришли / остались за период"]
        for l, (came, new, stayed) in sorted(used, key=lambda x: -x[1][0])[:6]:
            price = f" · {render.money(l['cost'] / stayed)} за оставшегося" if l["cost"] and stayed else ""
            lines.append(f"• {links.TARGET.get(l['target'] or 'bot')} {html.escape(l['name'])}: "
                         f"{came} / {stayed}{price}")

    best = sorted(_by_post(tg).values(), key=lambda x: -x[0]["views"])[:5]
    if best:
        lines += ["", "🏆 <b>Лучшие посты</b>"]
        for i, (t, d, c, p) in enumerate(best, 1):
            lines.append(f'{i}. <a href="{channel.post_url(t["id"])}">{channel.route(d)} '
                         f'−{c.get("pct")}%</a> — {_n(t["views"])} 👁, {t["forwards"]} ↗️')
    if tg and tg.get("stats_error"):
        lines += ["", f"<i>Графики Telegram: {html.escape(tg['stats_error'])}</i>"]

    tiles = [(_n(subs) if subs else "—", "подписчиков"), (_n(avg) if views else "—", "охват поста"),
             (f"{err:.0f}%" if err is not None else "—", "ERR"),
             (str(tp["clicks"]) if tp else "—", "кликов")]
    rows = [(f"{channel.route(d)} −{c.get('pct')}%", _n(t["views"])) for t, d, c, p in best[:4]]
    png = card.dashboard(f"Статистика за {days} {render.plural(days, 'день', 'дня', 'дней')}",
                         f"+{joined} / −{left} подписчиков · постов {len(tg['posts']) if tg else '—'}",
                         tiles, rows, footnote="лучшие посты — просмотры" if rows else None)
    return png, "\n".join(x for x in lines if x is not None)


def mediakit(data):
    """(картинка, текст) — медиакит для рекламодателей, за 30 дней."""
    tg = data["tg"]
    if not tg:
        return None
    views = _views(tg)
    avg = statistics.mean(views) if views else 0
    subs = tg["subs"]
    err = avg / subs * 100 if subs else 0
    per_day = len(tg["posts"]) / data["days"]
    st = tg.get("stats") or {}
    g = st.get("growth") or {}
    grow = int(g.get("Joined", 0) - g.get("Left", 0)) if g else data["flow"][0] - data["flow"][1]
    link = f"t.me/{tg['username']}" if tg.get("username") else ""
    best = sorted(_by_post(tg).values(), key=lambda x: -x[0]["views"])[:3]

    lines = [f"✈️ <b>{html.escape(tg['title'])}</b>", link, "",
             "Авиабилеты из Москвы и Петербурга на 40–90% дешевле обычного. "
             "Бот ловит скидки и падения цен и публикует их сам, цены в постах "
             "обновляются, ушедшие скидки помечаются.", "",
             f"👥 Подписчиков: <b>{_n(subs)}</b>",
             f"👁 Средний охват поста: <b>{_n(avg)}</b>",
             f"📈 ERR: <b>{err:.0f}%</b>",
             f"🗓 Постов в день: {per_day:.0f} — с 8 до 23, ночью только суперскидки",
             f"📊 За {data['days']} дней: {'+' if grow >= 0 else ''}{grow} подписчиков"]
    if st.get("languages"):
        top = sorted(st["languages"].items(), key=lambda x: -x[1])[:2]
        total = sum(st["languages"].values()) or 1
        lines.append("🌍 Языки аудитории: " + ", ".join(f"{k} {v / total * 100:.0f}%" for k, v in top))
    lines.append("🎯 Аудитория: путешественники из Москвы и Петербурга, "
                 "ищут дешёвые билеты на выходные и в отпуск")
    if best:
        lines += ["", "<b>Лучшие посты:</b>"] + [
            f"• {channel.route(d)} −{c.get('pct')}% — {_n(t['views'])} просмотров"
            for t, d, c, p in best]
    if C.AD_CONTACT:
        lines += ["", f"📩 Реклама: {html.escape(C.AD_CONTACT)}"]
    png = card.dashboard(tg["title"] or "Канал", f"{link} · медиакит · последние {data['days']} дней",
                         [(_n(subs), "подписчиков"), (_n(avg), "охват поста"),
                          (f"{err:.0f}%", "ERR"), (f"{per_day:.0f}", "постов в день")],
                         [(f"{channel.route(d)} −{c.get('pct')}%", f"{_n(t['views'])}")
                          for t, d, c, p in best], footnote="лучшие посты — просмотры")
    return png, "\n".join(lines)
