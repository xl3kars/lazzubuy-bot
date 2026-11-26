from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    """Минимальный загрузчик .env без лишних зависимостей."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    seller_username: str
    donation_url: str
    stars_message: str
    lot_name: str
    lot_price: str
    admin_ids: frozenset[int]
    db_path: Path
    proxy_url: str
    donation_widget_token: str
    donation_token: str
    donation_refresh_token: str
    donation_client_id: str
    donation_client_secret: str
    donation_min_amount: float
    key_value: str
    pc_api_url: str
    pc_api_token: str

    @property
    def pc_api_configured(self) -> bool:
        return bool(self.pc_api_url.strip()) and bool(self.pc_api_token.strip())

    @property
    def stars_url(self) -> str:
        username = self.seller_username.lstrip("@").strip() or "eqaho"
        return f"https://t.me/{username}?text={quote(self.stars_message, safe='')}"

    @property
    def donation_configured(self) -> bool:
        return bool(self.donation_url.strip()) and "YOUR_LOGIN" not in self.donation_url.upper()

    @property
    def donation_check_enabled(self) -> bool:
        return bool(
            self.donation_widget_token.strip()
            or self.donation_token.strip()
            or (
                self.donation_refresh_token.strip()
                and self.donation_client_id.strip()
                and self.donation_client_secret.strip()
            )
        )

    @classmethod
    def from_env(cls) -> Config:
        _load_env()

        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "Не задан BOT_TOKEN. Запусти бота через 'Запустить.bat' (или python run.py) "
                "— он сам создаст .env и спросит токен."
            )

        admin_ids = frozenset(
            int(chunk.strip())
            for chunk in os.getenv("ADMIN_IDS", "").split(",")
            if chunk.strip().isdigit()
        )

        db_raw = os.getenv("DB_PATH", "").strip()
        db_path = Path(db_raw) if db_raw else BASE_DIR / "bot_data.sqlite3"

        amount_raw = os.getenv("DONATION_MIN_AMOUNT", "50").strip().replace(",", ".")
        try:
            min_amount = float(amount_raw)
        except ValueError:
            min_amount = 50.0

        return cls(
            bot_token=token,
            seller_username=os.getenv("SELLER_USERNAME", "eqaho").strip(),
            donation_url=os.getenv("DONATION_ALERTS_URL", "").strip(),
            stars_message=os.getenv(
                "STARS_MESSAGE",
                "Привет, я купить ключ и приватку за везды",
            ).strip(),
            lot_name=os.getenv("LOT_NAME", "Ключ + приватка LazzuApp").strip(),
            lot_price=os.getenv("LOT_PRICE", "25 звёзд / 50 рублей").strip(),
            admin_ids=admin_ids,
            db_path=db_path,
            proxy_url=(
                os.getenv("PROXY_URL", "").strip()
                or os.getenv("HTTPS_PROXY", "").strip()
                or os.getenv("https_proxy", "").strip()
            ),
            donation_widget_token=os.getenv("DONATION_WIDGET_TOKEN", "").strip(),
            donation_token=os.getenv("DONATION_ALERTS_TOKEN", "").strip(),
            donation_refresh_token=os.getenv("DONATION_REFRESH_TOKEN", "").strip(),
            donation_client_id=os.getenv("DONATION_CLIENT_ID", "").strip(),
            donation_client_secret=os.getenv("DONATION_CLIENT_SECRET", "").strip(),
            donation_min_amount=min_amount,
            key_value=os.getenv("LICENSE_KEY", "").strip(),
            pc_api_url=os.getenv("PC_API_URL", "").strip().rstrip("/"),
            pc_api_token=os.getenv("PC_API_TOKEN", "").strip(),
        )
