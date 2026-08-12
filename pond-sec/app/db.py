"""SQLite access layer.

One connection per request, stored on Flask's `g`, closed automatically when the
request ends. Rows come back as sqlite3.Row, so templates and view code index
them like dictionaries: row["username"].

EVERYTHING that touches the database should go through query() and execute() in
this module. Two reasons: they take parameters separately from the SQL (which is
what stops SQL injection), and it means there is one place to add logging,
timing or a connection pool later.

ADDING A TABLE OR COLUMN
------------------------
Edit app/schema.sql, then run `flask --app wsgi init-db`. Be aware that
init-db DROPS AND REBUILDS EVERYTHING — there is no migration system. On a
development machine that is fine (re-run seed-db afterwards). Once this is
carrying real student scores, you need one of:

  * a migrations tool (Alembic, or plain numbered .sql files applied in order),
  * or a documented export/import step before each schema change.

Whoever first deploys this for a real cohort should sort that out before the
first schema change, not after.
"""

import sqlite3
from datetime import datetime

import click
from flask import current_app, g


def get_db() -> sqlite3.Connection:
    """The connection for this request, opened on first use.

    Foreign keys are enabled per-connection because SQLite defaults them OFF —
    forget this and the ON DELETE CASCADE rules in schema.sql silently do
    nothing.
    """
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    """Run a SELECT.

    args: values for the ? placeholders in sql. ALWAYS pass values this way.
          Never build SQL with f-strings or .format() — that is the injection
          bug this project exists to teach people about.
    one:  True returns a single row (or None) instead of a list.
    """
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    """Run an INSERT/UPDATE/DELETE, commit, and return the new row id.

    Commits immediately, which suits this app's short single-statement writes.
    If you add an operation where several statements must succeed or fail
    together, do not chain execute() calls — take the connection with get_db(),
    run the statements, and commit once. scoring.submit_flag() is the worked
    example of that pattern.
    """
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last_id = cur.lastrowid
    cur.close()
    return last_id


def utcnow() -> str:
    """Timestamp for manual inserts.

    Everything in this codebase stores UTC in this exact format so that string
    comparison equals chronological comparison in SQLite (which has no real
    date type). If you add a timestamp column, keep the format and keep it UTC —
    mixing local time in will break the expiry and throttle windows in ways that
    only show up at daylight saving.
    """
    """Timestamps are stored as UTC 'YYYY-MM-DD HH:MM:SS' to match datetime('now')."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """Rebuild the schema from scratch. Destroys all existing data."""
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))
    db.commit()


@click.command("init-db")
def init_db_command():
    """Drop everything and rebuild the schema."""
    init_db()
    click.echo("Schema rebuilt.")


@click.command("seed-db")
def seed_db_command():
    """Load the three MVP challenges, their rooms, VMs and flags."""
    from .seed import seed

    seed()
    click.echo("Seed data loaded.")


def init_app(app):
    """Wire this module into the app. Called once from create_app().

    Add new `flask --app wsgi <name>` commands here with app.cli.add_command().
    """
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)
