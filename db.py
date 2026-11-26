from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    lot          TEXT NOT NULL,
    price        TEXT NOT NULL,
    method       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'awaiting_payment',
    key_value    TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    paid_at      TEXT,
    donation_id  INTEGER,
    invite_link  TEXT,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Простое хранилище пользователей и заявок на покупку."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)
            # Старые базы догоняем до новой структуры без потери данных.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(purchases)")}
            for column, declaration in (
                ("paid_at", "TEXT"),
                ("donation_id", "INTEGER"),
                ("invite_link", "TEXT"),
                ("delivered_at", "TEXT"),
            ):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE purchases ADD COLUMN {column} {declaration}"
                    )

    def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, first_name, now),
            )

    def create_order(self, user_id: int, lot: str, price: str, method: str) -> int:
        now = _now()
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO purchases
                    (user_id, lot, price, method, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'awaiting_payment', ?, ?)
                """,
                (user_id, lot, price, method, now, now),
            )
            return int(cursor.lastrowid)

    def get_user_orders(self, user_id: int) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM purchases
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_user_paid_orders(self, user_id: int) -> list[dict[str, Any]]:
        """Только реально оплаченные покупки — для профиля пользователя."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM purchases
                WHERE user_id = ? AND status IN ('paid', 'key_sent')
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_orders(self, limit: int = 15) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM purchases ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (order_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_paid(self, order_id: int) -> dict[str, Any] | None:
        return self.mark_paid(order_id)

    def mark_paid(
        self,
        order_id: int,
        *,
        donation_id: int | None = None,
        paid_at: str | None = None,
    ) -> dict[str, Any] | None:
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE purchases
                    SET status = 'paid',
                        paid_at = COALESCE(?, ?),
                        donation_id = COALESCE(?, donation_id),
                        updated_at = ?
                    WHERE id = ? AND status = 'awaiting_payment'
                    """,
                    (paid_at, now, donation_id, now, order_id),
                )
            row = conn.execute(
                "SELECT * FROM purchases WHERE id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def mark_delivered(self, order_id: int, invite_link: str) -> dict[str, Any] | None:
        now = _now()
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE purchases
                    SET invite_link = ?, delivered_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (invite_link, now, now, order_id),
                )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM purchases WHERE id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_key(self, order_id: int, key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE purchases
                SET key_value = ?, status = 'key_sent', updated_at = ?
                WHERE id = ?
                """,
                (key, _now(), order_id),
            )
            if cursor.rowcount == 0:
                return None

            order = conn.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (order_id,),
            ).fetchone()
            return dict(order) if order else None
