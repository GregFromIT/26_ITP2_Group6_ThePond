"""Landing page and the signed-in dashboard.

Two routes:
  /           landing.html   — the public explanation, register and sign in
  /dashboard  dashboard.html — stats, overall board, one small board per
                               theme, resume banner, recent sessions

This blueprint holds no logic of its own on purpose: it asks scoring.py for
numbers and hands them to a template. Anything that computes a score belongs in
scoring.py, so the dashboard and the challenge pages can never disagree.

The three small boards are built by iterating over whatever is in the theme
table — there is nothing here that assumes three. Add a fourth theme in seed.py
and a fourth board appears, though the CSS grid will want a look.
"""

from flask import Blueprint, g, redirect, render_template, url_for

from .auth import login_required
from .db import query
from .scoring import overall_leaderboard, theme_leaderboard, user_stats

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def landing():
    if g.get("user"):
        return redirect(url_for("dashboard.index"))
    counts = query(
        "SELECT (SELECT COUNT(*) FROM theme)     AS themes, "
        "       (SELECT COUNT(*) FROM challenge) AS challenges, "
        "       (SELECT COUNT(*) FROM vm)        AS vms",
        one=True,
    )
    return render_template("landing.html", counts=counts)


@bp.route("/dashboard")
@login_required
def index():
    user = g.user
    themes = query("SELECT * FROM theme ORDER BY sort_order, theme_id")
    boards = [
        {"theme": theme, "rows": theme_leaderboard(theme["theme_id"], limit=5)}
        for theme in themes
    ]
    active = query(
        "SELECT ri.*, c.name AS challenge_name, t.name AS theme_name "
        "FROM running_instance ri "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "WHERE ri.user_id = ? AND ri.status = 'in_progress' "
        "ORDER BY ri.started_at DESC LIMIT 1",
        (user["user_id"],),
        one=True,
    )
    recent = query(
        "SELECT ri.status, ri.started_at, ri.ended_at, ri.duration_seconds, "
        "       c.name AS challenge_name, t.name AS theme_name "
        "FROM running_instance ri "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "WHERE ri.user_id = ? AND ri.status != 'in_progress' "
        "ORDER BY ri.ended_at DESC LIMIT 6",
        (user["user_id"],),
    )
    return render_template(
        "dashboard.html",
        stats=user_stats(user["user_id"]),
        overall=overall_leaderboard(limit=15),
        boards=boards,
        active=active,
        recent=recent,
    )
