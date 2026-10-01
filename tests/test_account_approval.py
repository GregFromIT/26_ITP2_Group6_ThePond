"""Account approval: the gate, the waiting page, the admin queue and the CLI."""

from pathlib import Path
import sys

import pytest
from flask import Flask
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pond-sec"))
from app import create_app
from app.security import set_password
from db import AuditLog, Role, User, db

PASSWORD = "CorrectHorseBattery1"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Flask, "auto_find_instance_path", lambda self: str(tmp_path / "instance"))
    app = create_app({"TESTING": True, "SECRET_KEY": "test-only", "FORCE_HTTPS": False,
                      "SQLALCHEMY_BINDS": {"pond": "sqlite:///" + str(tmp_path / "web.db")}})
    with app.app_context():
        from db.init_database import initialise_database
        initialise_database()
        roles = {r.role_name: r.role_id for r in db.session.scalars(select(Role))}
        for i, role, status in [(1, "sysadmin", "approved"), (2, "manager", "approved"),
                                (3, "user", "pending"), (4, "manager", "pending")]:
            db.session.add(User(user_id=i, role_id=roles[role], username=f"user{i}",
                                display_name="Test", approval_status=status))
        db.session.commit()
        set_password(3, PASSWORD)
    yield app
    with app.app_context():
        db.session.remove()
        db.engines["pond"].dispose()


def client(app, user=None):
    c = app.test_client()
    with c.session_transaction() as session:
        if user is not None:
            session["user_id"] = user
        session["_csrf"] = "test-csrf"
    return c


def user(app, user_id):
    with app.app_context():
        found = db.session.get(User, user_id)
        db.session.expunge(found)
        return found


def events(app, action):
    with app.app_context():
        return list(db.session.scalars(select(AuditLog).filter_by(action=action)))


def test_registration_creates_pending_account(app):
    c = client(app)
    result = c.post("/register", data={"_csrf": "test-csrf", "name": "New Person", "uni_year": "Year 1",
                                       "username": "newperson", "password": PASSWORD, "confirm": PASSWORD})
    assert result.status_code == 302
    with app.app_context():
        created = db.session.scalars(select(User).filter_by(username="newperson")).one()
        assert created.approval_status == "pending"
        assert created.approved_at is None and created.approved_by is None


def test_pending_login_lands_on_waiting_page(app):
    c = client(app)
    result = c.post("/login", data={"_csrf": "test-csrf", "username": "user3", "password": PASSWORD})
    assert result.status_code == 302 and result.location.endswith("/pending")
    page = c.get("/pending")
    assert page.status_code == 200
    assert b"Your password was correct" in page.data
    assert b"Waiting for approval" in page.data


def test_gate_holds_pending_account(app):
    c = client(app, user=3)
    for path in ("/dashboard", "/themes", "/notifications", "/admin/"):
        result = c.get(path)
        assert result.status_code == 302 and result.location.endswith("/pending"), path
    assert c.get("/pending").status_code == 200
    result = c.post("/logout", data={"_csrf": "test-csrf"})
    assert result.status_code == 302
    with c.session_transaction() as session:
        assert "user_id" not in session


def test_gate_holds_pending_staff_account(app):
    result = client(app, user=4).get("/admin/users")
    assert result.status_code == 302 and result.location.endswith("/pending")


def test_approved_account_skips_waiting_page(app):
    result = client(app, user=1).get("/pending")
    assert result.status_code == 302 and result.location.endswith("/dashboard")


def test_only_admins_see_the_queue(app):
    assert client(app, user=2).get("/admin/approvals").status_code == 403
    page = client(app, user=1).get("/admin/approvals")
    assert page.status_code == 200 and b"user3" in page.data


def test_moderator_cannot_approve_or_reject(app):
    c = client(app, user=2)
    assert c.post("/admin/users/3/approve", data={"_csrf": "test-csrf"}).status_code == 403
    assert c.post("/admin/users/3/reject", data={"_csrf": "test-csrf"}).status_code == 403
    assert user(app, 3).approval_status == "pending"


def test_approve_records_who_and_when_then_opens_the_platform(app):
    result = client(app, user=1).post("/admin/users/3/approve", data={"_csrf": "test-csrf"})
    assert result.status_code == 302 and result.location.endswith("/admin/approvals")
    approved = user(app, 3)
    assert approved.approval_status == "approved"
    assert approved.approved_by == 1 and approved.approved_at is not None
    assert len(events(app, "account.approved")) == 1
    assert client(app, user=3).get("/dashboard").status_code == 200


def test_approving_twice_changes_nothing(app):
    c = client(app, user=1)
    c.post("/admin/users/3/approve", data={"_csrf": "test-csrf"})
    first = user(app, 3).approved_at
    c.post("/admin/users/3/approve", data={"_csrf": "test-csrf"})
    assert user(app, 3).approved_at == first
    assert len(events(app, "account.approved")) == 1


def test_reject_keeps_row_and_can_be_reversed(app):
    c = client(app, user=1)
    assert c.post("/admin/users/3/reject", data={"_csrf": "test-csrf"}).status_code == 302
    rejected = user(app, 3)
    assert rejected.approval_status == "rejected" and rejected.approved_by == 1
    assert len(events(app, "account.rejected")) == 1

    login = client(app)
    login.post("/login", data={"_csrf": "test-csrf", "username": "user3", "password": PASSWORD})
    page = login.get("/pending")
    assert b"was not approved" in page.data
    assert login.get("/dashboard").location.endswith("/pending")

    page = c.get("/admin/approvals")
    assert b"rejected" in page.data and b"user1" in page.data
    c.post("/admin/users/3/approve", data={"_csrf": "test-csrf"})
    assert user(app, 3).approval_status == "approved"


def test_reject_refuses_staff_accounts(app):
    assert client(app, user=1).post("/admin/users/4/reject", data={"_csrf": "test-csrf"}).status_code == 403
    assert user(app, 4).approval_status == "pending"


def test_console_shows_pending_count_to_admins_only(app):
    assert b"Awaiting approval" in client(app, user=1).get("/admin/").data
    assert b"Awaiting approval" not in client(app, user=2).get("/admin/").data


def test_cli_set_role_approves_pending_account(app):
    # The `flask` CLI pushes an app context; invoking app.cli directly does not.
    with app.app_context():
        result = app.test_cli_runner().invoke(args=["set-role", "user3", "admin"])
    assert result.exit_code == 0, result.output
    promoted = user(app, 3)
    assert promoted.approval_status == "approved"
    assert promoted.approved_by is None and promoted.approved_at is not None
    assert len(events(app, "account.approved")) == 1


def test_seeded_accounts_are_approved(app):
    from db.seed_accounts import seed_accounts
    with app.app_context():
        assert seed_accounts([("seed-admin", "Seed Admin", "sysadmin")]) == 1
        seeded = db.session.scalars(select(User).filter_by(username="seed-admin")).one()
        assert seeded.approval_status == "approved" and seeded.approved_at is not None
