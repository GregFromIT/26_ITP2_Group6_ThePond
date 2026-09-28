"""Leaderboards and flag grading.

A submitted flag is hashed and matched against ChallengeFlag; a match writes
one row to UserSolve (the UNIQUE(user_id, flag_id) constraint is the lock
against claiming the same flag twice). There is no denormalised points
column anywhere anymore - every leaderboard computes SUM(UserSolve.awarded_points)
on read, which is the honest trade the real schema makes: one extra
aggregation per read, in exchange for a total that can never drift from
the ledger that produced it.

THEME -> CATEGORY: db/challenge_models.py's Challenge has no theme concept -
no weighting, no curated name/summary/tile image, no fixed six-challenges-
per-theme structure. What it has is a plain `category` string. Every
theme-shaped thing below (leaderboards, the matrix, the challenge listing)
is now grouped by that string instead, with weighting fixed at 1.0 - there
is nowhere left to store a custom one. themes.py's _theme_dict() fabricates
a display shim around a category so most of the old templates render
unchanged; only the challenge-count matrix has no way to stay the same
shape, since it isn't a fixed six columns anymore - see
category_challenge_matrix() and the rewritten table in theme_detail.html.
"""

import hashlib

from db.orm import db
from db.challenge_models import Challenge
from db.VMs_models import ChallengeFlag, VMTemplate
from db.runtime_models import ChallengeInstance
from db.scoring_models import FlagSubmission, UserSolve
from db.user_models import Role, User

# Results returned by submit_flag(). Views branch on these rather than on
# truthiness, because "wrong flag" and "right flag, already claimed" need
# different messages and neither is an error.
FLAG_MISS = "miss"
FLAG_HIT = "hit"
FLAG_REPEAT = "repeat"

# Administrators are excluded from every board; moderators DO compete - same
# rule as before, now checked against the real role vocabulary
# ('sysadmin', not 'admin' - see identity.py's ROLE_NAME_TO_APP).
_EXCLUDED_ROLE = "sysadmin"


def hash_flag(flag: str) -> str:
    """Normalise then hash. Case and stray whitespace never decide a
    submission. db/init_database.py's challenge-seeding step uses this same
    normalisation - if you ever change it, every stored flag hash becomes
    wrong and must be reseeded; treat it as a schema change."""
    return hashlib.sha256(flag.strip().lower().encode()).hexdigest()


# ------------------------------------------------------------------ grading

def submit_flag(user_id: int, challenge_id: int, instance_id: int, raw_flag: str):
    """Grade one submission. Returns (result, points, label).

    The order here matters:

      1. look for a matching flag IN THIS CHALLENGE (joined through
         VMTemplate, since ChallengeFlag belongs to a template, not
         directly to a challenge - a multi-VM challenge's flags all still
         resolve back to the one challenge_id via their template)
      2. log the attempt either way - right and wrong both count as "flags
         played" on the leaderboards
      3. check the award ledger (UserSolve) before paying out
      4. write the award in the SAME transaction as the submission log

    The UNIQUE(user_id, flag_id) constraint on UserSolve is what makes
    double-claiming impossible even if two submissions arrive at the same
    instant - the second one's insert would raise, not pay out twice. (Not
    exercised under real concurrency here - see the note in the status
    summary this shipped with.)
    """
    match = (
        db.session.query(ChallengeFlag)
        .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
        .filter(
            VMTemplate.challenge_id == challenge_id,
            ChallengeFlag.flag_hash == hash_flag(raw_flag),
            ChallengeFlag.is_active.is_(True),
        )
        .one_or_none()
    )

    already = None
    if match is not None:
        already = (
            db.session.query(UserSolve)
            .filter_by(user_id=user_id, flag_id=match.flag_id)
            .one_or_none()
        )

    db.session.add(FlagSubmission(
        user_id=user_id,
        instance_id=instance_id,
        matched_flag_id=match.flag_id if match else None,
        was_correct=match is not None,
        was_already_solved=already is not None,
    ))

    if match is None:
        db.session.commit()
        return FLAG_MISS, 0, None

    if already is not None:
        db.session.commit()
        return FLAG_REPEAT, 0, match.flag_name

    db.session.add(UserSolve(
        user_id=user_id, flag_id=match.flag_id, instance_id=instance_id,
        awarded_points=match.points,
    ))
    db.session.commit()
    return FLAG_HIT, match.points, match.flag_name


