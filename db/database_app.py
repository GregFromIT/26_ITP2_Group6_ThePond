from pathlib import Path
from flask import Flask
from db.orm import db

_DB_PATH = Path(__file__).resolve.parent.parent / "the_pond.db"

app = Flask(__name__)
app.config["SQLALCHEMY_BINDS"] = {"pond": f"sqlite:///{_DB_PATH}"}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)