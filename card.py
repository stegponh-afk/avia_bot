"""
Картинка к посту в канале: маршрут, цена, зачёркнутая обычная, процент.

Рисуем сами, а не снимаем скриншот с сайта. Скриншот — это браузер на
сервере, защита сайта от ботов и живая цена, которая может не совпасть
с постом. Своя карточка рисуется за доли секунды и цифры на ней всегда
те же, что в тексте.

Шрифты ищутся по списку: Inter в контейнере, Segoe UI на Windows, DejaVu
как последний вариант — у всех есть кириллица и знак ₽.
"""
import glob
import io
import math

from PIL import Image, ImageDraw, ImageFont

import config as C

W, H = 1280, 720
X0 = 72

BG_TOP, BG_BOTTOM = (15, 23, 42), (30, 27, 75)
WHITE, SOFT, MUTED, DIM = (255, 255, 255), (226, 232, 240), (148, 163, 184), (100, 116, 139)
GREEN, VIOLET, LILAC = (74, 222, 128), (99, 102, 241), (165, 180, 252)
ACCENT = {"super": (239, 68, 68), "deal": (249, 115, 22), "drop": (14, 165, 233),
          "combo": (168, 85, 247), "budget": (22, 163, 74), "cheaper": (14, 165, 233)}
LABEL = {"super": "СУПЕРСКИДКА", "deal": "СКИДКА", "drop": "ПОДЕШЕВЕЛО ЗА СУТКИ",
         "combo": "ДВУМЯ БИЛЕТАМИ", "budget": "В ТВОЁМ БЮДЖЕТЕ",
         "cheaper": "ПОДЕШЕВЕЛО"}