def challenge_progress(user_id: int, challenge_id: int):
    """Flags captured vs available for one user in one challenge."""
    total = (
        db.session.query(db.func.count(ChallengeFlag.flag_id), db.func.coalesce(db.func.sum(ChallengeFlag.points), 0))
        .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
        .filter(VMTemplate.challenge_id == challenge_id, ChallengeFlag.is_active.is_(True))
        .one()
    )
    mine = (
        db.session.query(db.func.count(UserSolve.solve_id), db.func.coalesce(db.func.sum(UserSolve.awarded_points), 0))
        .join(ChallengeFlag, UserSolve.flag_id == ChallengeFlag.flag_id)
        .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
        .filter(VMTemplate.challenge_id == challenge_id, UserSolve.user_id == user_id)
        .one()
    )
    flags_total, points_total = total
    flags_found, points_earned = mine
    return {
        "flags_total": flags_total,
        "flags_found": flags_found,
        "points_total": points_total,
        "points_earned": points_earned,
        "complete": flags_total > 0 and flags_found >= flags_total,
    }


# ------------------------------------------------------------- leaderboards

def _ranked(rows):
    """Attach a 1-based rank, sharing a rank on equal scores.

    Ties share a rank and the next rank skips (1, 2, 2, 4) - standard
    competition ranking. Every leaderboard function ends by calling this,
    so ranking behaviour only ever needs changing in one place.
    """
    ranked = []
    previous_score = None
    previous_rank = 0
    for position, row in enumerate(rows, start=1):
        data = dict(row)
        if data["score"] == previous_score:
            data["rank"] = previous_rank
        else:
            data["rank"] = position
            previous_rank = position
            previous_score = data["score"]
        ranked.append(data)
    return ranked


def _eligible_users_query():
    return (
        db.session.query(User)
        .join(Role, User.role_id == Role.role_id)
        .filter(Role.role_name != _EXCLUDED_ROLE)
    )


def overall_leaderboard(limit: int = 25):
    rows = []
    for user in _eligible_users_query().all():
        solves = db.session.query(UserSolve).filter_by(user_id=user.user_id).all()
        submissions = db.session.query(FlagSubmission).filter_by(user_id=user.user_id).count()
        score = sum(s.awarded_points for s in solves)
        last_solved = max((s.solved_at for s in solves), default=None)
        rows.append({
            "user_id": user.user_id, "username": user.username, "score": score,
            "last_solved": last_solved, "solved_count": len(solves), "flags_played": submissions,
        })
    rows.sort(key=lambda r: (-r["score"], r["last_solved"] is None, r["last_solved"] or "", r["username"]))
    return _ranked(rows[:limit])


def category_leaderboard(category: str, limit: int = 10):
    """Replaces theme_leaderboard(theme_id, ...). Same shape (score, rank,
    last_solved, solved_count, flags_played), grouped by Challenge.category
    instead of a theme row - see the module docstring."""
    rows = []
    for user in _eligible_users_query().all():
        solves = (
            db.session.query(UserSolve)
            .join(ChallengeFlag, UserSolve.flag_id == ChallengeFlag.flag_id)
            .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
            .join(Challenge, VMTemplate.challenge_id == Challenge.challenge_id)
            .filter(UserSolve.user_id == user.user_id, Challenge.category == category)
            .all()
        )
        if not solves:
            continue
        score = sum(s.awarded_points for s in solves)
        if score <= 0:
            continue
        submissions = (
            db.session.query(FlagSubmission)
            .join(ChallengeInstance, FlagSubmission.instance_id == ChallengeInstance.instance_id)
            .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
            .filter(FlagSubmission.user_id == user.user_id, Challenge.category == category)
            .count()
        )
        rows.append({
            "user_id": user.user_id, "username": user.username, "score": score,
            "last_solved": max(s.solved_at for s in solves),
            "solved_count": len(solves), "flags_played": submissions,
        })
    rows.sort(key=lambda r: (-r["score"], r["last_solved"] is None, r["last_solved"] or "", r["username"]))
    return _ranked(rows[:limit])


