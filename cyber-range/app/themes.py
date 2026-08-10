"""Themes, challenges, VM instances and flag submission.

VOCABULARY
    theme      a subject area (networking, forensics, web). Holds six challenges.
    challenge  one exercise inside a theme, backed by its own VM.
    instance   one student's live run at one challenge.

The core of the platform. One instance runs like this:

    launch()   POST  clone a VM, write active_vm + running_instance, redirect
    timer()    GET   polled by static/js/session.js; the SERVER owns the clock
    flag()     POST  grade a submission, auto-close on the last flag
    console()  GET   ownership-checked redirect to the hypervisor
    close()    POST  stop the clock, record complete/abandoned, destroy the VM

THERE IS NO SEPARATE SESSION PAGE. A running challenge is shown inline on its
own tile on the theme page: timer, console button, flag form and progress all
appear in place, and every action here redirects back to detail(). Keep it that
way when adding to this module — a student should never be navigated away from
the theme to work a challenge.

Only one instance may be in progress per user; launch() enforces that by sending
anyone with a live one back to it. That keeps one student from holding six VMs
at once, which matters when the cluster has twenty.

Every route that touches an instance goes through _owned_instance(), which 404s
on someone else's. A 404 rather than a 403 so the URL space cannot be probed for
which instance ids exist. ANY new instance route must use it too — it is the
only thing standing between a student and another student's machine.

ADDING A THEME OR CHALLENGE: no code change. They are rows; see app/seed.py.
ADDING A ROUTE HERE: @bp.route -> @login_required -> _owned_instance() ->
throttle if it can be hammered -> audit.record() if it changes state.
"""

import secrets
from datetime import datetime

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
)

from . import audit, throttle
from .auth import login_required
from .db import execute, get_db, query, utcnow
from .proxmox import ProxmoxError, clone_and_start, stop_and_destroy
from .scoring import (
    FLAG_HIT, FLAG_MISS, FLAG_REPEAT, challenge_progress, submit_flag,
    theme_challenge_matrix, theme_leaderboard,
)
from .security import parse_ts

bp = Blueprint("themes", __name__, url_prefix="/themes")


def _owned_instance(instance_id):
    """Fetch an instance, or 404 unless it belongs to the signed-in user.

    Returns the instance joined to its challenge, theme and VM, so views rarely
    need a second query. Call it at the TOP of every instance route, before
    reading anything from the request.
    """
    instance = query(
        "SELECT ri.*, c.name AS challenge_name, c.brief, c.challenge_number, c.difficulty, "
        "       t.name AS theme_name, av.console_url, av.proxmox_vmid, av.node, "
        "       av.status AS vm_status "
        "FROM running_instance ri "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "LEFT JOIN active_vm av ON av.active_vm_id = ri.active_vm_id "
        "WHERE ri.instance_id = ?",
        (instance_id,),
        one=True,
    )
    if instance is None or instance["user_id"] != g.user["user_id"]:
        abort(404)
    return instance


def _back(theme_id: int, challenge_id: int) -> str:
    """Where every action returns to: the theme page, scrolled to the tile.

    There is no session page to go back to, so the anchor is what keeps a
    student in place after submitting a flag rather than dumping them at the top
    of a long scoreboard.
    """
    return url_for("themes.detail", theme_id=theme_id) + f"#challenge-{challenge_id}"


def _active_instance(user_id):
    """The user's in-progress instance, if any. None means they can launch."""
    return query(
        "SELECT * FROM running_instance WHERE user_id = ? AND status = 'in_progress' "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id,),
        one=True,
    )


# ------------------------------------------------------------------ listing

@bp.route("/")
@login_required
def index():
    themes = query("SELECT * FROM theme ORDER BY sort_order, theme_id")
    cards = []
    for theme in themes:
        progress = query(
            "SELECT (SELECT COUNT(*) FROM challenge_points WHERE theme_id = ?) AS flags_total, "
            "       (SELECT COUNT(*) FROM user_challenge_points "
            "         WHERE theme_id = ? AND user_id = ?) AS flags_found",
            (theme["theme_id"], theme["theme_id"], g.user["user_id"]),
            one=True,
        )
        cards.append({"theme": theme, "progress": progress})
    return render_template("themes.html", cards=cards)


@bp.route("/<int:theme_id>")
@login_required
def detail(theme_id):
    theme = query("SELECT * FROM theme WHERE theme_id = ?", (theme_id,), one=True)
    if theme is None:
        abort(404)

    challenges = query(
        "SELECT c.*, v.name AS vm_name FROM challenge c "
        "LEFT JOIN vm v ON v.vm_id = c.vm_id "
        "WHERE c.theme_id = ? ORDER BY c.challenge_number",
        (theme_id,),
    )
    active = _active_instance(g.user["user_id"])

    # When the running challenge belongs to THIS theme, its tile becomes the
    # live panel: timer, console, flag form and progress, all in place. That is
    # what replaced the old standalone session page.
    live = _live_panel(active) if active and active["theme_id"] == theme_id else None

    tiles = [
        {
            "challenge": challenge,
            "progress": challenge_progress(g.user["user_id"], challenge["challenge_id"]),
            "live": live if live and live["instance"]["challenge_id"] == challenge["challenge_id"]
                    else None,
        }
        for challenge in challenges
    ]
    return render_template(
        "theme_detail.html",
        theme=theme,
        tiles=tiles,
        matrix=theme_challenge_matrix(theme_id),
        board=theme_leaderboard(theme_id, limit=10),
        active=active,
    )


