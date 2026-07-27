"""
db/store.py

All database access goes through the InstanceStore class. Nothing outside
this file should ever write raw SQL - that's the whole point: when v2
swaps this for SQLAlchemy, only this file changes. Every method here keeps
its name and signature; only the body becomes `session.query(...)` instead
of `self._conn.execute(...)`.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("THEPOND_DB_PATH", os.path.join(os.path.dirname(__file__), "thepond.db"))
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


class InstanceStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, timeout=10)
        self._conn.row_factory = sqlite3.Row

    def init_db(self):
        with open(SCHEMA_PATH) as f:
            self._conn.executescript(f.read())
        self._conn.commit()

    def create(self, name: str, challenge: str, vmid: int, node: str, ttl_hours: int) -> dict:
        now = int(time.time())
        expires = now + (ttl_hours * 3600)
        self._conn.execute(
            """INSERT INTO instances (name, challenge, vmid, node, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (name, challenge, vmid, node, now, expires),
        )
        self._conn.commit()
        return self.get_by_vmid(vmid)

    def get_by_vmid(self, vmid: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM instances WHERE vmid = ?", (vmid,)).fetchone()
        return dict(row) if row else None

    def get_by_name(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM instances WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_all(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute("SELECT * FROM instances WHERE status = ?", (status,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM instances").fetchall()
        return [dict(r) for r in rows]

    def list_expired(self) -> list[dict]:
        now = int(time.time())
        rows = self._conn.execute(
            "SELECT * FROM instances WHERE status = 'active' AND expires_at <= ?", (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_destroyed(self, vmid: int) -> None:
        now = int(time.time())
        self._conn.execute(
            "UPDATE instances SET status = 'destroyed', destroyed_at = ? WHERE vmid = ?",
            (now, vmid),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
