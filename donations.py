from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiogram import Bot

from config import Config
from db import Store
from delivery import deliver_order
from pc_client import issue_key_from_pc

log = logging.getLogger("lazzu_donations")

API_URL = "https://www.donationalerts.com/api/v1/alerts/donations"
TOKEN_URL = "https://www.donationalerts.com/oauth/token"
WIDGET_URL = "https://www.donationalerts.com/api/getwidgetdata"

ID_PATTERNS = (
    re.compile(r"#(\d{1,6})\b"),
    re.compile(r"заказ\s*[№#]?\s*(\d{1,6})\b", re.IGNORECASE),
    re.compile(r"order\s*#?\s*(\d{1,6})\b", re.IGNORECASE),
)


class DonationError(Exception):
    """Ошибка проверки платежей DonationAlerts."""


async def _issue_pc_key(cfg: Config, store: Store, order_id: int, lot: str) -> str:
    """Issue a key from LazzuPC as soon as a payment is detected."""
    if not cfg.pc_api_configured:
        return ""
    try:
        key = await issue_key_from_pc(cfg, order_id, lot)
    except Exception as error:  # noqa: BLE001 - payment flow must not break
        log.warning("Не удалось получить ключ с ПК для заказа #%s: %s", order_id, error)
        return ""
    result = store.set_key(order_id, key)
    return key if result else ""


def _extract_order_ids(text: str) -> set[int]:
    found: set[int] = set()
    for pattern in ID_PATTERNS:
        found.update(int(match.group(1)) for match in pattern.finditer(text or ""))
    stripped = (text or "").strip()
    if stripped.isdigit():
        found.add(int(stripped))
    return found


