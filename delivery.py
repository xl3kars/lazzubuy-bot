from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import Config
from db import Store

log = logging.getLogger("lazzu_delivery")


async def deliver_order(
    bot: Bot,
    cfg: Config,
    store: Store,
    order_id: int,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    """После оплаты отправляет покупателю только ключ (без ссылки на приватку)."""
    order: dict[str, Any] | None = store.get_order(order_id)
    if not order:
        return False, "Заявка не найдена."

    if order["status"] == "awaiting_payment":
        return False, "Оплата ещё не подтверждена."

    if order.get("delivered_at") and not force:
        return True, "Выдача уже была — ключ сохранён в профиле."

    key_value = str(order.get("key_value") or cfg.key_value or "").strip()
    if not key_value:
        key_value = "Ключ добавится сюда, когда продавец его настроит."

    lines = [
        f"✅ <b>Заказ №{order_id} оплачен</b>",
        "",
        "🔑 <b>Твой ключ:</b>",
        f"<code>{html.escape(key_value)}</code>",
        "",
        "Скопируй его целиком и вставь в LazzuApp.",
    ]

    try:
        await bot.send_message(order["user_id"], "\n".join(lines))
    except TelegramAPIError as error:
        log.warning("Не удалось отправить ключ по заказу #%s: %s", order_id, error)
        return False, "Не удалось написать тебе в Telegram. Напиши боту /start и попробуй ещё раз."

    store.mark_delivered(order_id, "")
    return True, "Ключ отправлен."
