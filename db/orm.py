"""
db/orm.py

Shared Flask-SQLAlchemy configuration.

The existing InstanceStore currently uses sqlite3 directly.
New databases can progressively move to SQLAlchemy without
breaking the existing instance management code.
"""

import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """
    Enable SQLite foreign key enforcement.
    """

    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
