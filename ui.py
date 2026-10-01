"""
Оформление сообщений: единый вид экранов и два стиля подачи.

Текст собирается одинаково для обоих стилей — заголовок, разделитель, блоки
через пустую строку, подвал. Отличается только способ показа:

  «старый»  — обычное сообщение, кнопки под ним. Работает везде.
  «новый»   — rich-сообщение Bot API: заголовок становится настоящим
              заголовком, разделители — линиями, кнопки уезжают внутрь
              сообщения. Выглядит опрятнее, но это новый тип сообщения,
              и не всякий клиент его покажет — поэтому стиль переключаемый.

Разметка в исходном тексте простая, тех же тегов хватает и Telegram, и
конвертеру: <b>, <i>, <s>, <code>, <a href>, <blockquote>.
"""
import asyncio
import re
import time
from html.parser import HTMLParser

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           InputRichBlockBlockQuotation, InputRichBlockButtons,
                           InputRichBlockDivider, InputRichBlockParagraph,
                           InputRichBlockSectionHeading, InputRichMessage,
                           RichMessageButton, RichTextBold, RichTextCode,
                           RichTextItalic, RichTextStrikethrough, RichTextUrl)

SEPARATOR = "━━━━━━━━━━━━━━━"
HEADING_SIZE = 2          # размер заголовка секции в rich-сообщении

OLD, NEW = "old", "new"


# ---------- сборка текста ----------

def screen(title, *blocks, footer=None):
    """
    Экран: заголовок, линия, блоки через пустую строку, подвал за линией.

    Пустые блоки пропускаются — чтобы вызывающий код не проверял каждый
    кусок на None и не плодил лишних переносов.
    """
    parts = [f"{title}\n{SEPARATOR}"] if title else []
    parts += [b for b in blocks if b]
    if footer:
        parts.append(f"{SEPARATOR}\n{footer}")
    return "\n\n".join(parts)


def rows_block(lines):
    """Несколько строк одним блоком: они про одно и то же."""
    return "\n".join(l for l in lines if l)


# ---------- текст -> rich-блоки ----------

_INLINE = {"b": RichTextBold, "code": RichTextCode, "i": RichTextItalic,
           "s": RichTextStrikethrough}      # зачёркнутая «обычная» цена
# Заголовок экрана: ровно одна жирная вставка и обычный текст вокруг,
# например «✈️ <b>Казань → Сочи</b>».
_TITLE_RE = re.compile(r"^[^<]*<b>[^<]*</b>[^<]*$")
_QUOTE_RE = re.compile(r"<blockquote>(.*?)</blockquote>", re.DOTALL)


