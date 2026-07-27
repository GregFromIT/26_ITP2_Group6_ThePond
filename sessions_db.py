#!/usr/bin/env python3
"""
sessions_db.py - SQLite CLI wrapper for The Pond's pool/session tracking.

All commands print a single JSON value to stdout so Ansible tasks can
consume results via `from_json`. Never called with SQL directly by
anything else in the project - this file is the only place SQL lives.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

DB_PATH = os.environ.get("THEPOND_DB_PATH", "/opt/thepond/sessions.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)  # wait on lock rather than fail immediately
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(args):
    conn = get_conn()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(json.dumps({"status": "initialized", "db_path": DB_PATH}))


# ---- pool_slots ----

def create_pool_slot(args):
    conn = get_conn()
    now = int(time.time())
    conn.execute(
        "INSERT INTO pool_slots (vmid, challenge, status, created_at) VALUES (?, ?, 'available', ?)",
        (args.vmid, args.challenge, now),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"status": "created", "vmid": args.vmid, "challenge": args.challenge}))


def list_available(args):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM pool_slots WHERE challenge = ? AND status = 'available'",
        (args.challenge,),
    ).fetchall()
    conn.close()
    print(json.dumps([dict(r) for r in rows]))


# ---- sessions (checkout / release) ----

def assign(args):
    """Pick one available pool slot for the given challenge and check it out."""
    conn = get_conn()
    row = conn.execute(
        "SELECT vmid FROM pool_slots WHERE challenge = ? AND status = 'available' LIMIT 1",
        (args.challenge,),
    ).fetchone()
    if row is None:
        conn.close()
        print(json.dumps({"status": "no_available_slot"}))
        sys.exit(1)

    vmid = row["vmid"]
    now = int(time.time())
    expires = now + (args.ttl_hours * 3600)

    conn.execute("UPDATE pool_slots SET status = 'in_use' WHERE vmid = ?", (vmid,))
    conn.execute(
        """INSERT INTO sessions
           (session_id, username, challenge, vmid, ip_address, flag, created_at, expires_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (args.session_id, args.username, args.challenge, vmid,
         args.ip or "", args.flag, now, expires),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"status": "assigned", "session_id": args.session_id, "vmid": vmid, "expires_at": expires}))


def release(args):
    """Return a session's pool slot to the pool (marked resetting, not available,
    until a reset playbook confirms it's clean)."""
    conn = get_conn()
    row = conn.execute("SELECT vmid FROM sessions WHERE session_id = ?", (args.session_id,)).fetchone()
    if row is None:
        conn.close()
        print(json.dumps({"status": "session_not_found"}))
        sys.exit(1)

    vmid = row["vmid"]
    conn.execute("UPDATE sessions SET status = 'destroyed' WHERE session_id = ?", (args.session_id,))
    conn.execute("UPDATE pool_slots SET status = 'resetting' WHERE vmid = ?", (vmid,))
    conn.commit()
    conn.close()
    print(json.dumps({"status": "released", "session_id": args.session_id, "vmid": vmid}))


def mark_pool_slot_available(args):
    """Called by the reset playbook once a released slot has been cleaned/reverted."""
    conn = get_conn()
    conn.execute("UPDATE pool_slots SET status = 'available' WHERE vmid = ?", (args.vmid,))
    conn.commit()
    conn.close()
    print(json.dumps({"status": "available", "vmid": args.vmid}))


def list_expired(args):
    conn = get_conn()
    now = int(time.time())
    rows = conn.execute(
        "SELECT * FROM sessions WHERE expires_at <= ? AND status = 'active'", (now,)
    ).fetchall()
    conn.close()
    print(json.dumps([dict(r) for r in rows]))


def mark_destroyed(args):
    conn = get_conn()
    conn.execute("UPDATE sessions SET status = 'destroyed' WHERE session_id = ?", (args.session_id,))
    conn.commit()
    conn.close()
    print(json.dumps({"status": "destroyed", "session_id": args.session_id}))


def get_session(args):
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (args.session_id,)).fetchone()
    conn.close()
    print(json.dumps(dict(row)) if row else json.dumps(None))


def list_sessions(args):
    conn = get_conn()
    if args.status:
        rows = conn.execute("SELECT * FROM sessions WHERE status = ?", (args.status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sessions").fetchall()
    conn.close()
    print(json.dumps([dict(r) for r in rows]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=init_db)

    p = sub.add_parser("create-pool-slot")
    p.add_argument("--vmid", type=int, required=True)
    p.add_argument("--challenge", required=True)
    p.set_defaults(func=create_pool_slot)

    p = sub.add_parser("list-available")
    p.add_argument("--challenge", required=True)
    p.set_defaults(func=list_available)

    p = sub.add_parser("assign")
    p.add_argument("--session-id", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--challenge", required=True)
    p.add_argument("--ip", default="")
    p.add_argument("--flag", required=True)
    p.add_argument("--ttl-hours", type=int, default=3)
    p.set_defaults(func=assign)

    p = sub.add_parser("release")
    p.add_argument("--session-id", required=True)
    p.set_defaults(func=release)

    p = sub.add_parser("mark-pool-slot-available")
    p.add_argument("--vmid", type=int, required=True)
    p.set_defaults(func=mark_pool_slot_available)

    sub.add_parser("list-expired").set_defaults(func=list_expired)

    p = sub.add_parser("mark-destroyed")
    p.add_argument("--session-id", required=True)
    p.set_defaults(func=mark_destroyed)

    p = sub.add_parser("get")
    p.add_argument("--session-id", required=True)
    p.set_defaults(func=get_session)

    p = sub.add_parser("list-sessions")
    p.add_argument("--status", default=None)
    p.set_defaults(func=list_sessions)

    parsed = parser.parse_args()
    parsed.func(parsed)
