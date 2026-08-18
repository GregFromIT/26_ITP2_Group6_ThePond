"""Themes, challenges, VM instances and flag submission.

VOCABULARY (changed from the old schema - see the note below)
    category   Challenge.category, a plain string. Stands in for the old
               "theme" concept - see _theme_dict().
    challenge  one exercise, backed by one or more VMTemplates.
    instance   one student's live run at one challenge (ChallengeInstance).

The core of the platform. One instance runs like this:

    launch()   POST  clone a VM, write ChallengeInstance + VMInstance, redirect
    timer()    GET   polled by static/js/session.js; the SERVER owns the clock
    flag()     POST  grade a submission, auto-close on the last flag
    console()  GET   ownership-checked redirect to the hypervisor
    close()    POST  stop the clock, record complete/abandoned, destroy the VM

THERE IS NO SEPARATE SESSION PAGE. A running challenge is shown inline on its
own tile on the category page: timer, console button, flag form and progress
all appear in place, and every action here redirects back to detail(). Keep
it that way when adding to this module - a student should never be navigated
away from the category page to work a challenge.

Only one instance may be in progress per user; launch() enforces that by
sending anyone with a live one back to it. That keeps one student from
holding six VMs at once, which matters when the cluster has twenty.

Every route that touches an instance goes through _owned_instance(), which
404s on someone else's. A 404 rather than a 403 so the URL space cannot be
probed for which instance ids exist. ANY new instance route must use it too -
it is the only thing standing between a student and another student's
machine.

NO "THEME" MODEL: db/challenge_models.py's Challenge has a plain `category`
string, not a curated theme with its own name/summary/weighting/tile image
like the old schema had. _theme_dict() below fabricates a display-shaped
stand-in around a category so most of the old templates - themes.html,
most of theme_detail.html, dashboard.html - render completely unchanged.
The one place that could not stay the same shape is the per-challenge score
matrix, since the old one hard-coded six C1-C6 columns (the old schema's
challenge_number was CHECKed to 1-6 per theme; nothing enforces or even has
that number now) - see scoring.category_challenge_matrix() and the rewritten
table in theme_detail.html.

URL SHAPE CHANGED: routes that took <int:theme_id> now take a category
string instead (still passed as theme_id= in url_for(), so template call
sites did not need to change - only the route converters here did).

ADDING A CHALLENGE: no code change to this file. Add it to
vars/challenges/*.yml and reseed - see db/init_database.py.
ADDING A ROUTE HERE: @bp.route -> @login_required -> _owned_instance() ->
throttle if it can be hammered -> audit.record() if it changes state.
"""

import ssl
import threading
from datetime import datetime
from urllib.parse import quote

import websocket as ws_client
from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, url_for,
)
from flask_sock import Sock

from db.orm import db
from db.challenge_models import Challenge
from db.VMs_models import ChallengeFlag, VMTemplate
from db.runtime_models import ChallengeInstance, VMInstance

from . import audit, throttle
from .auth import login_required
from .proxmox import ProxmoxError, clone_and_start, get_console_ticket, stop_and_destroy
from .scoring import (
    FLAG_HIT, FLAG_MISS, FLAG_REPEAT, category_challenge_matrix, category_leaderboard,
    challenge_progress, submit_flag,
)

bp = Blueprint("themes", __name__, url_prefix="/themes")
sock = Sock()

DEFAULT_CATEGORY = "general"


def _theme_dict(category: str, challenge_count: int) -> dict:
    """A theme-shaped stand-in around a plain category string - see the
    module docstring for why this exists. weighting is fixed at 1.0: there
    is nowhere in db/challenge_models.py to store a custom one."""
    category = category or DEFAULT_CATEGORY
    return {
        "theme_id": category,
        "name": category.replace("_", " ").replace("-", " ").title(),
        "category": category,
        "summary": f"{challenge_count} challenge{'s' if challenge_count != 1 else ''} in this category.",
        "weighting": 1.0,
        "tile_image": None,
        "sort_order": 0,
    }


