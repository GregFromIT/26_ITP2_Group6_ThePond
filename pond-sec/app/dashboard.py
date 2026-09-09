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

from db.orm import db
from db.challenge_models import Challenge
from db.VMs_models import VMTemplate
from db.runtime_models import ChallengeInstance

from .auth import login_required
from .scoring import category_leaderboard, overall_leaderboard, user_stats
from .themes import DEFAULT_CATEGORY, _theme_dict

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def landing():
    if g.get("user"):
        return redirect(url_for("dashboard.index"))
    counts = {
        "themes": db.session.query(db.func.count(db.func.distinct(Challenge.category))).scalar(),
        "challenges": db.session.query(db.func.count(Challenge.challenge_id)).scalar(),
        "vms": db.session.query(db.func.count(VMTemplate.template_id)).scalar(),
    }
    return render_template("landing.html", counts=counts)


@bp.route("/dashboard")
@login_required
def index():
    user = g.user
    category_counts = dict(
        db.session.query(Challenge.category, db.func.count(Challenge.challenge_id))
        .filter(Challenge.status == "published")
        .group_by(Challenge.category)
        .all()
    )
    boards = [
        {"theme": _theme_dict(category or DEFAULT_CATEGORY, count),
         "rows": category_leaderboard(category or DEFAULT_CATEGORY, limit=5)}
        for category, count in sorted(category_counts.items(), key=lambda kv: kv[0] or "")
    ]
    active_ci = (
        db.session.query(ChallengeInstance, Challenge)
        .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
        .filter(ChallengeInstance.user_id == user["user_id"], ChallengeInstance.status == "running")
        .order_by(ChallengeInstance.started_at.desc())
        .first()
    )
    active = None
    if active_ci:
        ci, challenge = active_ci
        active = {
            "instance_id": ci.instance_id,
            "theme_id": challenge.category or DEFAULT_CATEGORY,
            "theme_name": (challenge.category or DEFAULT_CATEGORY).replace("_", " ").title(),
            "challenge_id": ci.challenge_id,
            "challenge_name": challenge.title,
        }
    recent_rows = (
        db.session.query(ChallengeInstance, Challenge)
        .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
        .filter(ChallengeInstance.user_id == user["user_id"], ChallengeInstance.status != "running")
        .order_by(ChallengeInstance.stopped_at.desc())
        .limit(6)
        .all()
    )
    recent = [
        {
            "status": ci.status,
            "started_at": ci.started_at,
            "ended_at": ci.completed_at or ci.stopped_at,
            "duration_seconds": max(0, int(((ci.completed_at or ci.stopped_at) - ci.started_at).total_seconds()))
                                 if ci.started_at and (ci.completed_at or ci.stopped_at) else None,
            "challenge_name": challenge.title,
            "theme_name": (challenge.category or DEFAULT_CATEGORY).replace("_", " ").title(),
        }
        for ci, challenge in recent_rows
    ]
    return render_template(
        "dashboard.html",
        stats=user_stats(user["user_id"]),
        overall=overall_leaderboard(limit=15),
        boards=boards,
        active=active,
        recent=recent,
    )