def category_challenge_matrix(category: str, limit: int = 25):
    """Replaces theme_challenge_matrix(). The old version hard-coded six
    C1-C6 columns because the CHECK constraint on the old schema's
    challenge_number enforced exactly six challenges per theme. Nothing in
    the real schema enforces (or even has) that number anymore, so this
    returns a dynamic column list alongside the rows instead:

        {"challenges": [(challenge_id, title), ...],
         "rows": [{"user_id", "username", "per_challenge": {challenge_id: points},
                    "raw_total", "score", "rank", "last_solved"}, ...]}

    theme_detail.html iterates `challenges` for the header and looks up
    `row['per_challenge'][challenge_id]` for each cell - see that template
    for the other half of this change. Weighting is fixed at 1.0 (see the
    module docstring), so score == raw_total; the column is kept in the
    return shape rather than removed, in case a weighting field gets added
    to Challenge later.
    """
    challenges = (
        db.session.query(Challenge)
        .filter(Challenge.category == category)
        .order_by(Challenge.challenge_id)
        .all()
    )
    challenge_cols = [(c.challenge_id, c.title) for c in challenges]

    rows = []
    for user in _eligible_users_query().all():
        solves = (
            db.session.query(UserSolve, Challenge.challenge_id)
            .join(ChallengeFlag, UserSolve.flag_id == ChallengeFlag.flag_id)
            .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
            .join(Challenge, VMTemplate.challenge_id == Challenge.challenge_id)
            .filter(UserSolve.user_id == user.user_id, Challenge.category == category)
            .all()
        )
        if not solves:
            continue
        per_challenge = {}
        for solve, challenge_id in solves:
            per_challenge[challenge_id] = per_challenge.get(challenge_id, 0) + solve.awarded_points
        raw_total = sum(per_challenge.values())
        last_solved = max(solve.solved_at for solve, _ in solves)
        rows.append({
            "user_id": user.user_id, "username": user.username,
            "per_challenge": per_challenge, "raw_total": raw_total,
            "weighting": 1.0, "score": raw_total, "last_solved": last_solved,
        })
    rows.sort(key=lambda r: (-r["score"], r["last_solved"]))
    return {"challenges": challenge_cols, "rows": _ranked(rows[:limit])}


def user_stats(user_id: int):
    """Everything the dashboard header shows for one user."""
    user = db.session.get(User, user_id)
    solves = db.session.query(UserSolve).filter_by(user_id=user_id).all()
    score = sum(s.awarded_points for s in solves)
    flags_played = db.session.query(FlagSubmission).filter_by(user_id=user_id).count()
    challenges_complete = (
        db.session.query(ChallengeInstance)
        .filter_by(user_id=user_id, status="complete")
        .count()
    )
    challenges_abandoned = (
        db.session.query(ChallengeInstance)
        .filter_by(user_id=user_id, status="abandoned")
        .count()
    )
    last_solved = max((s.solved_at for s in solves), default=None)

    data = {
        "score": score,
        "flags_captured": len(solves),
        "flags_played": flags_played,
        "challenges_complete": challenges_complete,
        "challenges_abandoned": challenges_abandoned,
        "last_solved": last_solved,
    }

    # Administrators are not on the boards, so they have no rank either -
    # the dashboard renders a dash rather than a misleading position.
    data["field_size"] = _eligible_users_query().count()

    if user.role.role_name == _EXCLUDED_ROLE:
        data["rank"] = None
        return data

    higher_scorers = 0
    for other in _eligible_users_query().all():
        if other.user_id == user_id:
            continue
        other_score = sum(
            s.awarded_points for s in db.session.query(UserSolve).filter_by(user_id=other.user_id).all()
        )
        if other_score > score:
            higher_scorers += 1
    data["rank"] = higher_scorers + 1
    return data