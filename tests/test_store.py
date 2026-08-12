import time

import pytest

from db.store import InstanceStore


@pytest.fixture
def store():
    s = InstanceStore(db_path=":memory:")
    s.init_db()
    now = int(time.time())
    s._conn.execute(
        """INSERT INTO instances (name, challenge, vmid, node, status, created_at, expires_at)
           VALUES ('lockedshields-team01', 'lockedshields', 1001, 'pve', 'active', ?, ?)""",
        (now, now + 3600),
    )
    s._conn.commit()
    yield s
    s.close()


def test_get_instance_by_session_returns_match(store):
    instance = store.get_instance_by_session(1001)
    assert instance is not None
    assert instance["vmid"] == 1001
    assert instance["name"] == "lockedshields-team01"


def test_get_instance_by_session_returns_none_when_missing(store):
    assert store.get_instance_by_session(9999) is None
