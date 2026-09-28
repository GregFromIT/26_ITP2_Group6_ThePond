"""Shared Flask-SQLAlchemy setup.

WHY THIS FILE DEFERS TO db/orm.py
---------------------------------
The group's database work lives in a `db/` package with its own `db/orm.py`
defining a SQLAlchemy instance. This file was a standalone copy of it, written
while `db/` was not on the path.

Two copies is a real bug waiting to happen, not just duplication. Flask-SQLAlchemy
registers every model against the instance it was declared with. If `app/` builds
models on one instance and `db/` builds them on another, the two sets never see
each other: `db.create_all()` creates half the tables, relationships across the
boundary fail to resolve, and a query through the wrong session returns nothing
with no error to explain why. That failure is confusing enough to lose an
afternoon to.

So this imports the group's instance when `db/` is importable, and only falls
back to defining its own when it is not. Either way there is exactly one
SQLAlchemy instance in the process.

When the two trees are merged, delete this file and import `db.orm` directly.
It exists to make the merge work without a flag day, not to stay.
"""

import sqlite3

try:  # the group's db/ package, once it is on the path
    from db.orm import Base, db  # noqa: F401

    USING_SHARED_ORM = True

except ImportError:  # pond-sec running on its own
    from flask_sqlalchemy import SQLAlchemy
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import DeclarativeBase

    USING_SHARED_ORM = False

    class Base(DeclarativeBase):
        pass

    db = SQLAlchemy(model_class=Base)

    @event.listens_for(Engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        """SQLite defaults foreign keys OFF, so turn them on per connection."""
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
