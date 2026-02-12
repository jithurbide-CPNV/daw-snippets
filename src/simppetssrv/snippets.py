from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNIPPET_VISIBILITIES = ("public", "private")
REQUEST_STATUSES = ("pending", "approved", "denied")


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            visibility TEXT NOT NULL,
            description TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snippet_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snippet_id INTEGER NOT NULL,
            requester_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            UNIQUE(snippet_id, requester_id)
        )
        """
    )


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def create_snippet(
    db_path: str | Path,
    *,
    owner_id: int,
    title: str,
    language: str,
    description: str,
    body: str,
    visibility: str,
) -> int:
    if visibility not in SNIPPET_VISIBILITIES:
        msg = f"Invalid visibility '{visibility}'"
        raise ValueError(msg)
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        cursor = conn.execute(
            """
            INSERT INTO snippets (owner_id, title, language, visibility, description, body, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                title,
                language,
                visibility,
                description,
                body,
                _now(),
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def update_snippet(
    db_path: str | Path,
    snippet_id: int,
    *,
    owner_id: int,
    title: str,
    language: str,
    description: str,
    body: str,
    visibility: str,
) -> bool:
    if visibility not in SNIPPET_VISIBILITIES:
        msg = f"Invalid visibility '{visibility}'"
        raise ValueError(msg)
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        result = conn.execute(
            """
            UPDATE snippets
            SET title = ?, language = ?, visibility = ?, description = ?, body = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (title, language, visibility, description, body, _now(), snippet_id, owner_id),
        )
        return result.rowcount == 1


def delete_snippet(db_path: str | Path, snippet_id: int, *, owner_id: int) -> bool:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        result = conn.execute(
            "DELETE FROM snippets WHERE id = ? AND owner_id = ?",
            (snippet_id, owner_id),
        )
        conn.execute("DELETE FROM snippet_access_requests WHERE snippet_id = ?", (snippet_id,))
        return result.rowcount == 1


def fetch_snippet(db_path: str | Path, snippet_id: int) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT * FROM snippets WHERE id = ?",
            (snippet_id,),
        ).fetchone()
    return dict(row) if row else None


def list_public_snippets(db_path: str | Path) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM snippets WHERE visibility = 'public' ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def list_snippets_by_ids(db_path: str | Path, snippet_ids: list[int]) -> list[dict[str, Any]]:
    if not snippet_ids:
        return []
    placeholders = ",".join("?" for _ in snippet_ids)
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            f"SELECT * FROM snippets WHERE id IN ({placeholders})",
            tuple(snippet_ids),
        ).fetchall()
    mapping = {int(row["id"]): dict(row) for row in rows}
    return [mapping[sid] for sid in snippet_ids if sid in mapping]


def list_accessible_snippets(db_path: str | Path, user_id: int) -> list[int]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT id AS snippet_id FROM snippets WHERE visibility = 'public'
            UNION
            SELECT id AS snippet_id FROM snippets WHERE owner_id = ?
            UNION
            SELECT snippet_id AS snippet_id FROM snippet_access_requests
              WHERE requester_id = ? AND status = 'approved'
            """,
            (user_id, user_id),
        ).fetchall()
    return [int(row["snippet_id"]) for row in rows]


def has_access(db_path: str | Path, snippet_id: int, *, user_id: int | None) -> bool:
    snippet = fetch_snippet(db_path, snippet_id)
    if snippet is None:
        return False
    if snippet["visibility"] == "public":
        return True
    if user_id is None:
        return False
    if snippet["owner_id"] == user_id:
        return True
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            """
            SELECT id FROM snippet_access_requests
            WHERE snippet_id = ? AND requester_id = ? AND status = 'approved'
            LIMIT 1
            """,
            (snippet_id, user_id),
        ).fetchone()
    return row is not None


def get_request_for_user(
    db_path: str | Path,
    *,
    snippet_id: int,
    requester_id: int,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT * FROM snippet_access_requests WHERE snippet_id = ? AND requester_id = ?",
            (snippet_id, requester_id),
        ).fetchone()
    return dict(row) if row else None


def create_access_request(
    db_path: str | Path,
    *,
    snippet_id: int,
    requester_id: int,
    note: str | None = None,
) -> int | None:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        existing = conn.execute(
            "SELECT id, status FROM snippet_access_requests WHERE snippet_id = ? AND requester_id = ?",
            (snippet_id, requester_id),
        ).fetchone()
        if existing and existing["status"] == "pending":
            return None
        if existing and existing["status"] in {"approved", "denied"}:
            conn.execute(
                "DELETE FROM snippet_access_requests WHERE id = ?",
                (existing["id"],),
            )
        cursor = conn.execute(
            """
            INSERT INTO snippet_access_requests (snippet_id, requester_id, status, note, created_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (snippet_id, requester_id, note, _now()),
        )
        return int(cursor.lastrowid)


def update_request_status(db_path: str | Path, request_id: int, *, status: str) -> bool:
    if status not in REQUEST_STATUSES:
        msg = f"Invalid request status '{status}'"
        raise ValueError(msg)
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        result = conn.execute(
            "UPDATE snippet_access_requests SET status = ?, resolved_at = ? WHERE id = ?",
            (status, _now(), request_id),
        )
        return result.rowcount == 1


def list_pending_requests(db_path: str | Path) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM snippet_access_requests WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


def list_requests_for_snippet(db_path: str | Path, snippet_id: int) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM snippet_access_requests WHERE snippet_id = ?",
            (snippet_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_requests_for_user(db_path: str | Path, requester_id: int) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM snippet_access_requests WHERE requester_id = ?",
            (requester_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_private_snippets(db_path: str | Path, owner_id: int) -> int:
    with _connect(db_path) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM snippets WHERE owner_id = ? AND visibility = 'private'",
            (owner_id,),
        ).fetchone()
    return int(row["total"]) if row else 0
