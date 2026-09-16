"""Create initial accounts in The Pond using the web app's configuration.

    python3 -m db.seed_accounts --check
    python3 -m db.seed_accounts

*****************************************************************************
Edit ACCOUNTS below before running. Existing usernames are skipped, including
their passwords and roles. Accounts are committed individually by the existing password helper; if a later account fails, earlier accounts remain created.
Temporary passwords are printed to the terminal and are not saved to a file.
This script does not initialise tables or seed challenges/contact Proxmox.
*****************************************************************************
"""

import argparse
import sys
from pathlib import Path


# Each entry is (username, display name, database role).
# Allowed roles: user, manager, sysadmin.
# Add confirmed accounts here; the commented entry is only an example.
ACCOUNTS = [
    ("Ducky-0", "Ben Turnbull", "sysadmin"),
    ("11-Ducky", "Vasili Stergiou", "manager"),
    ("21-Ducky", "Lochlan Hardie", "manager"),
    ("11A-Ducky", "Gareth Thomas", "user"),
    ("21A-Ducky", "Megan Bates", "user"),
    
]

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pond-sec"))

from app import create_app  # noqa: E402
from app.security import USERNAME_RE, issue_temporary_password  # noqa: E402
from db.orm import db  # noqa: E402
from db.user_models import Role, User, UserCredential  # noqa: E402
from db.throttle_models import ThrottleEvent  # noqa: E402
from sqlalchemy import inspect  # noqa: E402


def seed_accounts(accounts, check_only=False):
    """Run inside an app context. Return the number of created accounts."""
    seen = set()
    for username, display_name, role_name in accounts:
        if not USERNAME_RE.fullmatch(username):
            raise ValueError(f"Invalid username: {username!r}; use 3-32 letters, digits, _, . or -.")
        if username in seen:
            raise ValueError(f"Duplicate username in ACCOUNTS: {username}")
        seen.add(username)
        if not display_name.strip() or len(display_name) > 100:
            raise ValueError(f"Display name for {username} must contain 1-100 characters.")
        if role_name not in {"user", "manager", "sysadmin"}:
            raise ValueError(f"Unknown role for {username}: {role_name}")

    engine = db.engines["pond"]
    print(f"Database: {engine.url.render_as_string(hide_password=True)}", flush=True)
    if engine.dialect.name == "sqlite":
        filename = engine.url.database
        if not filename or filename == ":memory:" or not Path(filename).is_file():
            raise RuntimeError("Expected an existing SQLite database file. Check the web app's database path first.")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for model in (Role, User, UserCredential, ThrottleEvent):
        table = model.__table__
        if table.name not in tables:
            raise RuntimeError(f"Missing table: {table.name}. Resolve database initialisation before seeding accounts.")
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        missing = set(table.columns.keys()) - actual
        if missing:
            raise RuntimeError(f"Missing columns in {table.name}: {', '.join(sorted(missing))}.")

    roles = {role.role_name: role for role in db.session.execute(db.select(Role)).scalars()}
    missing_roles = {entry[2] for entry in accounts} - roles.keys()
    if missing_roles:
        raise RuntimeError(f"Missing roles: {', '.join(sorted(missing_roles))}. Initialise roles first.")

    created = 0
    for username, display_name, role_name in accounts:
        existing = db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none()
        if existing is not None:
            print(f"SKIP {username}: already exists; role and password unchanged.", flush=True)
            if existing.credentials is None:
                print("  This existing account has no credentials; use the staff password-reset process.", flush=True)
            continue
        if check_only:
            print(f"WOULD CREATE {username} ({display_name}), role={role_name}")
            continue

        try:
            user = User(username=username, display_name=display_name,
                        role=roles[role_name], is_active=True)
            db.session.add(user)
            db.session.flush()
            temporary = issue_temporary_password(user.user_id)
        except Exception:
            db.session.rollback()
            print(f"Failed while creating {username}. Earlier accounts remain created; inspect this account before retrying.", file=sys.stderr)
            raise

        created += 1
        print(f"CREATED {username} | role={role_name} | temporary password: {temporary}", flush=True)
        print("  Password change required at first login. Copy the password now.", flush=True)

    if check_only:
        print("Check complete. No accounts created.")
    else:
        print(f"Done. Created {created} account(s). Existing accounts were skipped.")
    return created

# db/seed_accounts.py — add after seed_accounts(), before debug_accounts()

def reset_accounts(accounts, check_only=False):
    """Issue each existing account in ACCOUNTS a fresh temporary password.

    Skips usernames not yet in the database — run without --reset first to
    create them. Does not touch role, display_name, or is_active.
    """
    reset = 0
    for username, display_name, role_name in accounts:
        user = db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none()
        if user is None:
            print(f"SKIP {username}: not in database. Run seed_accounts without --reset to create it first.", flush=True)
            continue
        if check_only:
            print(f"WOULD RESET {username} ({user.display_name})")
            continue
        temporary = issue_temporary_password(user.user_id)
        reset += 1
        print(f"RESET {username} | temporary password: {temporary}", flush=True)
        print("  Password change required at first login. Copy the password now.", flush=True)

    if check_only:
        print("Check complete. No passwords reset.")
    else:
        print(f"Done. Reset {reset} account(s).")
    return reset

# db/seed_accounts.py — add this function, placed after seed_accounts(), before main()

def debug_accounts(accounts):
    """Report each ACCOUNTS entry's live DB state. Read-only — never writes."""
    for username, display_name, role_name in accounts:
        user = db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none()
        if user is None:
            print(f"{username}: NOT IN DATABASE (next run would create as role={role_name})")
            continue
        cred = user.credentials
        print(f"{username}:")
        print(f"  display_name = {user.display_name!r}  (ACCOUNTS says {display_name!r})")
        print(f"  role         = {user.role.role_name}  (ACCOUNTS says {role_name})")
        print(f"  is_active    = {user.is_active}")
        print(f"  last_login   = {user.last_login_at}")
        if cred is None:
            print("  credentials  = MISSING — cannot log in; needs the staff password-reset process")
        else:
            print(f"  must_change_password = {cred.must_change_password}")
            print(f"  failed_login_count   = {cred.failed_login_count}")
            print(f"  locked_until         = {cred.locked_until}")
            print(f"  password_changed_at  = {cred.password_changed_at}")

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Preview only — combine with --debug or --reset, or use alone for seed_accounts's own preview.")
    parser.add_argument("--debug", action="store_true", help="Print live DB state for every account in ACCOUNTS. Read-only.")
    parser.add_argument("--reset", action="store_true", help="Issue existing accounts in ACCOUNTS a fresh temporary password.")
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        try:
            if args.debug:
                debug_accounts(ACCOUNTS)
            elif args.reset:
                reset_accounts(ACCOUNTS, check_only=args.check)
            else:
                seed_accounts(ACCOUNTS, check_only=args.check)
        except (ValueError, RuntimeError) as error:
            parser.exit(1, f"Error: {error}\n")

if __name__ == "__main__":
    main()
