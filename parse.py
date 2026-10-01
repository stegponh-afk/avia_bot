"""
Разбор свободного текста: даты и маршруты.

Кнопки кнопками, а написать «Сочи 18.10 25.10» быстрее. Здесь только чистые
функции без Telegram — их легко проверить руками.
"""
import re
from datetime import date, datetime

import places


DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"),      # 2026-12-01
    re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"),    # 01.12.2026
    re.compile(r"^(\d{1,2})\.(\d{1,2})$"),             # 01.12, год ближайший
)


def parse_one_date(tok):
    """'01.12.2026', '2026-12-01' и '01.12' -> 'YYYY-MM-DD' или None."""
    tok = tok.strip()
    m = DATE_PATTERNS[0].match(tok)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
    else:
        m = DATE_PATTERNS[1].match(tok)
        if m:
            y, mo, d = m.group(3), m.group(2), m.group(1)
        else:
            m = DATE_PATTERNS[2].match(tok)
            if not m:
                return None
            d, mo = m.group(1), m.group(2)
            today = date.today()
            # «01.12» без года: если месяц уже прошёл, человек имеет в виду следующий год
            y = str(today.year if int(mo) >= today.month else today.year + 1)
    try:
        return datetime(int(y), int(mo), int(d)).date().isoformat()
    except ValueError:
        return None


# Даты именно ищем в тексте, а не делим строку по разделителям: дефис служит
# и разделителем периода, и частью самой даты 2026-12-01.
DATE_TOKEN = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\.\d{1,2}")


def parse_dates(text):
    """'01.12.2026 15.12.2026', '2026-12-01 - 2026-12-15' -> (с, по).

    Одна дата = только «с», порядок неважен.
    """
    got = [p for p in (parse_one_date(t) for t in DATE_TOKEN.findall(text or "")) if p]
    if not got:
        return None, None
    if len(got) == 1:
        return got[0], None
    return min(got[:2]), max(got[:2])


CITY_SEPS = ("→", "->", "—", "–", " - ", ",", " в ")


def _cities(text):
    """
    Вытаскивает из строки один или два города.

    Сначала пробуем явные разделители, потом — всю строку целиком как один
    город, и только затем перебираем точку разреза. Порядок важен:
    «Нижний Новгород» это один город, а «Киров Питер» — два, и отличить
    их можно только попыткой разобрать.
    """
    text = " ".join((text or "").split())
    if not text:
        return []

    for sep in CITY_SEPS:
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            codes = [places.find(p) for p in parts]
            if len(codes) >= 2 and all(codes):
                return codes[:2]

    whole = places.find(text)
    if whole:
        return [whole]

    words = text.split()
    for i in range(1, len(words)):
        a = places.find(" ".join(words[:i]))
        b = places.find(" ".join(words[i:]))
        if a and b:
            return [a, b]

    for w in words:
        c = places.find(w)
        if c:
            return [c]
    return []


def parse_trip(text, default_origin):
    """
    «Киров Питер 18.09 20.09» -> (откуда, куда, туда, обратно).

    Города можно не писать оба: если назван один, вылет берётся из настроек.
    Одна дата — билет в одну сторону, две — туда и обратно.
    """
    found = [parse_one_date(t) for t in DATE_TOKEN.findall(text or "")]
    dates = sorted({d for d in found if d})
    if not dates:
        return None

    codes = _cities(DATE_TOKEN.sub(" ", text or ""))
    if not codes:
        return None

    origin, dest = (codes[0], codes[1]) if len(codes) >= 2 else (default_origin, codes[0])
    if origin == dest:
        return None
    return origin, dest, dates[0], (dates[1] if len(dates) > 1 else None)