FONTS = {
    "regular": ["/usr/share/fonts/opentype/inter/Inter-Regular.otf",
                "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
                "/usr/share/fonts/**/Inter-Regular.*",
                "C:/Windows/Fonts/segoeui.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": ["/usr/share/fonts/opentype/inter/Inter-Bold.otf",
             "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
             "/usr/share/fonts/**/Inter-Bold.*",
             "C:/Windows/Fonts/segoeuib.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
_paths = {}


def _font(kind, size):
    if kind not in _paths:
        for pattern in FONTS[kind]:
            found = glob.glob(pattern, recursive=True)
            if found:
                _paths[kind] = found[0]
                break
        else:
            raise RuntimeError("не нашёлся шрифт с кириллицей для карточки")
    return ImageFont.truetype(_paths[kind], size)


def _fit(d, text, kind, size, max_w):
    """Крупно, но в ширину: «Санкт-Петербург → Петропавловск-Камчатский» тоже влезет."""
    while size > 30:
        fnt = _font(kind, size)
        if d.textlength(text, font=fnt) <= max_w:
            return fnt
        size -= 4
    return _font(kind, size)


def _background():
    img = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return img


# Самолёт контуром, носом вправо, в условных единицах. Рисуем полигоном,
# а не эмодзи: цветных эмодзи в шрифтах сервера нет, а так он ещё
# и поворачивается ровно по касательной к траектории.
PLANE = [(24, 0), (18, -2.6), (5, -3), (-5, -19), (-10, -19), (-5, -3),
         (-15, -3), (-20, -10), (-24, -10), (-21, 0),
         (-24, 10), (-20, 10), (-15, 3), (-5, 3), (-10, 19), (-5, 19),
         (5, 3), (18, 2.6)]


def _plane(img, x, y, angle, scale=2.2, color=WHITE):
    """Самолёт с поворотом и сглаживанием: рисуем вчетверо крупнее и уменьшаем."""
    k = 4
    size = int(60 * scale) * k
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    c, s = math.cos(angle), math.sin(angle)
    pts = [(size / 2 + (px * c - py * s) * scale * k,
            size / 2 + (px * s + py * c) * scale * k) for px, py in PLANE]
    ld.polygon(pts, fill=color + (255,))
    layer = layer.resize((size // k, size // k), Image.LANCZOS)
    img.paste(layer, (int(x - size / k / 2), int(y - size / k / 2)), layer)


def _curve(a, b, top):
    """Квадратичная кривая Безье: точка на ней по доле пути t."""
    def point(t):
        return ((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * top[0] + t ** 2 * b[0],
                (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * top[1] + t ** 2 * b[1])
    return point


def _dashes(d, point, step=9):
    """Пунктир с одинаковым шагом штриха, какой бы длины ни была дуга."""
    probe = [point(i / 20) for i in range(21)]
    length = sum(math.dist(p, q) for p, q in zip(probe, probe[1:]))
    n = max(4, int(length / step) // 2 * 2)
    pts = [point(i / n) for i in range(n + 1)]
    for i in range(0, n, 2):
        d.line([pts[i], pts[i + 1]], fill=VIOLET, width=4)


def _dot(d, x, y, hollow=False):
    """Город на траектории. Пересадка — кольцом: там не конец пути."""
    if hollow:
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=BG_TOP, outline=LILAC, width=4)
    else:
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=LILAC)


def _flight(img, d, hops=1, via=()):
    """
    Пунктир маршрута в правом верхнем углу и самолёт на нём.

    Прямой рейс — одна дуга. С пересадками — дуга на каждый перелёт:
    сюда, оттуда дальше, и точка-кольцо в городе пересадки. Самолёт
    на последнем участке — «уже летим к цели».
    """
    a, b = (860, 150), (1200, 70)
    if hops <= 1:
        legs = [_curve(a, b, (1000, 20))]
    else:
        n = min(hops, 3)                      # больше трёх дуг в углу не уместить
        stops = [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                 for i in range(n + 1)]
        lift = 70 if n == 2 else 50           # высота «прыжка» над прямой
        legs = [_curve(p, q, ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 - lift))
                for p, q in zip(stops, stops[1:])]

    for point in legs:
        _dashes(d, point)
    ends = [legs[0](0)] + [leg(1) for leg in legs]
    for i, (x, y) in enumerate(ends):
        _dot(d, x, y, hollow=0 < i < len(ends) - 1)

    # подписи городов пересадки под кольцами; две — чуть мельче, чтобы не слиплись
    rings = ends[1:-1]
    room = (ends[-1][0] - ends[0][0]) / len(legs) - 12
    for (x, y), name in zip(rings, via or ()):
        fnt = _fit(d, name, "regular", 24 if len(rings) == 1 else 22,
                   room * (1.6 if len(rings) == 1 else 1))
        d.text((x, y + 18), name, font=fnt, fill=SOFT, anchor="mt")

    last = legs[-1]
    t = 0.58 if hops <= 1 else 0.5
    (x1, y1), (x2, y2) = last(t - 0.01), last(t + 0.01)
    x, y = last(t)
    _plane(img, x, y, math.atan2(y2 - y1, x2 - x1), scale=2.2 if hops <= 1 else 1.8)


def digest(title, subtitle, rows, sign=None):
    """
    Картинка дайджеста: заголовок и до шести строк «−55% · маршрут · цена».
    rows — [(процент, маршрут, цена-строкой), ...].
    """
    img = _background()
    d = ImageDraw.Draw(img)
    d.text((X0, 70), title, font=_font("bold", 64), fill=WHITE)
    d.text((X0, 158), subtitle, font=_font("regular", 32), fill=MUTED)
    y, pill = 230, _font("bold", 36)
    for pct, route, price in rows[:6]:
        txt = f"−{pct}%"
        color = ACCENT["super"] if pct >= C.SUPER_PCT else ACCENT["deal"]
        d.rounded_rectangle([X0, y, X0 + 150, y + 58], radius=29, fill=color)
        d.text((X0 + 75, y + 29), txt, font=pill, fill=WHITE, anchor="mm")
        pf = _font("bold", 40)
        pw = d.textlength(price, font=pf)
        d.text((W - X0, y + 29), price, font=pf, fill=GREEN, anchor="rm")
        room = W - 2 * X0 - 150 - 40 - pw - 30
        d.text((X0 + 180, y + 29), route, font=_fit(d, route, "regular", 38, room),
               fill=WHITE, anchor="lm")
        y += 72
    sign = C.CHANNEL_SIGN if sign is None else sign
    if sign:
        d.text((W - X0, H - 40), sign, font=_font("bold", 30), fill=LILAC, anchor="rs")
    d.text((X0, H - 40), "цены на момент публикации", font=_font("regular", 26),
           fill=DIM, anchor="ls")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def stamp(png, text="НЕ АКТУАЛЬНО"):
    """
    Та же карточка, притушенная, с плашкой поперёк. Для постов, чья цена
    ушла: читатель листает ленту и должен понять это по картинке, не читая.
    """
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", img.size, (8, 10, 20, 150)))
    d = ImageDraw.Draw(img)
    fnt = _font("bold", 64)
    tw = d.textlength(text, font=fnt)
    cx, cy = W / 2, H / 2
    d.rounded_rectangle([cx - tw / 2 - 48, cy - 62, cx + tw / 2 + 48, cy + 62],
                        radius=24, fill=(30, 30, 40, 235), outline=ACCENT["super"], width=6)
    d.text((cx, cy), text, font=fnt, fill=ACCENT["super"], anchor="mm")
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render(kind, pct, route, country, price, usual, info, sign=None, transfers=0,
           via=(), back=None, footnote="цена на момент публикации"):
    """
    Готовая картинка в PNG-байтах.

    kind — super / deal / drop (цвет плашки и подпись),
    price и usual — уже отформатированные строки «3 495 ₽»,
    transfers — сколько пересадок: столько же колец на траектории.
    Неизвестно (None) — рисуем как прямой, выдумывать пересадку хуже.
    via — названия городов пересадки, подписываются под кольцами.
    back — строка про обратный билет, мельче под строкой рейса.
    pct None — процента нет (билет просто в бюджете): в плашке одна подпись.
    """
    img = _background()
    d = ImageDraw.Draw(img)
    _flight(img, d, hops=max(transfers or 0, len(via or ())) + 1, via=via)

    big = _font("bold", 64) if pct is not None else _font("bold", 40)
    txt = f"−{pct}%" if pct is not None else LABEL[kind]
    tw = d.textlength(txt, font=big)
    d.rounded_rectangle([X0, 60, X0 + tw + 56, 156], radius=48, fill=ACCENT[kind])
    d.text((X0 + 28, 108), txt, font=big, fill=WHITE, anchor="lm")
    if pct is not None:
        d.text((X0 + tw + 88, 108), LABEL[kind], font=_font("bold", 34), fill=SOFT,
               anchor="lm")

    d.text((X0, 210), route, font=_fit(d, route, "bold", 88, W - 2 * X0), fill=WHITE)
    if country:
        d.text((X0, 322), country, font=_font("regular", 36), fill=MUTED)

    pf = _font("bold", 136)
    d.text((X0, 520), price, font=pf, fill=GREEN, anchor="ls")
    if usual:
        uf = _font("regular", 58)
        ux = X0 + d.textlength(price, font=pf) + 40
        d.text((ux, 505), usual, font=uf, fill=MUTED, anchor="ls")
        uw = d.textlength(usual, font=uf)
        d.line([(ux - 4, 480), (ux + uw + 4, 480)], fill=MUTED, width=5)

    d.text((X0, 590), info, font=_fit(d, info, "regular", 40, W - 2 * X0),
           fill=SOFT, anchor="ls")

    if back:
        d.text((X0, 638), back, font=_fit(d, back, "regular", 32, W - 2 * X0),
               fill=LILAC, anchor="ls")

    if footnote:
        d.text((X0, H - 40), footnote, font=_font("regular", 26), fill=DIM, anchor="ls")
    sign = C.CHANNEL_SIGN if sign is None else sign
    if sign:
        d.text((W - X0, H - 40), sign, font=_font("bold", 30), fill=LILAC, anchor="rs")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def bars(title, subtitle, days, sign=None):
    """
    График ссылки-отслеживания: столбик на день, сверху — сколько переходов,
    зелёная часть столбика — новые люди. days — [(дата 'YYYY-MM-DD', всего, новых)].
    """
    img = _background()
    d = ImageDraw.Draw(img)
    d.text((X0, 56), title, font=_fit(d, title, "bold", 56, W - 2 * X0), fill=WHITE)
    d.text((X0, 132), subtitle, font=_font("regular", 30), fill=MUTED)

    top, bottom = 210, H - 110
    left, right = X0, W - X0
    peak = max([t for _, t, _ in days] + [1])
    step = (right - left) / max(len(days), 1)
    bar = max(4, step * 0.66)
    small, label = _font("regular", 22), _font("bold", 24)
    d.line([(left, bottom), (right, bottom)], fill=DIM, width=2)
    for i, (day, total, new) in enumerate(days):
        x = left + step * i + (step - bar) / 2
        if total:
            h = (bottom - top) * total / peak
            d.rounded_rectangle([x, bottom - h, x + bar, bottom], radius=min(6, bar / 2),
                                fill=VIOLET)
            if new:
                hn = (bottom - top) * new / peak
                d.rounded_rectangle([x, bottom - hn, x + bar, bottom],
                                    radius=min(6, bar / 2), fill=GREEN)
            d.text((x + bar / 2, bottom - h - 8), str(total), font=label, fill=SOFT,
                   anchor="ms")
        # подпись даты — у сегодняшнего и через каждые семь дней назад
        if (len(days) - 1 - i) % 7 == 0:
            d.text((x + bar / 2, bottom + 30), f"{int(day[8:10])}.{day[5:7]}", font=small,
                   fill=MUTED, anchor="ms")

    lx = X0
    for color, text in ((VIOLET, "переходы"), (GREEN, "новые люди")):
        d.rounded_rectangle([lx, H - 52, lx + 22, H - 30], radius=5, fill=color)
        d.text((lx + 32, H - 34), text, font=_font("regular", 24), fill=SOFT, anchor="ls")
        lx += 32 + d.textlength(text, font=_font("regular", 24)) + 40
    sign = C.CHANNEL_SIGN if sign is None else sign
    if sign:
        d.text((W - X0, H - 34), sign, font=_font("bold", 28), fill=LILAC, anchor="rs")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
