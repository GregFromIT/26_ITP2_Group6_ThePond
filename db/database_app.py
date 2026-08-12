"""
db/database_app.py

Minimal Flask application context used for
The Pond database administration and testing.
"""

from flask import Flask

from db.orm import db


app = Flask(__name__)

app.config["SQLALCHEMY_BINDS"] = {
    "pond": "sqlite:///the_pond.db"
}

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
