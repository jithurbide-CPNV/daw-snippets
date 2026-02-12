from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SESSION_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class MemberSession:
    user_id: int
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            email_verified INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(derived).decode()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt_b64, derived_b64 = hashed.split(":", 1)
        salt = base64.b64decode(salt_b64.encode())
        expected = base64.b64decode(derived_b64.encode())
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(computed, expected)


def build_member_cookie(secret: str, user_id: int) -> str:
    expires_at = _now() + SESSION_TTL
    payload = f"{user_id}|{int(expires_at.timestamp())}"
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}|{signature}"


def parse_member_cookie(secret: str, cookie_value: str) -> MemberSession | None:
    if not cookie_value:
        return None
    parts = cookie_value.split("|")
    if len(parts) != 3:
        return None
    user_id_raw, expires_raw, sig = parts
    try:
        user_id = int(user_id_raw)
        expires_at = datetime.fromtimestamp(int(expires_raw), tz=timezone.utc)
    except ValueError:
        return None
    if expires_at < _now():
        return None
    payload = f"{user_id}|{expires_raw}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return MemberSession(user_id=user_id, expires_at=expires_at)


def create_user(
    db_path: str | Path,
    *,
    email: str,
    password: str,
    role: str = "user",
) -> int:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        try:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, role, email_verified, created_at) VALUES (?, ?, ?, 1, ?)",
                (
                    email,
                    hash_password(password),
                    role,
                    _now().isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return -1
        inserted_id = cursor.lastrowid
        if inserted_id is None:
            return -1
        return int(inserted_id)


def fetch_user_by_email(db_path: str | Path, email: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT id, email, password_hash, role, email_verified, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    return dict(row) if row else None


def fetch_user_by_id(db_path: str | Path, user_id: int) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT id, email, role, email_verified, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def list_users(db_path: str | Path) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT id, email, role, email_verified, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def update_user_role(db_path: str | Path, user_id: int, role: str) -> None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def update_password(db_path: str | Path, user_id: int, password: str) -> None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
