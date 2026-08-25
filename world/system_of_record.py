"""The payer's backend enrollment database: the SYSTEM OF RECORD (SoR).

This is the source of truth for whether an enrollment actually posted. The
portal's on-screen "Submitted successfully" page is NOT proof; a row here is.
The agent under test is expected to reconcile against this via the read API
(/api/sor/enrollment/{npi}) after every submit.

Backed by a SQLite file so state survives and the harness can inspect it.
"""
import os
import sqlite3
import threading
import time

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sor.db")
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(reset: bool = False) -> None:
    with _LOCK:
        conn = _conn()
        if reset:
            conn.execute("DROP TABLE IF EXISTS enrollments")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrollments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                npi             TEXT NOT NULL,
                payer           TEXT,
                status          TEXT NOT NULL DEFAULT 'active',
                idempotency_key TEXT,
                confirmation_id TEXT,
                created_at      REAL NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()


def record_enrollment(npi: str, payer: str, idempotency_key: str | None = None):
    """Insert an enrollment. Returns (row, created).

    If idempotency_key is provided and already seen, no new row is created and
    the existing one is returned with created=False. Without a key, a repeat
    call creates a DUPLICATE row: the real double-submission bug, on purpose.
    """
    with _LOCK:
        conn = _conn()
        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM enrollments WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                conn.close()
                return dict(existing), False
        confirmation_id = f"PC-{int(time.time() * 1000) % 1_000_000:06d}"
        cur = conn.execute(
            "INSERT INTO enrollments (npi, payer, status, idempotency_key, confirmation_id, created_at) "
            "VALUES (?, ?, 'active', ?, ?, ?)",
            (npi, payer, idempotency_key, confirmation_id, time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM enrollments WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row), True


def get_enrollment(npi: str):
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM enrollments WHERE npi = ? ORDER BY id DESC LIMIT 1", (npi,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None


def count_enrollments(npi: str) -> int:
    with _LOCK:
        conn = _conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM enrollments WHERE npi = ?", (npi,)
        ).fetchone()["n"]
        conn.close()
        return int(n)


def list_all():
    with _LOCK:
        conn = _conn()
        rows = conn.execute("SELECT * FROM enrollments ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def reset() -> None:
    init(reset=True)
