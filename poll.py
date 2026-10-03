"""
Сборщик: обходит все источники по всем городам вылета, отбирает интересное.

Один город — примерно десять запросов к Travelpayouts. Пока подписчики летают
из одного-двух городов, в лимиты укладываемся с большим запасом.
"""
import asyncio
import traceback
from datetime import date, datetime

import aiohttp

import airlines
import amadeus
import combo
import config as C
import db
import detect
import places
import tp


def needed_months():
    """
    Какие месяцы опрашивать.

    Обычно это ближайшие MONTHS_AHEAD. Но если кто-то из подписчиков задал окно
    дат подальше — например, отпуск в июле, — то без этих месяцев он не увидит
    ничего вообще, поэтому добавляем и их.
    """
    months = set(tp.months_ahead(C.MONTHS_AHEAD))
    today = date.today()
    limit = f"{today.year + 1:04d}-{today.month:02d}"     # не дальше года вперёд

    for s in db.active_subs():
        a, b = s["date_from"], s["date_to"]
        if not (a and b):
            continue
        y, m = int(a[:4]), int(a[5:7])
        while f"{y:04d}-{m:02d}" <= b[:7] and f"{y:04d}-{m:02d}" <= limit:
            if f"{y:04d}-{m:02d}" >= f"{today.year:04d}-{today.month:02d}":
                months.add(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
    return sorted(months)


async def gather_all(progress=None):
    """
    Все предложения из всех включённых источников по всем городам вылета.

    progress(текст) — чтобы бот мог показывать человеку, что именно сейчас
    происходит: опрос занимает до минуты, и молчание выглядит зависанием.
    """
    deals = []
    months = needed_months()
    origins = db.origins()

    def say(text):
        if progress:
            progress(text)

    async with aiohttp.ClientSession() as s:
        for n, origin in enumerate(origins, 1):
            say(f"город {places.name(origin)} · {n} из {len(origins)}")
            try:
                got = await tp.Travelpayouts(s, origin).collect(months)
                deals += got
                print(f"  travelpayouts {origin}: {len(got)}")
            except Exception as e:
                print(f"  travelpayouts {origin}: упал — {e}")

        for name, coro in (("amadeus", amadeus.collect(s)),
                           ("акции авиакомпаний", airlines.collect(s))):
            say(name)
            try:
                got = await coro
                deals += got
                print(f"  {name}: {len(got)}")
            except Exception as e:
                print(f"  {name}: упал — {e}")
    return deals


async def run_once(progress=None, on_found=None):
    """
    Один проход. Возвращает список предложений, о которых стоит написать.

    on_found(размеченное) — для канала: ему нужна вся разметка прохода,
    а не только то, что уйдёт подписчикам.
    """
    started = datetime.now()
    print(f"[{started:%H:%M:%S}] опрос источников...")
    deals = await gather_all(progress)
    # источник ничего не вернул ни по одному городу — это не «скидок нет»,
    # это поломка: протух токен, лимит, сеть
    import notify
    if sum(1 for d in deals if (d.get("src") or "").startswith("tp/")) == 0:
        await notify.problem("api", "Источник цен ничего не вернул ни по одному городу"
                                    f" ({tp.last_error or 'причина неизвестна'}). "
                                    "Скидки и канал стоят. Проверь TP_TOKEN.")
    else:
        await notify.resolved("api", "Источник цен снова отвечает.")
    deals += await combos_due(progress=progress)

    # Город за городом: собрать календари обхода, разметить, отпустить.
    # Раньше обход держал в памяти календари всех городов разом — контейнер
    # раздувался до 450 МБ. Разметке соседство городов не нужно: история,
    # соседние даты и страны считаются внутри своего города вылета.
    # Разметка — в отдельном потоке, чтобы бот тем временем отвечал на кнопки.
    sweep = sweep_is_due()
    origins = db.origins()
    found, swept, sweep_started = [], 0, datetime.now()
    if sweep:
        # справочник маршрутов раз в месяц качается заново (14 МБ) — не в потоке бота
        await asyncio.to_thread(places.direct_routes, C.ORIGIN)
    async with aiohttp.ClientSession() as s:
        for origin in origins:
            chunk = [d for d in deals if d["origin"] == origin]
            if sweep:
                cal = await sweep_origin(s, origin, progress)
                swept += len(cal)
                chunk += cal
                del cal
            if progress:
                progress(f"отбираю находки: {places.name(origin)}")
            found += await asyncio.to_thread(detect.evaluate, chunk)
            del chunk
    rest = [d for d in deals if d["origin"] not in origins]
    if rest:                                   # хабы связок: только в историю
        await asyncio.to_thread(detect.evaluate, rest)
    if sweep:
        db.meta_set("last_sweep", datetime.now().isoformat(timespec="seconds"))
        secs = (datetime.now() - sweep_started).total_seconds()
        print(f"  обход: {swept} цен за {secs:.0f} с")
    db.save_feed(found)
    alerts = detect.select(found)
    if on_found:
        try:
            await on_found(found)
        except Exception:
            traceback.print_exc()
    db.meta_set("last_poll", started.isoformat(timespec="seconds"))
    db.meta_set("last_count", len(deals))
    print(f"  собрано {len(deals)}, в базе {db.count_prices()}, "
          f"к отправке {len(alerts)}")
    return alerts


def sweep_targets(origin):
    """Куда заглядывать при обходе: прямые маршруты + всё, что было в истории."""
    out = set(places.direct_routes(origin)) | set(db.destinations(origin))
    out.discard(origin)
    out.discard("?")
    return sorted(c for c in out if c and len(c) == 3)


def sweep_is_due():
    """Пора ли проходить полный круг по направлениям (раз в SWEEP_EVERY_MIN)."""
    if not C.SWEEP_ENABLED:
        return False
    last = db.meta_get("last_sweep")
    if not last:
        return True
    age = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
    return age >= C.SWEEP_EVERY_MIN


async def sweep_origin(session, origin, progress=None):
    """
    Календари по всем направлениям одного города: запрос на направление,
    цены на год. Дальше горизонта опроса не берём: ради января 2028 не стоит
    ни хранить, ни будить людей.
    """
    until = needed_months()[-1] + "-31"
    api = tp.Travelpayouts(session, origin)
    targets = sweep_targets(origin)
    out = []
    for n, dest in enumerate(targets, 1):
        if progress and n % 10 == 1:
            progress(f"обход {places.name(origin)}: {n} из {len(targets)}")
        out += await api.route_dates(dest, until=until)
    print(f"  обход {origin}: {len(targets)} направлений, {len(out)} цен")
    return out


async def warm(origin):
    """
    Первый сбор по новому городу вылета, сразу после выбора.

    Иначе человек выбирает город и до следующего прохода видит пустую ленту.
    Ничего не рассылает: скидки лягут в ленту, письма уйдут обычным ходом.
    Историю маршрутов это не заменит — «обычную цену» бот узнаёт за несколько
    дней, — но соседние даты и кривая расстояний работают сразу.
    """
    if db.has_recent(origin, hours=6):
        return 0
    print(f"  прогрев {origin}")
    try:
        async with aiohttp.ClientSession() as s:
            got = await tp.Travelpayouts(s, origin).collect(needed_months())
        db.save_feed(detect.evaluate(got))
        print(f"  прогрев {origin}: {len(got)}")
        return len(got)
    except Exception as e:
        print(f"  прогрев {origin}: упал — {e}")
        return 0


async def find_combos(origin, dests=None, months=None, hubs_n=None, store=True,
                      progress=None):
    """
    Точечный поиск связок по одному городу.

    store=False — когда город назван вручную: такой разовый ответ не должен
    затирать общий список, собранный по всем направлениям.
    """
    async with aiohttp.ClientSession() as s:
        got = await combo.search(s, origin, dests=dests, months=months,
                                 hubs_n=hubs_n, progress=progress,
                                 log=lambda line: print("    " + line))
    if store:
        db.save_combos(origin, got)
    return got


async def combos_due(force=False, progress=None):
    """
    Связки ищем не каждый прогон: это около минуты запросов на город.

    Возвращает найденное, чтобы оно прошло через обычный отбор и рассылку.
    """
    if not C.COMBO_ENABLED:
        return []
    last = db.meta_get("last_combo")
    if not force and last:
        age = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
        if age < C.COMBO_EVERY_MIN:
            return []

    out = []
    for origin in db.origins():
        if progress:
            progress(f"связки из города {places.name(origin)}")
        try:
            got = await find_combos(origin, progress=progress)
            print(f"  связок из {origin}: {len(got)}")
            out += got
        except Exception as e:
            print(f"  связки {origin}: упал — {e}")
    db.meta_set("last_combo", datetime.now().isoformat(timespec="seconds"))
    return out


async def watch_due():
    """Свои направления людей: после общей рассылки, чтобы не толкаться."""
    import watch
    try:
        await watch.check()
    except Exception:
        traceback.print_exc()


async def watch_daily_due():
    """Ежедневная сводка по своим направлениям — после свежей проверки цен."""
    import watch
    try:
        await watch.daily_due()
    except Exception:
        traceback.print_exc()


async def backup_due():
    """Бэкап базы раз в BACKUP_EVERY_H часов, в отдельном потоке."""
    import backup
    import notify
    last = db.meta_get("last_backup")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() \
            < C.BACKUP_EVERY_H * 3600:
        return
    try:
        path, mb = await asyncio.to_thread(backup.run)
        db.meta_set("last_backup", datetime.now().isoformat(timespec="seconds"))
        print(f"  бэкап: {path}, {mb} МБ")
        await notify.resolved("backup", "Бэкап базы снова делается.")
    except Exception as e:
        await notify.problem("backup", f"Бэкап базы не сделался: {e}")


async def partner_due():
    """Раз в сутки обновить параметры партнёрских ссылок (erid может смениться)."""
    last = db.meta_get("partner_at")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < 86400:
        return
    await tp.refresh_partner()
    db.meta_set("partner_at", datetime.now().isoformat(timespec="seconds"))


async def loop(on_alerts, on_found=None):
    """
    Вечный цикл опроса. on_alerts(список) вызывается, когда есть что слать.
    Два прохода подряд упали — пишем владельцу: иначе поломку видно
    только по тишине.
    """
    import notify
    fails = 0
    while True:
        try:
            alerts = await run_once(on_found=on_found)
            if alerts:
                await on_alerts(alerts)
            await watch_due()
            await notify.morning()
            await watch_daily_due()
            db.prune()
            if fails >= 2:
                await notify.resolved("poll", "Сборщик цен снова работает.")
            fails = 0
        except Exception as e:
            traceback.print_exc()
            fails += 1
            if fails >= 2:
                await notify.problem("poll", f"Сборщик цен падает {fails} раза подряд: "
                                             f"{type(e).__name__}: {str(e)[:200]}")
        await backup_due()
        await partner_due()
        await asyncio.sleep(C.POLL_EVERY_MIN * 60)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import render
    print("города вылета:", ", ".join(db.origins()))
    print("месяцы:", ", ".join(needed_months()))
    got = asyncio.run(run_once())
    print(f"\nнашлось поводов написать: {len(got)}\n")
    for d in got:
        print(render.deal(d).replace("<b>", "").replace("</b>", ""), "\n")
