"""Leaderboards and flag grading.

The scoring path follows the DB outline: a submitted flag is hashed and matched
against challenge_points; a match writes one row to user_challenge_points (the
UNIQUE constraint is the lock against claiming the same flag twice) and adds the
points to user.points, which is what every leaderboard reads.
"""

import hashlib

from .db import execute, get_db, query, utcnow

# Results returned by submit_flag(). Views branch on these rather than on
# truthiness, because "wrong flag" and "right flag, already claimed" need
# different messages and neither is an error.
FLAG_MISS = "miss"
FLAG_HIT = "hit"
FLAG_REPEAT = "repeat"


def hash_flag(flag: str) -> str:
    """Normalise then hash.

    seed.py calls this too, which is what keeps stored flags and submitted flags
    consistent. If you ever change the normalisation, EVERY stored flag hash
    becomes wrong and must be regenerated — treat it as a schema change.
    """
    """Normalise then hash. Case and stray whitespace never decide a submission."""
    return hashlib.sha256(flag.strip().lower().encode()).hexdigest()


# ------------------------------------------------------------------ grading

def submit_flag(user_id: int, challenge_id: int, instance_id, raw_flag: str):
    """Grade one submission. Returns (result, points, label).

    The order here matters:

      1. look for a matching flag IN THIS CHALLENGE (the challenge_id filter is
         what stops a flag from challenge 1 scoring in challenge 6)
      2. log the attempt either way — right and wrong both count as "flags
         played" on the leaderboards
      3. check the award ledger before paying out
      4. write the award and bump the user's total in ONE transaction

    Step 4 is deliberately not two execute() calls: a crash between them would
    leave an award with no points or points with no award. The UNIQUE(user_id,
    flag_id) constraint on the ledger is what makes double-claiming impossible
    even if two submissions arrive at the same instant — the second one raises
    rather than paying twice.
    """
    match = query(
        "SELECT flag_id, theme_id, label, points FROM challenge_points "
        "WHERE challenge_id = ? AND flag_hash = ?",
        (challenge_id, hash_flag(raw_flag)),
        one=True,
    )

    execute(
        "INSERT INTO flag_submission (user_id, challenge_id, instance_id, was_correct) "
        "VALUES (?, ?, ?, ?)",
        (user_id, challenge_id, instance_id, 1 if match else 0),
    )

    if match is None:
        return FLAG_MISS, 0, None

    already = query(
        "SELECT 1 FROM user_challenge_points WHERE user_id = ? AND flag_id = ?",
        (user_id, match["flag_id"]),
        one=True,
    )
    if already:
        return FLAG_REPEAT, 0, match["label"]

    db = get_db()
    db.execute(
        "INSERT INTO user_challenge_points "
        "(user_id, flag_id, theme_id, challenge_id, points_awarded, awarded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, match["flag_id"], match["theme_id"], challenge_id, match["points"], utcnow()),
    )
    db.execute(
        "UPDATE user SET points = points + ? WHERE user_id = ?",
        (match["points"], user_id),
    )
    db.commit()
    return FLAG_HIT, match["points"], match["label"]


def challenge_progress(user_id: int, challenge_id: int):
    """Flags captured vs available for one user in one challenge."""
    total = query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(points), 0) AS pts "
        "FROM challenge_points WHERE challenge_id = ?",
        (challenge_id,),
        one=True,
    )
    mine = query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(points_awarded), 0) AS pts "
        "FROM user_challenge_points WHERE user_id = ? AND challenge_id = ?",
        (user_id, challenge_id),
        one=True,
    )
    return {
        "flags_total": total["n"],
        "flags_found": mine["n"],
        "points_total": total["pts"],
        "points_earned": mine["pts"],
        "complete": total["n"] > 0 and mine["n"] >= total["n"],
    }


# ------------------------------------------------------------- leaderboards

