"""ONE-OFF: add the account-approval columns to an existing users table.

Run once per existing database, then DELETE THIS FILE. It is not imported by
anything and nothing calls it; a fresh database gets the columns from
python3 -m db.init_database, which still refuses an old users table until this
has been run.

    python3 -m db.oneoff_add_account_approval --check    # report only, no writes
    python3 -m db.oneoff_add_account_approval

Run from the repository root with the web server's virtual environment and
environment settings (same as db.init_database), and back up the database first.

What it does, in one transaction:
  * adds users.approval_status ('pending' default), approved_at, approved_by
    and the ix_users_approval_status index
  * marks existing manager and sysadmin accounts approved, so staff can still
    get in and approve everyone else
  * leaves every existing student account pending, to go through the queue

If no sysadmin exists afterwards, approve one with
    flask --app wsgi set-role <username> admin
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pond-sec"))

from sqlalchemy import inspect  # noqa: E402

from db.orm import db  # noqa: E402

APPROVAL_COLUMNS = ("approval_status", "approved_at", "approved_by")
STAFF_ROLES = ("manager", "sysadmin")


def run(check_only=False):
    """Run inside the web app's application context."""
    engine = db.engines["pond"]
    if engine.dialect.name != "sqlite":
        raise RuntimeError("This script supports SQLite only.")
    print(f"Database: {engine.url.render_as_string(hide_password=True)}", flush=True)
    filename = engine.url.database
    if not filename or filename == ":memory:" or not Path(filename).is_file():
        raise RuntimeError("Database file does not exist; nothing to upgrade.")

    inspector = inspect(engine)
    if not {"users", "roles"} <= set(inspector.get_table_names()):
        raise RuntimeError("No users/roles tables; use python3 -m db.init_database instead.")
    found = {c["name"] for c in inspector.get_columns("users")}.intersection(APPROVAL_COLUMNS)
    if len(found) == len(APPROVAL_COLUMNS):
        print("Already upgraded: approval columns exist. Nothing to do - delete this script.")
        return
    if found:
        raise RuntimeError("Partial approval columns on users (" + ", ".join(sorted(found)) +
                           "); review by hand. No changes made.")

    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection
        placeholders = ", ".join("?" for _ in STAFF_ROLES)
        rows = conn.execute(
            "SELECT u.username, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id "
            "ORDER BY r.role_level DESC, u.username"
        ).fetchall()
        staff = [name for name, role in rows if role in STAFF_ROLES]
        students = [name for name, role in rows if role not in STAFF_ROLES]
        print(f"Will approve {len(staff)} staff account(s): {', '.join(staff) or '-'}")
        print(f"Will leave {len(students)} other account(s) pending: {', '.join(students) or '-'}")
        if not any(role == "sysadmin" for _, role in rows):
            print("WARNING: no sysadmin account exists. Afterwards, run "
                  "`flask --app wsgi set-role <username> admin` to approve one.")
        if check_only:
            print("Check complete. No changes made.")
            return

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        roles_sql = ", ".join(f"'{name}'" for name in STAFF_ROLES)
        # executescript() so the ALTERs and the staff backfill commit or roll
        # back together: a crash between them would leave every admin pending.
        script = f"""
            BEGIN;
            ALTER TABLE users ADD COLUMN approval_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (approval_status IN ('pending', 'approved', 'rejected'));
            ALTER TABLE users ADD COLUMN approved_at DATETIME;
            ALTER TABLE users ADD COLUMN approved_by INTEGER REFERENCES users(user_id);
            CREATE INDEX ix_users_approval_status ON users(approval_status);
            UPDATE users SET approval_status = 'approved', approved_at = '{now}'
                WHERE role_id IN (SELECT role_id FROM roles WHERE role_name IN ({roles_sql}));
            COMMIT;
        """
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        raw.close()
    print("Done. Run `python3 -m db.init_database --check` to verify, then delete this script.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Report what would change; no writes.")
    args = parser.parse_args(argv)
    from app import create_app
    try:
        app = create_app()
        with app.app_context():
            run(check_only=args.check)
    except RuntimeError as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
