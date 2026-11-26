from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message

from config import Config
from db import Store
from delivery import deliver_order
from donations import check_order, check_pending_orders
from keyboards import back_to_menu, link_button, main_menu, payment_done_menu, payment_menu
from pc_client import issue_key_from_pc

log = logging.getLogger("lazzu_bot")

cfg = Config.from_env()
store = Store(cfg.db_path)

try:
    session = AiohttpSession(proxy=cfg.proxy_url) if cfg.proxy_url else AiohttpSession()
except RuntimeError as error:
    raise SystemExit(
        f"Проблема с PROXY_URL: {error}. Для SOCKS-прокси установи aiohttp-socks "
        "или убери PROXY_URL из .env."
    ) from error

bot = Bot(
    token=cfg.bot_token,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

METHOD_LABELS = {
    "stars": "⭐ Звёзды",
    "rub": "💳 Рубли",
}
STATUS_LABELS = {
    "awaiting_payment": "Ожидает оплаты",
    "paid": "Оплачено",
    "key_sent": "Ключ выдан",
}


def _fmt_date(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return raw


def _order_line(order: dict) -> str:
    method = METHOD_LABELS.get(order["method"], order["method"])
    status = STATUS_LABELS.get(order["status"], order["status"])
    paid = _fmt_date(order.get("paid_at")) if order.get("paid_at") else "—"
    line = (
        f"• <b>№{order['id']}</b> — {html.escape(order['lot'])}\n"
        f"  {html.escape(order['price'])} | {method} | статус: {status}\n"
        f"  Оплачено: {paid}"
    )
    if order.get("key_value"):
        line += f"\n  🔑 <code>{html.escape(order['key_value'])}</code>"
    return line


def _profile_text(user) -> str:
    username = f"@{html.escape(user.username)}" if user.username else "не указан"
    name = html.escape(user.first_name or "без имени")

    orders = store.get_user_paid_orders(user.id)
    history = (
        "\n\n".join(_order_line(order) for order in orders)
        if orders
        else "Оплаченных покупок пока нет."
    )

    return (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {name}\n"
        f"Юзернейм: {username}\n"
        f"ID: <code>{user.id}</code>\n\n"
        "<b>История покупок (только оплаченные):</b>\n"
        f"{history}\n\n"
        "<i>Здесь не показываются заявки, у которых оплата ещё не подтверждена.</i>"
    )


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=reply_markup
        )
        return

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


async def _is_admin(message: Message) -> bool:
    if cfg.admin_ids and message.from_user and message.from_user.id in cfg.admin_ids:
        return True
    await message.answer("Команда доступна только администратору.")
    return False


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    store.upsert_user(user.id, user.username, user.first_name)
    await message.answer(
        "👋 Привет! Здесь можно купить ключ для LazzuApp.\n"
        "Приватку после оплаты выдаст продавец.\n\n"
        "Выбери действие:",
        reply_markup=main_menu(),
    )


@dp.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    user = message.from_user
    store.upsert_user(user.id, user.username, user.first_name)
    await message.answer(_profile_text(user))


@dp.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(callback, "Выбери действие:", main_menu())


@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    store.upsert_user(user.id, user.username, user.first_name)
    await _safe_edit(callback, _profile_text(user), back_to_menu())


@dp.callback_query(F.data == "buy")
async def cb_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        "🎁 <b>Лот:</b> "
        f"{html.escape(cfg.lot_name)}\n"
        f"💰 <b>Цена:</b> {html.escape(cfg.lot_price)}\n\n"
        "Выбери способ оплаты:",
        payment_menu(),
    )


async def _handle_payment(callback: CallbackQuery, method: str) -> None:
    user = callback.from_user
    store.upsert_user(user.id, user.username, user.first_name)
    order_id = store.create_order(user.id, cfg.lot_name, cfg.lot_price, method)

    if method == "stars":
        url = cfg.stars_url
        button_text = "💬 Открыть чат с продавцом"
    else:
        if not cfg.donation_configured:
            await callback.answer(
                "Администратор ещё не указал ссылку DonationAlerts.",
                show_alert=True,
            )
            return
        url = cfg.donation_url
        button_text = "💳 Открыть DonationAlerts"

    # Пытаемся открыть ссылку сразу после нажатия. Если клиент не поддерживает
    # такой ответ, ниже всегда остаётся обычная кнопка со ссылкой.
    try:
        await callback.answer(url=url)
    except TelegramBadRequest:
        await callback.answer()

    if method == "rub":
        text = (
            f"✅ Заявка №{order_id} сохранена.\n\n"
            "Переведи оплату через DonationAlerts и обязательно напиши в комментарии "
            f"номер заявки: <code>#{order_id}</code>.\n"
            f"Сумма: от {cfg.donation_min_amount:g} ₽.\n\n"
            "После оплаты нажми «Я оплатил — проверить». Если платёж не найдётся сразу, "
            "повтори через минуту — проверка идёт автоматически."
        )
        markup = payment_done_menu(order_id, url)
    else:
        text = (
            f"✅ Заявка №{order_id} сохранена.\n"
            f"Способ оплаты: {METHOD_LABELS[method]}.\n\n"
            "Если чат не открылся автоматически — нажми кнопку ниже."
        )
        markup = link_button(button_text, url)

    await _safe_edit(callback, text, markup)


