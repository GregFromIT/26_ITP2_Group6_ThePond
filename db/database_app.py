from pathlib import Path
from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine
from db.orm import db

_DB_PATH = Path(__file__).resolve().parent.parent / "the_pond.db"

app = Flask(__name__)
app.config["SQLALCHEMY_BINDS"] = {"pond": f"sqlite:///{_DB_PATH}"}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """WAL mode lets readers proceed without blocking writers, and lets
    concurrent writers queue instead of one immediately raising 'database
    is locked' - needed now that claim_vmid() below expects concurrent
    commits to genuinely race rather than serialize by accident."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()