def _live_panel(active_row):
    """Everything the inline panel for a running challenge needs.

    Pulled together here rather than in the template so detail() stays a single
    pass and the template stays presentation only.
    """
    running = _owned_instance(active_row["instance_id"])
    return {
        "instance": running,
        "progress": challenge_progress(g.user["user_id"], running["challenge_id"]),
        "flags": query(
            "SELECT cp.flag_id, cp.label, cp.points, "
            "       (SELECT 1 FROM user_challenge_points ucp "
            "         WHERE ucp.flag_id = cp.flag_id AND ucp.user_id = ?) AS captured "
            "FROM challenge_points cp WHERE cp.challenge_id = ? "
            "ORDER BY cp.points, cp.flag_id",
            (g.user["user_id"], running["challenge_id"]),
        ),
        "attempts": query(
            "SELECT submitted_at, was_correct FROM flag_submission "
            "WHERE user_id = ? AND challenge_id = ? ORDER BY submitted_at DESC LIMIT 5",
            (g.user["user_id"], running["challenge_id"]),
        ),
    }


# ------------------------------------------------------------------- launch

@bp.route("/challenges/<int:challenge_id>/launch", methods=("POST",))
@login_required
def launch(challenge_id):
    challenge = query(
        "SELECT c.*, v.vm_id, v.template_vmid, v.proxmox_node, v.name AS vm_name "
        "FROM challenge c LEFT JOIN vm v ON v.vm_id = c.vm_id WHERE c.challenge_id = ?",
        (challenge_id,),
        one=True,
    )
    if challenge is None:
        abort(404)

    existing = _active_instance(g.user["user_id"])
    if existing:
        if existing["challenge_id"] == challenge_id:
            return redirect(_back(existing["theme_id"], existing["challenge_id"]))
        flash(
            "You already have a challenge running. Finish or close it before starting another.",
            "info",
        )
        return redirect(_back(existing["theme_id"], existing["challenge_id"]))

    if challenge["vm_id"] is None:
        flash("No VM template is mapped to this challenge yet.", "error")
        return redirect(url_for("themes.detail", theme_id=challenge["theme_id"]))

    label = f"{g.user['username']}-c{challenge['challenge_number']}"
    try:
        clone = clone_and_start(challenge["template_vmid"], challenge["proxmox_node"], label)
    except ProxmoxError as exc:
        flash(f"The hypervisor could not start this challenge: {exc}", "error")
        return redirect(url_for("themes.detail", theme_id=challenge["theme_id"]))

    active_vm_id = execute(
        "INSERT INTO active_vm (vm_id, proxmox_vmid, node, console_url, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (challenge["vm_id"], clone.vmid, clone.node, clone.console_url, clone.status),
    )
    instance_id = execute(
        "INSERT INTO running_instance "
        "(user_id, theme_id, challenge_id, active_vm_id, access_key) VALUES (?, ?, ?, ?, ?)",
        (
            g.user["user_id"],
            challenge["theme_id"],
            challenge_id,
            active_vm_id,
            secrets.token_urlsafe(24),
        ),
    )
    audit.record(
        audit.INSTANCE_LAUNCH,
        user_id=g.user["user_id"],
        username=g.user["username"],
        detail=f"challenge {challenge_id}, vmid {clone.vmid}",
    )
    return redirect(_back(challenge["theme_id"], challenge_id))


@bp.route("/session/<int:instance_id>/console")
@login_required
def console(instance_id):
    """Ownership-checked hop to the hypervisor console.

    The console URL is never rendered into the page, so it cannot be copied out
    of the HTML, pulled from a bookmark, or leaked in a Referer header. Note
    this still does not authenticate the student TO Proxmox — see the README; a
    ticket-issuing proxy is the remaining piece.
    """
    instance = _owned_instance(instance_id)
    if instance["status"] != "in_progress" or not instance["console_url"]:
        flash("That session is closed, so its machine is gone.", "info")
        return redirect(url_for("themes.detail", theme_id=instance["theme_id"]))
    return redirect(instance["console_url"])


# --------------------------------------------------------- live instance