@dp.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: CallbackQuery) -> None:
    await _handle_payment(callback, "stars")


@dp.callback_query(F.data == "pay_rub")
async def cb_pay_rub(callback: CallbackQuery) -> None:
    await _handle_payment(callback, "rub")


@dp.callback_query(F.data.startswith("check:"))
async def cb_check_payment(callback: CallbackQuery) -> None:
    await callback.answer("Проверяю оплату...")
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    ok, message = await check_order(callback.bot, cfg, store, order_id)
    if ok:
        await _safe_edit(
            callback,
            f"✅ {message}\n\nПосмотреть покупки можно в профиле.",
            back_to_menu(),
        )
    else:
        await _safe_edit(callback, f"⏳ {message}", back_to_menu())


@dp.message(Command("orders"))
async def cmd_orders(message: Message) -> None:
    if not await _is_admin(message):
        return

    orders = store.get_recent_orders(15)
    if not orders:
        await message.answer("Заявок пока нет.")
        return

    lines = [
        f"№{order['id']} | user {order['user_id']} | "
        f"{METHOD_LABELS.get(order['method'], order['method'])} | "
        f"{STATUS_LABELS.get(order['status'], order['status'])} | "
        f"{_fmt_date(order['created_at'])}"
        for order in orders
    ]
    await message.answer(
        "<b>Последние заявки:</b>\n" + "\n".join(html.escape(line) for line in lines)
    )


@dp.message(Command("paid"))
async def cmd_paid(message: Message) -> None:
    if not await _is_admin(message):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /paid <номер заявки>")
        return

    order = store.set_paid(int(parts[1]))
    if not order:
        await message.answer("Заявка не найдена.")
        return

    issue_note = ""
    if cfg.pc_api_configured:
        try:
            key = await issue_key_from_pc(
                cfg, int(order["id"]), str(order.get("lot") or "")
            )
            store.set_key(int(order["id"]), key)
            issue_note = "Ключ создан LazzuPC. "
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as error:
            log.warning("Не удалось получить ключ с ПК для заказа #%s: %s", order["id"], error)
            issue_note = f"Ключ с ПК не получен: {error}. Повторите /key {order['id']}. "

    ok, result = await deliver_order(message.bot, cfg, store, int(order["id"]))
    result = issue_note + result
    status = "Выдача выполнена." if ok else f"Выдача не выполнена: {result}"
    await message.answer(f"Заявка №{order['id']} отмечена как оплаченная. {status}")


@dp.message(Command("key"))
async def cmd_key(message: Message) -> None:
    if not await _is_admin(message):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) not in (2, 3) or not parts[1].isdigit():
        usage = "Использование: /key <номер заявки>"
        if not cfg.pc_api_configured:
            usage += " <ключ>"
        await message.answer(usage)
        return

    order_id = int(parts[1])
    order = store.get_order(order_id)
    if not order:
        await message.answer("Заявка не найдена.")
        return
    if order["status"] == "awaiting_payment":
        await message.answer("Сначала подтвердите оплату: /paid <номер заявки>")
        return

    if len(parts) == 3:
        key = parts[2].strip()
    else:
        if not cfg.pc_api_configured:
            await message.answer(
                "Автоматическая выдача не настроена. Укажите ключ вручную: "
                "/key <номер заявки> <ключ>"
            )
            return
        try:
            key = await issue_key_from_pc(
                cfg, order_id, str(order.get("lot") or "")
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as error:
            await message.answer(
                f"Не удалось получить ключ с ПК: {error}. "
                "Проверьте, включён ли LazzuPC и актуальны ли PC_API_URL / PC_API_TOKEN."
            )
            return

    stored = store.set_key(order_id, key)
    if not stored:
        await message.answer("Заявка не найдена.")
        return

    lines = [
        "🔑 <b>Твой ключ:</b>",
        f"<code>{html.escape(stored['key_value'])}</code>",
        "",
        "Скопируй его целиком и вставь в LazzuApp.",
    ]
    await message.answer(f"Ключ по заявке №{order_id} выдан покупателю.")
    await message.bot.send_message(stored["user_id"], "\n".join(lines))


async def donation_watcher() -> None:
    while True:
        try:
            await check_pending_orders(bot, cfg, store)
        except Exception:
            log.exception("Ошибка фоновой проверки DonationAlerts")
        await asyncio.sleep(60)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="profile", description="Профиль и покупки"),
            ]
        )
    except (TelegramNetworkError, TelegramBadRequest) as error:
        log.warning("Не удалось обновить список команд: %s", error)

    try:
        me = await bot.get_me()
        log.info("Бот: @%s (ID %s)", me.username, me.id)
    except TelegramNetworkError:
        pass

    log.info("Бот запущен. БД: %s", cfg.db_path)
    watcher = asyncio.create_task(donation_watcher())
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as error:
        log.error("Нет связи с api.telegram.org: %s", error)
        log.error("Проверь интернет, VPN или прокси — бот без связи не работает.")
        raise SystemExit(1) from None
    finally:
        watcher.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