def _ranked(rows):
    """Attach a 1-based rank, sharing a rank on equal scores.

    Ties share a rank and the next rank skips (1, 2, 2, 4) — standard
    competition ranking. Every leaderboard function ends by calling this, so
    ranking behaviour only ever needs changing in one place.
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


# ADDING A LEADERBOARD:
#   * if it is a straight read, add a VIEW in schema.sql and a thin function
#     here that orders it and calls _ranked() — that is what the two below do
#   * if it needs a pivot or a computed weighting, write the query here instead
#     (theme_challenge_matrix is the example)
# Sorting is always: score desc, then earliest last_solved, then username. That
# rewards whoever got there first on a tie; keep it consistent across boards or
# students will notice the boards disagree.


def overall_leaderboard(limit: int = 25):
    rows = query(
        "SELECT * FROM leaderboard_overall "
        "ORDER BY score DESC, last_solved IS NULL, last_solved ASC, username ASC "
        "LIMIT ?",
        (limit,),
    )
    return _ranked(rows)


def theme_leaderboard(theme_id: int, limit: int = 10):
    rows = query(
        "SELECT * FROM leaderboard_theme WHERE theme_id = ? AND score > 0 "
        "ORDER BY score DESC, last_solved IS NULL, last_solved ASC, username ASC "
        "LIMIT ?",
        (theme_id, limit),
    )
    return _ranked(rows)


def theme_challenge_matrix(theme_id: int, limit: int = 25):
    """Per-challenge breakdown: rank, user, C1-C6, total, weighted total.

    The six CASE columns are hard-coded because the scoreboard in the spec has
    six fixed columns and the CHECK constraint on challenge.challenge_number
    enforces that. If challenges per theme ever becomes variable, this query and
    theme_detail.html both need rewriting to build columns dynamically — it is
    the one place the "six challenges" assumption is baked into code.

    Weighting is applied HERE, on read, not stored. Changing a challenge's
    weighting therefore takes effect immediately and rescores nobody.
    """
    rows = query(
        """
        SELECT u.user_id,
               u.username,
               COALESCE(SUM(CASE WHEN c.challenge_number = 1 THEN ucp.points_awarded END), 0) AS r1,
               COALESCE(SUM(CASE WHEN c.challenge_number = 2 THEN ucp.points_awarded END), 0) AS r2,
               COALESCE(SUM(CASE WHEN c.challenge_number = 3 THEN ucp.points_awarded END), 0) AS r3,
               COALESCE(SUM(CASE WHEN c.challenge_number = 4 THEN ucp.points_awarded END), 0) AS r4,
               COALESCE(SUM(CASE WHEN c.challenge_number = 5 THEN ucp.points_awarded END), 0) AS r5,
               COALESCE(SUM(CASE WHEN c.challenge_number = 6 THEN ucp.points_awarded END), 0) AS r6,
               COALESCE(SUM(ucp.points_awarded), 0) AS raw_total,
               MAX(ucp.awarded_at) AS last_solved
          FROM user_challenge_points ucp
          JOIN challenge c ON c.challenge_id = ucp.challenge_id
          JOIN user u ON u.user_id = ucp.user_id
         WHERE ucp.theme_id = ?
           AND u.role != 'admin'          -- see the eligibility note in schema.sql
      GROUP BY u.user_id
      ORDER BY raw_total DESC, last_solved ASC
         LIMIT ?
        """,
        (theme_id, limit),
    )
    weighting = query(
        "SELECT weighting FROM theme WHERE theme_id = ?", (theme_id,), one=True
    )["weighting"]

    scored = []
    for row in rows:
        data = dict(row)
        data["weighting"] = weighting
        data["score"] = int(round(data["raw_total"] * weighting))
        scored.append(data)
    scored.sort(key=lambda item: (-item["score"], item["last_solved"] or "9999"))
    return _ranked(scored)


def user_stats(user_id: int):
    """Everything the dashboard header shows for one user.

    One query with correlated subqueries rather than six round trips. If the
    dashboard grows more figures, add them to this SELECT rather than adding
    another call in the view.
    """
    stats = query(
        """
        SELECT u.points AS score,
               (SELECT COUNT(*) FROM user_challenge_points x WHERE x.user_id = u.user_id) AS flags_captured,
               (SELECT COUNT(*) FROM flag_submission       x WHERE x.user_id = u.user_id) AS flags_played,
               (SELECT COUNT(*) FROM running_instance      x WHERE x.user_id = u.user_id AND x.status = 'complete')  AS challenges_complete,
               (SELECT COUNT(*) FROM running_instance      x WHERE x.user_id = u.user_id AND x.status = 'abandoned') AS challenges_abandoned,
               (SELECT MAX(awarded_at) FROM user_challenge_points x WHERE x.user_id = u.user_id) AS last_solved
          FROM user u WHERE u.user_id = ?
        """,
        (user_id,),
        one=True,
    )
    data = dict(stats)

    # Administrators are not on the boards, so they have no rank either — the
    # dashboard renders a dash rather than a misleading position. Everyone else
    # is ranked against the eligible field only, so removing an admin does not
    # silently shift every student up a place.
    data["field_size"] = query(
        "SELECT COUNT(*) AS n FROM user WHERE role != 'admin'", one=True
    )["n"]

    role = query("SELECT role FROM user WHERE user_id = ?", (user_id,), one=True)["role"]
    if role == "admin":
        data["rank"] = None
        return data

    position = query(
        "SELECT COUNT(*) + 1 AS place FROM user u "
        "WHERE u.role != 'admin' "
        "AND u.points > (SELECT points FROM user WHERE user_id = ?)",
        (user_id,),
        one=True,
    )
    data["rank"] = position["place"]
    return data