@bp.route("/session/<int:instance_id>/flag", methods=("POST",))
@login_required
def flag(instance_id):
    running = _owned_instance(instance_id)
    if running["status"] != "in_progress":
        flash("That session is closed. Launch the challenge again to keep going.", "info")
        return redirect(_back(running["theme_id"], running["challenge_id"]))

    allowed, wait = throttle.hit(
        "flag_challenge", f"{g.user['user_id']}:{running['challenge_id']}"
    )
    if not allowed:
        # Without this, a script can brute-force a short flag in seconds.
        audit.record(audit.FLAG_THROTTLED, user_id=g.user["user_id"],
                     username=g.user["username"],
                     detail=f"challenge {running['challenge_id']}")
        flash(f"Too many flag attempts on this challenge. Try again in {wait} seconds.", "error")
        return redirect(_back(running["theme_id"], running["challenge_id"]))

    submitted = request.form.get("flag", "").strip()[:200]
    if not submitted:
        flash("Enter a flag first.", "error")
        return redirect(_back(running["theme_id"], running["challenge_id"]))

    result, points, label = submit_flag(
        g.user["user_id"], running["challenge_id"], instance_id, submitted
    )
    if result == FLAG_HIT:
        flash(f"Correct — {label} is worth {points} points.", "success")
        progress = challenge_progress(g.user["user_id"], running["challenge_id"])
        if progress["complete"]:
            _close(instance_id, "complete")
            flash("Every flag in this challenge is captured. Marked complete.", "success")
            return redirect(url_for("themes.detail", theme_id=running["theme_id"]))
    elif result == FLAG_REPEAT:
        flash(f"You already have the points for {label}.", "info")
    elif result == FLAG_MISS:
        flash("That flag does not match anything in this challenge.", "error")

    return redirect(_back(running["theme_id"], running["challenge_id"]))


@bp.route("/session/<int:instance_id>/close", methods=("POST",))
@login_required
def close(instance_id):
    running = _owned_instance(instance_id)
    if running["status"] != "in_progress":
        return redirect(url_for("themes.detail", theme_id=running["theme_id"]))

    requested = request.form.get("outcome", "abandoned")
    progress = challenge_progress(g.user["user_id"], running["challenge_id"])
    status = "complete" if (requested == "complete" and progress["complete"]) else "abandoned"

    _close(instance_id, status)
    if status == "complete":
        flash("Closed and recorded as complete.", "success")
    else:
        flash(
            f"Closed and recorded as abandoned "
            f"({progress['flags_found']} of {progress['flags_total']} flags captured).",
            "info",
        )
    return redirect(url_for("themes.detail", theme_id=running["theme_id"]))


@bp.route("/session/<int:instance_id>/timer")
@login_required
def timer(instance_id):
    """Elapsed seconds, polled by the page so the clock survives a reload."""
    running = _owned_instance(instance_id)
    started = parse_ts(running["started_at"])
    ended = parse_ts(running["ended_at"]) or datetime.utcnow()
    return jsonify(
        {
            "status": running["status"],
            "elapsed_seconds": max(0, int((ended - started).total_seconds())),
        }
    )


def _close(instance_id: int, status: str):
    """Stop the clock, record the outcome and tear the VM down.

    status is 'complete' or 'abandoned' — the only two outcomes the spec asks
    for. Called from three places: the close() route, automatically from flag()
    when the last flag is captured, and from the staff console.

    Ordering is deliberate: the instance row and the VM row are updated and
    committed BEFORE the hypervisor is asked to destroy anything. If Proxmox is
    unreachable, the student still keeps their result and their time, and the
    orphaned clone is flagged 'error' for staff. Do not reorder this to "tidy
    up first".
    """
    running = query(
        "SELECT ri.*, av.proxmox_vmid, av.node FROM running_instance ri "
        "LEFT JOIN active_vm av ON av.active_vm_id = ri.active_vm_id "
        "WHERE ri.instance_id = ?",
        (instance_id,),
        one=True,
    )
    started = parse_ts(running["started_at"])
    duration = max(0, int((datetime.utcnow() - started).total_seconds()))

    db = get_db()
    db.execute(
        "UPDATE running_instance SET ended_at = ?, duration_seconds = ?, status = ? "
        "WHERE instance_id = ?",
        (utcnow(), duration, status, instance_id),
    )
    if running["active_vm_id"]:
        db.execute(
            "UPDATE active_vm SET status = 'stopped', stopped_at = ? WHERE active_vm_id = ?",
            (utcnow(), running["active_vm_id"]),
        )
    db.commit()

    audit.record(
        audit.INSTANCE_CLOSE,
        user_id=running["user_id"],
        detail=f"instance {instance_id}, {status}, {duration}s",
    )

    if running["proxmox_vmid"]:
        try:
            stop_and_destroy(running["proxmox_vmid"], running["node"])
        except ProxmoxError as exc:
            db.execute(
                "UPDATE active_vm SET status = 'error' WHERE active_vm_id = ?",
                (running["active_vm_id"],),
            )
            db.commit()
            print(f"[proxmox] teardown failed for vmid {running['proxmox_vmid']}: {exc}")