class _Inline(HTMLParser):
    """Разбирает строку с нашей разметкой в rich-текст."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack = [[]]
        self._href = []

    def handle_starttag(self, tag, attrs):
        if tag in _INLINE:
            self._stack.append([])
        elif tag == "a":
            self._stack.append([])
            self._href.append(dict(attrs).get("href", ""))

    def handle_endtag(self, tag):
        if tag in _INLINE and len(self._stack) > 1:
            kids = self._stack.pop()
            self._stack[-1].append(_INLINE[tag](text=_one(kids)))
        elif tag == "a" and len(self._stack) > 1:
            kids = self._stack.pop()
            href = self._href.pop() if self._href else ""
            self._stack[-1].append(RichTextUrl(text=_one(kids), url=href))

    def handle_data(self, data):
        if data:
            self._stack[-1].append(data)

    def result(self):
        return _one(self._stack[0])


def _one(items):
    if not items:
        return ""
    return items[0] if len(items) == 1 else items


def _inline(line):
    p = _Inline()
    p.feed(line)
    p.close()
    return p.result()


def blocks_from_text(text):
    """
    Текст экрана -> список rich-блоков.

    Первая строка, если она целиком заголовок, становится заголовком секции,
    и следующий за ней разделитель убирается — линию заголовок рисует сам.
    Каждая линия SEPARATOR — разделитель, цитата — своим блоком,
    остальное — по абзацу на группу строк между пустыми.
    """
    blocks = []
    title_done = False
    skip_sep = False

    for chunk in text.split("\n\n"):
        for piece in _split_quotes(chunk):
            kind, body = piece
            if kind == "quote":
                # цитата состоит из блоков, а не из текста: внутрь кладём абзац
                blocks.append(InputRichBlockBlockQuotation(
                    blocks=[InputRichBlockParagraph(text=_inline(body))]))
                continue

            lines, keep = body.split("\n"), []
            for line in lines:
                if line.strip() == SEPARATOR:
                    if keep:
                        blocks.append(InputRichBlockParagraph(
                            text=_inline("\n".join(keep))))
                        keep = []
                    if skip_sep:
                        skip_sep = False
                        continue
                    blocks.append(InputRichBlockDivider())
                    continue
                if not title_done and line.strip() and _TITLE_RE.match(line.strip()):
                    blocks.append(InputRichBlockSectionHeading(
                        text=_inline(line.strip()), size=HEADING_SIZE))
                    title_done, skip_sep = True, True
                    continue
                if line.strip():
                    keep.append(line)
            if keep:
                blocks.append(InputRichBlockParagraph(text=_inline("\n".join(keep))))
    return blocks


def _split_quotes(chunk):
    """Разрезает кусок на обычные части и цитаты, сохраняя порядок."""
    out, pos = [], 0
    for m in _QUOTE_RE.finditer(chunk):
        if m.start() > pos:
            out.append(("text", chunk[pos:m.start()]))
        out.append(("quote", m.group(1).strip()))
        pos = m.end()
    if pos < len(chunk):
        out.append(("text", chunk[pos:]))
    return [(k, v) for k, v in out if v.strip()]


# ---------- кнопки ----------

def is_url(target):
    return target.startswith(("http://", "https://"))


def keyboard(rows):
    """[[(подпись, callback или url), ...], ...] -> обычная клавиатура."""
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, url=x) if is_url(x)
         else InlineKeyboardButton(text=t, callback_data=x) for t, x in row]
        for row in rows])


def button_blocks(rows):
    """Те же кнопки, но внутрь сообщения."""
    return [InputRichBlockButtons(buttons=[
        RichMessageButton(text=t, url=x) if is_url(x)
        else RichMessageButton(text=t, callback_data=x) for t, x in row])
        for row in rows]


def rows_from_markup(markup):
    """Готовую InlineKeyboardMarkup обратно в пары — чтобы не дублировать код."""
    if not markup:
        return []
    return [[(b.text, b.url or b.callback_data) for b in row]
            for row in markup.inline_keyboard]


# ---------- отправка ----------

async def send(message, text, markup=None, style=NEW):
    """
    Отправляет экран в выбранном стиле.

    Новый стиль складывает кнопки внутрь сообщения; если Telegram его почему-то
    не принял, молча откатываемся на обычное сообщение — пусть лучше выглядит
    проще, чем не придёт вовсе.
    """
    if style != NEW:
        return await message.answer(text, reply_markup=markup)
    try:
        blocks = blocks_from_text(text)
        inline = markup if isinstance(markup, InlineKeyboardMarkup) else None
        rows = rows_from_markup(inline)
        if rows:
            blocks.append(InputRichBlockDivider())
            blocks += button_blocks(rows)
        # нижняя клавиатура — не инлайн-кнопки, внутрь сообщения её не убрать,
        # поэтому она едет как обычно
        return await message.answer_rich(
            rich_message=InputRichMessage(blocks=blocks),
            reply_markup=None if inline else markup)
    except Exception as e:
        print(f"  rich не отправился ({e}), шлю обычным")
        return await message.answer(text, reply_markup=markup)


async def edit(message, text, markup=None, style=NEW):
    """
    Заменить показанный экран.

    Telegram не даёт превратить обычное сообщение в rich и наоборот, да и
    правка чужого типа падает — поэтому на любой отказ просто шлём новое.
    """
    try:
        if style != NEW:
            return await message.edit_text(text, reply_markup=markup)
        blocks = blocks_from_text(text)
        inline = markup if isinstance(markup, InlineKeyboardMarkup) else None
        rows = rows_from_markup(inline)
        if rows:
            blocks.append(InputRichBlockDivider())
            blocks += button_blocks(rows)
        return await message.edit_text(
            rich_message=InputRichMessage(blocks=blocks),
            reply_markup=None if inline else markup)
    except Exception:
        return await send(message, text, markup, style)


class Progress:
    """
    Живой индикатор работы: самолётик летит, счётчик тикает.

    Нужен потому, что опрос источников занимает до минуты, и статичная строка
    «ищу…» всё это время неотличима от зависшего бота. Индикатор делает две
    вещи сразу: правит своё сообщение раз в пару секунд и держит «печатает»
    в шапке чата — это две разные подсказки, и вместе они убедительнее.

    Сообщение намеренно обычное, не rich: его придётся переписывать десятки
    раз, и лишние блоки тут ни к чему.
    """

    TRACK = 10                 # длина полосы, по которой летит самолёт
    TICK = 2.0                 # как часто перерисовывать, сек
    ACTION_EVERY = 4.0         # «печатает» живёт около пяти секунд

    def __init__(self, message, title="🔎 <b>Ищу билеты</b>"):
        self._message = message
        self._title = title
        self._label = ""
        self._note = None
        self._task = None
        self._started = 0.0

    def _frame(self, i):
        pos = i % (self.TRACK + 1)
        track = "·" * pos + "✈️" + "·" * (self.TRACK - pos)
        secs = int(time.monotonic() - self._started)
        tail = f"{self._label} · {secs} с" if self._label else f"{secs} с"
        return f"{self._title}\n{SEPARATOR}\n{track}\n<i>{tail}</i>"

    async def _run(self):
        # начинаем со второго кадра: первый уже отрисован при отправке,
        # и повтор Telegram всё равно отклонит как «сообщение не изменено»
        i, last_action = 1, 0.0
        while True:
            now = time.monotonic()
            if now - last_action > self.ACTION_EVERY:
                last_action = now
                try:
                    await self._message.bot.send_chat_action(
                        self._message.chat.id, "typing")
                except Exception:
                    pass
            try:
                await self._note.edit_text(self._frame(i))
            except Exception:
                pass          # «сообщение не изменено» и прочие мелочи не важны
            i += 1
            await asyncio.sleep(self.TICK)

    async def start(self, label=""):
        self._label = label
        self._started = time.monotonic()
        self._note = await self._message.answer(self._frame(0))
        self._task = asyncio.create_task(self._run())
        return self

    def step(self, label):
        """Обновить подпись. Перерисует ближайший же кадр."""
        self._label = label

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._note:
            try:
                await self._note.delete()
            except Exception:
                pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.stop()
        return False


async def push(bot, chat_id, text, markup=None, style=NEW):
    """То же самое, но для рассылки, где нет входящего сообщения."""
    if style != NEW:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    try:
        blocks = blocks_from_text(text)
        inline = markup if isinstance(markup, InlineKeyboardMarkup) else None
        rows = rows_from_markup(inline)
        if rows:
            blocks.append(InputRichBlockDivider())
            blocks += button_blocks(rows)
        return await bot.send_rich_message(
            chat_id, rich_message=InputRichMessage(blocks=blocks),
            reply_markup=None if inline else markup)
    except Exception as e:
        print(f"  rich не отправился ({e}), шлю обычным")
        return await bot.send_message(chat_id, text, reply_markup=markup)