def _owned_instance(instance_id):
    """Fetch an instance, or 404 unless it belongs to the signed-in user.

    Returns a dict shaped like the old running_instance-joined-to-everything
    row, so the rest of this file (and every template) barely had to change.
    Call it at the TOP of every instance route, before reading anything from
    the request.
    """
    row = (
        db.session.query(ChallengeInstance, Challenge)
        .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
        .filter(ChallengeInstance.instance_id == instance_id)
        .one_or_none()
    )
    if row is None:
        abort(404)
    ci, challenge = row
    if ci.user_id != g.user["user_id"]:
        abort(404)

    vm = (
        db.session.query(VMInstance)
        .filter(VMInstance.instance_id == instance_id, VMInstance.deleted_at.is_(None))
        .order_by(VMInstance.created_at.desc())
        .first()
    )
    category = challenge.category or DEFAULT_CATEGORY
    return {
        "instance_id": ci.instance_id,
        "user_id": ci.user_id,
        "theme_id": category,
        "theme_name": category.replace("_", " ").title(),
        "challenge_id": ci.challenge_id,
        "challenge_name": challenge.title,
        "brief": challenge.description,
        "difficulty": challenge.difficulty,
        "status": ci.status,
        "started_at": ci.started_at,
        "ended_at": ci.completed_at or ci.stopped_at,
        "console_available": bool(vm and vm.status == "running"),
        "proxmox_vmid": vm.proxmox_vmid if vm else None,
        "node": vm.proxmox_node if vm else None,
        "vm_status": vm.status if vm else None,
    }


def _back(theme_id: str, challenge_id: int) -> str:
    """Where every action returns to: the category page, scrolled to the
    tile. There is no session page to go back to, so the anchor is what
    keeps a student in place after submitting a flag rather than dumping
    them at the top of a long scoreboard."""
    return url_for("themes.detail", theme_id=theme_id) + f"#challenge-{challenge_id}"


def _active_instance(user_id):
    """The user's in-progress instance, if any. None means they can launch."""
    ci = (
        db.session.query(ChallengeInstance)
        .filter_by(user_id=user_id, status="running")
        .order_by(ChallengeInstance.started_at.desc())
        .first()
    )
    return _owned_instance(ci.instance_id) if ci else None


def _live_panel(active_row):
    """Everything the inline panel for a running challenge needs: the
    instance itself, per-flag captured state, and recent attempts.

    Pulled together here rather than in the template so detail() stays a
    single pass and the template stays presentation only - same shape as
    before the migration ({"instance", "progress", "flags", "attempts"}).
    """
    from db.scoring_models import FlagSubmission, UserSolve

    running = _owned_instance(active_row["instance_id"])
    challenge_id = running["challenge_id"]

    flag_rows = (
        db.session.query(ChallengeFlag)
        .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
        .filter(VMTemplate.challenge_id == challenge_id, ChallengeFlag.is_active.is_(True))
        .order_by(ChallengeFlag.points, ChallengeFlag.flag_id)
        .all()
    )
    solved_ids = {
        s.flag_id for s in db.session.query(UserSolve.flag_id)
        .filter_by(user_id=g.user["user_id"]).all()
    }
    flags = [
        {"flag_id": f.flag_id, "label": f.flag_name, "points": f.points, "captured": f.flag_id in solved_ids}
        for f in flag_rows
    ]

    attempts = (
        db.session.query(FlagSubmission)
        .filter_by(user_id=g.user["user_id"], instance_id=active_row["instance_id"])
        .order_by(FlagSubmission.submitted_at.desc())
        .limit(5)
        .all()
    )
    attempts = [{"submitted_at": a.submitted_at, "was_correct": a.was_correct} for a in attempts]

    return {
        "instance": running,
        "progress": challenge_progress(g.user["user_id"], challenge_id),
        "flags": flags,
        "attempts": attempts,
    }


# ------------------------------------------------------------------ listing

@bp.route("/")
@login_required
def index():
    rows = (
        db.session.query(Challenge.category, db.func.count(Challenge.challenge_id))
        .filter(Challenge.status == "published")
        .group_by(Challenge.category)
        .order_by(Challenge.category)
        .all()
    )
    cards = []
    for category, count in rows:
        category = category or DEFAULT_CATEGORY
        flags_total = (
            db.session.query(db.func.count(ChallengeFlag.flag_id))
            .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
            .join(Challenge, VMTemplate.challenge_id == Challenge.challenge_id)
            .filter(Challenge.category == category, ChallengeFlag.is_active.is_(True))
            .scalar()
        )
        flags_found = (
            db.session.query(db.func.count())
            .select_from(_solves_in_category(g.user["user_id"], category).subquery())
            .scalar()
        )
        cards.append({
            "theme": _theme_dict(category, count),
            "progress": {"flags_total": flags_total, "flags_found": flags_found},
        })
    return render_template("themes.html", cards=cards)


