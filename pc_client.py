from __future__ import annotations

import json
import re

import aiohttp

from config import Config

KEY_PATTERN = re.compile(r"LAZZU-1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
ISSUE_PATH = "/api/license/issue"


class PcIssueError(RuntimeError):
    """LazzuPC could not issue a key for an order."""


async def issue_key_from_pc(cfg: Config, order_id: int, label: str = "") -> str:
    """Ask the local LazzuPC panel to sign a key for a paid order.

    The master key never leaves the PC. The same endpoint is used by /paid,
    /key and the automatic DonationAlerts checker.
    """
    if not cfg.pc_api_configured:
        raise PcIssueError("PC_API_URL или PC_API_TOKEN не настроены")

    url = cfg.pc_api_url.rstrip("/")
    if not url.endswith(ISSUE_PATH):
        url += ISSUE_PATH

    payload = {
        "order_id": order_id,
        "label": label.strip() or f"order-{order_id}",
        "days": 365,
    }
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(
            url,
            json=payload,
            headers={"X-Lazzu-Api-Key": cfg.pc_api_token},
        ) as response:
            raw = await response.text()
            if response.status != 200:
                raise PcIssueError(
                    f"LazzuPC вернул HTTP {response.status}: {raw[:200]}"
                )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PcIssueError("LazzuPC вернул некорректный ответ") from error
    if not data.get("ok") or not data.get("key"):
        raise PcIssueError("LazzuPC не вернул ключ")

    key = str(data["key"]).strip()
    if not KEY_PATTERN.fullmatch(key):
        raise PcIssueError("Получен ключ неверного формата")
    return key
