from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from bot import bot, cfg, dp, store
from donations import check_pending_orders

BASE_DIR = Path(__file__).resolve().parent
OFFSET_FILE = BASE_DIR / "offset.txt"
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))

log = logging.getLogger("lazzu_cloud")


def read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    OFFSET_FILE.write_text(str(value), encoding="utf-8")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    offset = read_offset()
    started = time.monotonic()
    deadline = started + RUN_SECONDS
    next_donation_check = started
    log.info("Облачный опрос запущен на %s секунд, offset=%s", RUN_SECONDS, offset)

    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_donation_check:
                paid = await check_pending_orders(bot, cfg, store)
                if paid:
                    log.info("Оплата подтверждена по заявкам: %s", paid)
                next_donation_check = time.monotonic() + 60

            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message", "callback_query"],
            )
            for update in updates:
                await dp.feed_update(bot, update)
                offset = max(offset, update.update_id + 1)
                write_offset(offset)
    finally:
        await bot.session.close()

    log.info("Облачный опрос завершён, сохранён offset=%s", offset)


if __name__ == "__main__":
    asyncio.run(main())