def _solves_in_category(user_id, category):
    from db.scoring_models import UserSolve
    return (
        db.session.query(UserSolve)
        .join(ChallengeFlag, UserSolve.flag_id == ChallengeFlag.flag_id)
        .join(VMTemplate, ChallengeFlag.template_id == VMTemplate.template_id)
        .join(Challenge, VMTemplate.challenge_id == Challenge.challenge_id)
        .filter(UserSolve.user_id == user_id, Challenge.category == category)
    )


@bp.route("/<theme_id>")
@login_required
def detail(theme_id):
    category = theme_id
    challenges = (
        db.session.query(Challenge)
        .filter(Challenge.category == category, Challenge.status == "published")
        .order_by(Challenge.challenge_id)
        .all()
    )
    if not challenges:
        abort(404)

    active = _active_instance(g.user["user_id"])
    live = _live_panel(active) if active and active["theme_id"] == category else None

    tiles = [
        {
            "challenge": {
                "challenge_id": challenge.challenge_id,
                "name": challenge.title,
                "brief": challenge.description,
                "difficulty": challenge.difficulty or "Entry",
                "vm_name": None,
            },
            "progress": challenge_progress(g.user["user_id"], challenge.challenge_id),
            "live": live if live and live["instance"]["challenge_id"] == challenge.challenge_id else None,
        }
        for challenge in challenges
    ]
    return render_template(
        "theme_detail.html",
        theme=_theme_dict(category, len(challenges)),
        tiles=tiles,
        matrix=category_challenge_matrix(category),
        board=category_leaderboard(category, limit=10),
        active=active,
    )


# ------------------------------------------------------------------- launch

@bp.route("/challenges/<int:challenge_id>/launch", methods=("POST",))
@login_required
def launch(challenge_id):
    challenge = db.session.get(Challenge, challenge_id)
    if challenge is None or challenge.status != "published":
        abort(404)
    category = challenge.category or DEFAULT_CATEGORY

    existing = _active_instance(g.user["user_id"])
    if existing:
        if existing["challenge_id"] == challenge_id:
            return redirect(_back(existing["theme_id"], existing["challenge_id"]))
        flash(
            "You already have a challenge running. Finish or close it before starting another.",
            "info",
        )
        return redirect(_back(existing["theme_id"], existing["challenge_id"]))

    template = (
        db.session.query(VMTemplate)
        .filter_by(challenge_id=challenge_id, is_active=True, is_user_accessible=True)
        .order_by(VMTemplate.boot_order)
        .first()
    )
    if template is None:
        flash("No VM template is mapped to this challenge yet.", "error")
        return redirect(url_for("themes.detail", theme_id=category))

    instance = ChallengeInstance(
        user_id=g.user["user_id"], challenge_id=challenge_id,
        status="running", started_at=datetime.utcnow(),
    )
    db.session.add(instance)
    db.session.commit()

    label = f"{g.user['username']}-{challenge_id}"[:63]
    try:
        clone = clone_and_start(
            template.proxmox_template_vmid, template.proxmox_node, label,
            instance_id=instance.instance_id, template_id=template.template_id,
        )
    except ProxmoxError as exc:
        instance.status = "abandoned"
        instance.error_message = str(exc)
        instance.stopped_at = datetime.utcnow()
        db.session.commit()
        flash(f"The hypervisor could not start this challenge: {exc}", "error")
        return redirect(url_for("themes.detail", theme_id=category))

    audit.record(
        audit.INSTANCE_LAUNCH,
        user_id=g.user["user_id"],
        username=g.user["username"],
        detail=f"challenge {challenge_id}, vmid {clone.vmid}",
    )
    return redirect(_back(category, challenge_id))


@bp.route("/session/<int:instance_id>/console")
@login_required
def console(instance_id):
    """Ownership-checked console page.

    The student is never handed a Proxmox credential or a link to Proxmox's
    own admin console - instead this issues a fresh one-time VNC ticket and
    renders a page that opens a WebSocket back to THIS app, which relays to
    Proxmox server-side. See console_relay() below for the other half.
    """
    instance = _owned_instance(instance_id)
    if instance["status"] != "running" or not instance["console_available"]:
        flash("That session is closed, so its machine is gone.", "info")
        return redirect(url_for("themes.detail", theme_id=instance["theme_id"]))

    try:
        ticket = get_console_ticket(instance["proxmox_vmid"], instance["node"])
    except ProxmoxError as exc:
        flash(f"Could not open a console for this session: {exc}", "error")
        return redirect(_back(instance["theme_id"], instance["challenge_id"]))

    return render_template(
        "console.html",
        instance_id=instance_id,
        vnc_password=ticket.ticket,
        vnc_port=ticket.port,
    )


