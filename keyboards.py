from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵Купить ключ + приватка", callback_data="buy")
    builder.button(text="👤Профиль", callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()


def payment_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Оплатить звёздами", callback_data="pay_stars")
    builder.button(text="💳 Оплатить рублями", callback_data="pay_rub")
    builder.button(text="« Назад в меню", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def link_button(text: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]]
    )


def payment_done_menu(order_id: int, donation_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Открыть DonationAlerts", url=donation_url)
    builder.button(text="✅ Я оплатил — проверить", callback_data=f"check:{order_id}")
    builder.button(text="« В меню", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« В меню", callback_data="menu")]]
    )
