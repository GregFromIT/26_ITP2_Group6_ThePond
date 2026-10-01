"""Initialization tests use temporary databases, never the deployed database."""

import sqlite3

import pytest
from flask import Flask
from sqlalchemy import inspect, text

from db import Challenge, Role, User, db
from db import init_database as init


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_BINDS={"pond": "sqlite:///" + str(tmp_path / "pond.sqlite")},
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    monkeypatch.setattr(init, "seed_challenges_from_yaml", lambda: pytest.fail("Unexpected Proxmox seeding"))
    with app.app_context():
        yield app
        db.session.remove()
        db.engines["pond"].dispose()


def test_creates_all_five_tables_and_default_roles(app):
    init.initialise_database()
    assert init.UPLOAD_TABLES <= set(inspect(db.engines["pond"]).get_table_names())
    assert init.check_schema(db.engines["pond"]) == []
    assert set(db.session.execute(db.select(Role.role_name)).scalars()) == {"user", "manager", "sysadmin"}
    assert db.session.execute(db.select(User)).first() is None


def test_repeated_initialization_preserves_existing_data(app):
    init.initialise_database()
    role = db.session.execute(db.select(Role).filter_by(role_name="user")).scalar_one()
    user = User(username="existing", display_name="Existing", role_id=role.role_id)
    challenge = Challenge(title="Existing", description="Keep", instructions="Keep", status="draft")
    db.session.add_all([user, challenge])
    db.session.commit()
    ids = user.user_id, challenge.challenge_id
    init.initialise_database()
    assert db.session.get(User, ids[0]).username == "existing"
    assert db.session.get(Challenge, ids[1]).description == "Keep"
    assert len(list(db.session.execute(db.select(Role)).scalars())) == 3


def test_check_missing_file_does_not_create_it(app):
    from pathlib import Path
    filename = Path(db.engines["pond"].url.database)
    with pytest.raises(RuntimeError, match="Database file does not exist"):
        init.initialise_database(check_only=True)
    assert not filename.exists()


def test_check_does_not_add_missing_roles(app):
    db.create_all(bind_key="pond")
    with pytest.raises(RuntimeError, match="Missing roles"):
        init.initialise_database(check_only=True)
    assert db.session.execute(db.select(Role)).first() is None


def test_check_succeeds_without_data_writes(app):
    init.initialise_database()
    statements = []
    from sqlalchemy import event
    engine = db.engines["pond"]
    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        init.initialise_database(check_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}.intersection(statements)


def test_adds_upload_tables_to_existing_model_database(app):
    tables = [t for t in db.metadatas["pond"].sorted_tables if t.name not in init.UPLOAD_TABLES]
    db.metadatas["pond"].create_all(db.engines["pond"], tables=tables)
    with pytest.raises(RuntimeError, match="Missing table:.*"):
        init.initialise_database(check_only=True)
    assert not init.UPLOAD_TABLES.intersection(inspect(db.engines["pond"]).get_table_names())
    init.initialise_database()
    assert init.check_schema(db.engines["pond"]) == []


def test_incompatible_existing_table_stops_before_schema_changes(app):
    with db.engines["pond"].begin() as conn:
        conn.execute(text("CREATE TABLE challenges (challenge_id INTEGER PRIMARY KEY)"))
    with pytest.raises(RuntimeError, match="Missing column: challenges"):
        init.initialise_database()
    assert inspect(db.engines["pond"]).get_table_names() == ["challenges"]


def test_missing_queue_index_reported_not_silently_repaired(app):
    init.initialise_database()
    with db.engines["pond"].begin() as conn:
        conn.execute(text("DROP INDEX uq_submission_active_job"))
    with pytest.raises(RuntimeError, match="Missing/mismatched index: submission_jobs.uq_submission_active_job"):
        init.initialise_database()


def test_role_conflict_stops_before_adding_upload_tables(app):
    Role.__table__.create(db.engines["pond"])
    db.session.add(Role(role_name="unexpected", role_level=1))
    db.session.commit()
    with pytest.raises(RuntimeError, match="Conflicting role"):
        init.initialise_database()
    assert inspect(db.engines["pond"]).get_table_names() == ["roles"]


def test_only_explicit_flag_calls_seeder(app, monkeypatch):
    calls = []
    monkeypatch.setattr(init, "seed_challenges_from_yaml", lambda: calls.append(True))
    init.initialise_database(seed_challenges=True)
    assert calls == [True]


def test_seed_failure_keeps_initialized_tables(app, monkeypatch):
    def failure():
        raise RuntimeError("Simulated seeding failure")
    monkeypatch.setattr(init, "seed_challenges_from_yaml", failure)
    with pytest.raises(RuntimeError, match="Simulated seeding failure"):
        init.initialise_database(seed_challenges=True)
    assert init.check_schema(db.engines["pond"]) == []
    assert len(list(db.session.execute(db.select(Role)).scalars())) == 3


def test_conflicting_options_rejected_in_function_and_cli(app):
    with pytest.raises(ValueError):
        init.initialise_database(check_only=True, seed_challenges=True)
    with pytest.raises(SystemExit) as result:
        init.main(["--check", "--seed-challenges"])
    assert result.value.code == 2


def test_plural_entrypoint_delegates_to_canonical_function():
    from db import init_databases
    assert init_databases.main is init.main
    assert init_databases.initialise_database is init.initialise_database


def test_real_web_configuration_drives_cli(tmp_path, monkeypatch):
    from app import create_app as web_create_app
    from flask import Flask
    # Keep factory-created instance directory and all databases in tmp_path.
    monkeypatch.setattr(Flask, "auto_find_instance_path", lambda self: str(tmp_path / "instance"))
    filename = tmp_path / "web-configured.sqlite"
    app = web_create_app({"TESTING": True, "SECRET_KEY": "test-only",
                          "SQLALCHEMY_BINDS": {"pond": "sqlite:///" + str(filename)}})
    monkeypatch.setattr(init, "create_app", lambda: app)
    monkeypatch.setattr(init, "seed_challenges_from_yaml", lambda: pytest.fail("Unexpected seeding"))
    init.main([])
    init.main(["--check"])
    with sqlite3.connect(filename) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert init.UPLOAD_TABLES <= tables
    with app.app_context():
        db.session.remove()
        db.engines["pond"].dispose()