@sock.route("/session/<int:instance_id>/console/ws", bp=bp)
def console_relay(ws, instance_id):
    """Bidirectional pump between the browser's noVNC socket and Proxmox's
    vncwebsocket. Reuses the SAME ticket console() already issued - the
    browser passes it back as a query param - rather than minting a second
    one, since the port/ticket pair from one vncproxy call has to stay
    paired for Proxmox to accept it.
    """
    if g.get("user") is None:
        ws.close(reason=1008, message="sign in required")
        return

    instance = _owned_instance(instance_id)
    if instance["status"] != "running" or not instance["proxmox_vmid"]:
        ws.close(reason=1008, message="no such session")
        return

    node, vmid = instance["node"], instance["proxmox_vmid"]
    ticket_value = request.args.get("ticket", "")
    port = request.args.get("port", "")

    cfg = current_app.config
    token_secret = cfg["PROXMOX_TOKEN_SECRET"]
    user, _, token_name = cfg["PROXMOX_TOKEN_ID"].partition("!")
    auth_header = f"Authorization: PVEAPIToken={user}!{token_name}={token_secret}"
    upstream_url = (
        f"wss://{cfg['PROXMOX_HOST']}:8006/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket"
        f"?port={port}&vncticket={quote(ticket_value, safe='')}"
    )
    upstream = ws_client.create_connection(
        upstream_url,
        header=[auth_header],
        sslopt={"cert_reqs": ssl.CERT_NONE if not cfg["PROXMOX_VERIFY_SSL"] else ssl.CERT_REQUIRED},
    )

    def pump_upstream_to_client():
        try:
            while True:
                data = upstream.recv()
                if data == "":
                    break
                ws.send(data)
        except Exception:
            pass
        finally:
            try:
                ws.close()
            except Exception:
                pass

    threading.Thread(target=pump_upstream_to_client, daemon=True).start()

    try:
        while True:
            data = ws.receive()
            if data is None:
                break
            upstream.send_binary(data if isinstance(data, (bytes, bytearray)) else data.encode())
    except Exception:
        pass
    finally:
        upstream.close()


# --------------------------------------------------------- live instance

@bp.route("/session/<int:instance_id>/flag", methods=("POST",))
@login_required
def flag(instance_id):
    running = _owned_instance(instance_id)
    if running["status"] != "running":
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
    if running["status"] != "running":
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
    started = running["started_at"]
    ended = running["ended_at"] or datetime.utcnow()
    return jsonify(
        {
            "status": running["status"],
            "elapsed_seconds": max(0, int((ended - started).total_seconds())),
        }
    )


def _close(instance_id: int, status: str):
    """Stop the clock, record the outcome and tear the VM down.

    status is 'complete' or 'abandoned' - the only two outcomes the spec asks
    for. Called from three places: the close() route, automatically from
    flag() when the last flag is captured, and from the staff console
    (admin.close_session).

    Ordering is deliberate: the ChallengeInstance row is updated and
    committed BEFORE the hypervisor is asked to destroy anything. If Proxmox
    is unreachable, the student still keeps their result and their time, and
    the orphaned VMInstance is flagged 'error' for staff. Do not reorder this
    to "tidy up first". stop_and_destroy() itself marks VMInstance
    deleted_at/status='destroyed' on success - this function only has to
    handle the failure case.
    """
    instance = db.session.get(ChallengeInstance, instance_id)
    vm = (
        db.session.query(VMInstance)
        .filter(VMInstance.instance_id == instance_id, VMInstance.deleted_at.is_(None))
        .order_by(VMInstance.created_at.desc())
        .first()
    )
    now = datetime.utcnow()
    duration = max(0, int((now - instance.started_at).total_seconds()))

    instance.status = status
    instance.stopped_at = now
    if status == "complete":
        instance.completed_at = now
    db.session.commit()

    audit.record(
        audit.INSTANCE_CLOSE,
        user_id=instance.user_id,
        detail=f"instance {instance_id}, {status}, {duration}s",
    )

    if vm is not None and vm.proxmox_vmid:
        try:
            stop_and_destroy(vm.proxmox_vmid, vm.proxmox_node)
        except ProxmoxError as exc:
            vm.status = "error"
            db.session.commit()
            print(f"[proxmox] teardown failed for vmid {vm.proxmox_vmid}: {exc}")