def _parse_dt(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_widget_alerts(alerts: Any) -> list[dict[str, Any]]:
    """Приводит донаты из виджета к полям официального API."""
    result: list[dict[str, Any]] = []
    for item in alerts if isinstance(alerts, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            amount = float(
                item.get("amount_main")
                if item.get("amount_main") is not None
                else item.get("amount") or 0
            )
        except (TypeError, ValueError):
            amount = 0.0
        result.append(
            {
                "id": item.get("id"),
                "username": item.get("username"),
                "message": item.get("message") or "",
                "amount": amount,
                "currency": item.get("currency") or "RUB",
                "created_at": item.get("date_created") or item.get("created_at"),
            }
        )
    return result


async def fetch_widget_donations(token: str) -> list[dict[str, Any]]:
    """Донаты из виджета — нужен только Secret token из кабинета DonationAlerts."""
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(WIDGET_URL, params={"token": token}) as response:
                if response.status != 200:
                    raise DonationError(
                        f"DonationAlerts вернул ошибку {response.status} при чтении виджета."
                    )
                raw = (await response.text()).strip()
    except aiohttp.ClientError as error:
        raise DonationError(f"Не удалось связаться с DonationAlerts: {error}") from error

    # Ответ может быть как JSON, так и JSONP вида ({...});
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip().rstrip(";")

    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise DonationError(
            "DonationAlerts не принял Secret token: вместо данных донатов пришла "
            "веб-страница. Проверь DONATION_WIDGET_TOKEN."
        ) from error

    if str(payload.get("status", "success")).lower() == "error":
        raise DonationError(
            f"DonationAlerts отклонил запрос: {payload.get('message') or 'проверь Secret token'}."
        )
    return _normalize_widget_alerts(payload.get("alerts") or payload.get("data") or [])


async def _access_token(cfg: Config) -> str:
    """Если есть refresh-токен — каждый запуск получает свежий access token."""
    if not (
        cfg.donation_refresh_token
        and cfg.donation_client_id
        and cfg.donation_client_secret
    ):
        return cfg.donation_token

    timeout = aiohttp.ClientTimeout(total=20)
    data = {
        "grant_type": "refresh_token",
        "client_id": cfg.donation_client_id,
        "client_secret": cfg.donation_client_secret,
        "refresh_token": cfg.donation_refresh_token,
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(TOKEN_URL, data=data) as response:
                if response.status != 200:
                    raise DonationError(
                        f"Не удалось обновить токен DonationAlerts ({response.status})."
                    )
                payload = await response.json(content_type=None)
    except aiohttp.ClientError as error:
        raise DonationError(f"Не удалось обновить токен DonationAlerts: {error}") from error

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise DonationError("DonationAlerts не вернул access_token.")
    return token


async def fetch_donations(cfg: Config, pages: int = 2) -> list[dict[str, Any]]:
    if cfg.donation_widget_token.strip():
        return await fetch_widget_donations(cfg.donation_widget_token)

    token = await _access_token(cfg)
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    donations: list[dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for page in range(1, pages + 1):
            try:
                async with session.get(API_URL, params={"page": page}) as response:
                    if response.status == 401:
                        raise DonationError(
                            "DonationAlerts отказал в доступе — проверь токен и право "
                            "oauth-donation-index."
                        )
                    if response.status != 200:
                        raise DonationError(
                            f"DonationAlerts вернул ошибку {response.status}."
                        )
                    payload = await response.json(content_type=None)
            except aiohttp.ClientError as error:
                raise DonationError(f"Не удалось связаться с DonationAlerts: {error}") from error

            donations.extend(payload.get("data") or [])
            if not (payload.get("links") or {}).get("next"):
                break
    return donations


def _matches(order: dict[str, Any], donation: dict[str, Any], min_amount: float) -> bool:
    if str(donation.get("currency", "")).upper() != "RUB":
        return False

    try:
        amount = float(donation.get("amount") or 0)
    except (TypeError, ValueError):
        return False
    if amount + 0.001 < min_amount:
        return False

    message = str(donation.get("message") or "")
    if order["id"] not in _extract_order_ids(message):
        return False

    donation_dt = _parse_dt(donation.get("created_at"))
    order_dt = _parse_dt(order.get("created_at"))
    if donation_dt and order_dt and donation_dt < order_dt - timedelta(minutes=10):
        return False
    return True


async def check_order(
    bot: Bot,
    cfg: Config,
    store: Store,
    order_id: int,
    *,
    donations: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Проверяет конкретную заявку и при находке выдаёт товар."""
    if not cfg.donation_check_enabled:
        return (
            False,
            "Проверка оплаты пока не настроена. "
            "Продавцу нужно указать DONATION_WIDGET_TOKEN в настройках бота.",
        )

    order = store.get_order(order_id)
    if not order:
        return False, "Заявка не найдена."
    if order["status"] != "awaiting_payment":
        return True, "Эта заявка уже оплачена — выдача в профиле."

    try:
        items = donations if donations is not None else await fetch_donations(cfg)
    except DonationError as error:
        return False, str(error)

    for donation in items:
        if not _matches(order, donation, cfg.donation_min_amount):
            continue

        paid_at = _parse_dt(donation.get("created_at"))
        store.mark_paid(
            order_id,
            donation_id=int(donation.get("id") or 0) or None,
            paid_at=paid_at.isoformat() if paid_at else None,
        )
        await _issue_pc_key(cfg, store, order_id, str(order.get("lot") or ""))
        ok, delivery_message = await deliver_order(bot, cfg, store, order_id)
        if ok:
            return True, f"Оплата найдена! {delivery_message}"
        return True, f"Оплата найдена, но выдача не удалась: {delivery_message}"

    return False, (
        "Платёж пока не найден. Убедись, что в комментарии к донату указан "
        f"номер заявки #<b>{order_id}</b> и сумма не меньше {cfg.donation_min_amount:g} ₽. "
        "После оплаты нажми кнопку ещё раз — или подожди до 5 минут."
    )


async def check_pending_orders(bot: Bot, cfg: Config, store: Store) -> list[int]:
    """Фоновая проверка всех неоплаченных рублёвых заявок."""
    if not cfg.donation_check_enabled:
        return []

    orders = [
        order
        for order in store.get_recent_orders(100)
        if order["status"] == "awaiting_payment" and order["method"] == "rub"
    ]
    if not orders:
        return []

    try:
        donations = await fetch_donations(cfg)
    except DonationError as error:
        log.warning("Проверка DonationAlerts не удалась: %s", error)
        return []

    paid: list[int] = []
    used: set[int] = set()
    for order in orders:
        for donation in donations:
            donation_id = int(donation.get("id") or 0)
            if donation_id in used or not _matches(order, donation, cfg.donation_min_amount):
                continue
            used.add(donation_id)

            paid_at = _parse_dt(donation.get("created_at"))
            store.mark_paid(
                order["id"],
                donation_id=donation_id or None,
                paid_at=paid_at.isoformat() if paid_at else None,
            )
            await _issue_pc_key(
                cfg, store, int(order["id"]), str(order.get("lot") or "")
            )
            ok, message = await deliver_order(bot, cfg, store, order["id"])
            if ok:
                paid.append(order["id"])
            else:
                log.warning("Выдача заказа #%s: %s", order["id"], message)
            break
    return paid
