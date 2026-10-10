"""
Аналитика в админке: 📊 статистика канала, 🧾 медиакит и 🔐 подключение
аккаунта статистики (ссылки и закупки рекламы — в handlers/links.py). Всё — только владельцам.

Вход в аккаунт статистики идёт по шагам в чате. Сообщения с ключами, кодом
и паролем бот удаляет сразу после чтения — в переписке они не остаются.
"""
import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import analytics
import keyboards as kb
import tgstats
import ui
from .common import Ask, is_owner, say

router = Router()


def _kb(rows):
    return kb._kb([[kb._b(t, d) for t, d in row] for row in rows])


async def _forget(m: Message):
    """Удалить сообщение человека с секретом (ключ, код, пароль)."""
    try:
        await m.delete()
    except Exception:
        pass


# ---------- статистика и медиакит ----------

@router.callback_query(F.data.startswith("an:stats:"))
async def cb_stats(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    days = int(q.data.split(":")[2])
    await q.answer("Собираю, это до минуты")
    data = await analytics.gather(days)
    png, text = analytics.owner_report(data)
    await q.message.answer_photo(BufferedInputFile(png, "stats.png"))
    await say(q.message, text, _kb([[("За 7 дней", "an:stats:7"), ("За 30 дней", "an:stats:30")],
                                    [("🧾 Медиакит", "an:kit")]]))


@router.callback_query(F.data == "an:kit")
async def cb_kit(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    await q.answer("Собираю медиакит")
    got = analytics.mediakit(await analytics.gather(30))
    if not got:
        await q.message.answer("Для медиакита нужны просмотры постов — подключи аккаунт "
                               "статистики: 🔐 в админке.")
        return
    png, text = got
    await q.message.answer("🧾 Медиакит — перешли рекламодателю следующее сообщение:")
    if len(text) <= 1000:                       # одним сообщением удобнее пересылать
        await q.message.answer_photo(BufferedInputFile(png, "mediakit.png"), caption=text)
    else:
        await q.message.answer_photo(BufferedInputFile(png, "mediakit.png"))
        await q.message.answer(text)


# ---------- аккаунт статистики ----------

@router.callback_query(F.data == "an:acc")
async def cb_account(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    await state.clear()
    await q.answer()
    if tgstats.connected():
        await say(q.message, ui.screen(
            "🔐 <b>Аккаунт статистики</b>",
            f"Подключён: <b>{html.escape(tgstats.account_name() or '')}</b>.",
            "Через него бот видит просмотры, пересылки, реакции и графики канала."),
            _kb([[("🔁 Подключить заново", "an:acc:go"), ("🗑 Отключить", "an:acc:off")]]))
        return
    await say(q.message, ui.screen(
        "🔐 <b>Аккаунт статистики</b>",
        "Просмотры постов и графики Telegram отдаёт только аккаунту-админу канала, "
        "не боту. Нужен <b>отдельный</b> аккаунт — не твой основной: его сессия "
        "будет храниться на сервере.",
        ui.rows_block([
            "1. Заведи отдельный аккаунт и сделай его админом канала.",
            "2. С него зайди на my.telegram.org → API development tools → "
            "создай приложение, возьми <b>api_id</b> и <b>api_hash</b>.",
            "3. Нажми «Подключить» и пришли их по очереди, потом телефон и код.",
        ]),
        "Сообщения с ключами, кодом и паролем удаляю сразу после чтения."),
        _kb([[("🔐 Подключить", "an:acc:go")]]))


@router.callback_query(F.data == "an:acc:off")
async def cb_account_off(q: CallbackQuery):
    if not is_owner(q.from_user.id):
        return
    tgstats.forget()
    await q.answer("Отключён")
    await q.message.answer("🗑 Аккаунт отключён, сессия удалена с сервера. Для надёжности "
                           "заверши её и в самом Telegram: Настройки → Устройства.")


@router.callback_query(F.data == "an:acc:go")
async def cb_account_go(q: CallbackQuery, state: FSMContext):
    if not is_owner(q.from_user.id):
        return
    await state.set_state(Ask.st_api_id)
    await q.answer()
    await q.message.answer("1/4. Пришли <b>api_id</b> — число с my.telegram.org.\n"
                           "Передумал — нажми любую кнопку меню.")


@router.message(Ask.st_api_id)
async def got_api_id(m: Message, state: FSMContext):
    await _forget(m)
    v = (m.text or "").strip()
    if not v.isdigit():
        await m.answer("api_id — это число. Пришли ещё раз.")
        return
    await state.update_data(api_id=int(v))
    await state.set_state(Ask.st_api_hash)
    await m.answer("2/4. Теперь <b>api_hash</b> — строка из 32 букв и цифр.")


@router.message(Ask.st_api_hash)
async def got_api_hash(m: Message, state: FSMContext):
    await _forget(m)
    v = (m.text or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", v):
        await m.answer("api_hash — 32 символа, буквы a–f и цифры. Пришли ещё раз.")
        return
    await state.update_data(api_hash=v)
    await state.set_state(Ask.st_phone)
    await m.answer("3/4. Номер телефона этого аккаунта, например <code>+79001234567</code>.")


@router.message(Ask.st_phone)
async def got_phone(m: Message, state: FSMContext):
    await _forget(m)
    phone = "+" + "".join(ch for ch in (m.text or "") if ch.isdigit())
    if len(phone) < 11:
        await m.answer("Нужен номер целиком, с кодом страны: <code>+79001234567</code>.")
        return
    d = await state.get_data()
    try:
        await tgstats.login_start(m.chat.id, d["api_id"], d["api_hash"], phone)
    except Exception as e:
        await state.clear()
        await m.answer(f"Не вышло запросить код: {html.escape(str(e)[:200])}\n"
                       "Проверь api_id/api_hash и номер и начни заново: 🔐 в админке.")
        return
    await state.set_state(Ask.st_code)
    await m.answer("4/4. Код пришёл в Telegram этого аккаунта. Пришли его <b>через дефисы</b>: "
                   "<code>1-2-3-4-5</code> — без них Telegram решит, что код переслали, "
                   "и отменит вход.")


@router.message(Ask.st_code)
async def got_code(m: Message, state: FSMContext):
    await _forget(m)
    code = "".join(ch for ch in (m.text or "") if ch.isdigit())
    if len(code) < 5:
        await m.answer("Код — 5 цифр, через дефисы: <code>1-2-3-4-5</code>.")
        return
    try:
        res = await tgstats.login_code(m.chat.id, code)
    except Exception as e:
        await state.clear()
        await tgstats.login_cancel(m.chat.id)
        await m.answer(f"Код не подошёл: {html.escape(str(e)[:200])}\nНачни заново: 🔐 в админке.")
        return
    if res == "password":
        await state.set_state(Ask.st_password)
        await m.answer("На аккаунте облачный пароль — пришли его. Сообщение сразу удалю.")
        return
    await state.clear()
    await _done(m)


@router.message(Ask.st_password)
async def got_password(m: Message, state: FSMContext):
    await _forget(m)
    try:
        await tgstats.login_password(m.chat.id, m.text or "")
    except Exception as e:
        await m.answer(f"Пароль не подошёл: {html.escape(str(e)[:150])}. Пришли ещё раз.")
        return
    await state.clear()
    await _done(m)


async def _done(m: Message):
    await say(m, ui.screen("✅ <b>Аккаунт статистики подключён</b>",
                           f"Вошёл как {html.escape(tgstats.account_name() or '')}.",
                           "Теперь в сводке будут просмотры, пересылки и реакции."),
              _kb([[("📊 Статистика за 7 дней", "an:stats:7")]]))
