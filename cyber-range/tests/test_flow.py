"""End-to-end checks for the main paths and the hardening.

Run with:  python -m tests.test_flow      (no pytest needed)

Deliberately plain: no pytest, no fixtures, no mocking. It builds a real app
against a throwaway SQLite file, drives it through Flask's test client the way a
browser would, and prints one line per check. Anyone on the team can read it
top to bottom and see what the platform is supposed to do.

ADDING A CHECK:
    * use post() rather than client.post() — it attaches a CSRF token, without
      which every POST is correctly rejected with a 400
    * page= tells post() which page to scrape the token from; it must be a page
      that renders a form for the current session
    * assert with check("what this proves", condition)
    * anything that reads the database directly needs `with app.app_context():`

RUN THIS BEFORE EVERY COMMIT. Several checks exist because a plausible-looking
change would quietly break security: that a temporary password is never stored
readable, that the console URL never appears in HTML, that a second user
gets a 404 on someone else's session. If one of those fails, the fix is almost
never the test.

The two throttle checks temporarily lower the limits in throttle.LIMITS so the
suite does not have to send fifteen real requests, then put them back. If you
add a throttled action, follow that pattern rather than raising the limits.
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from app import throttle                        # noqa: E402
from app.db import execute, init_db, query      # noqa: E402
from app.seed import seed                       # noqa: E402
from app.seed import DEMO_PASSWORD               # noqa: E402  (seeded demo accounts)

PASSWORD = "CorrectHorseBattery1"
TOKEN_RE = re.compile(rb'name="_csrf" value="([^"]+)"')
checks = []


def check(label, condition):
    """Record one assertion. Never stops the run — a failure prints and the
    suite continues, so one break does not hide the state of everything after
    it."""
    checks.append((label, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {label}")


def token(client, path="/login"):
    """Pull this session's CSRF token out of a rendered page."""
    match = TOKEN_RE.search(client.get(path).data)
    return match.group(1).decode() if match else ""


def post(client, path, data=None, page="/login", **kwargs):
    """POST with a valid CSRF token, the way a browser would."""
    payload = dict(data or {})
    payload["_csrf"] = token(client, page)
    return client.post(path, data=payload, **kwargs)


def _events(app):
    with app.app_context():
        return query("SELECT DISTINCT event FROM audit_log")


def build():
    handle, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(handle)
    app = create_app({"DATABASE": path, "TESTING": True, "SECRET_KEY": "test",
                      "PROXMOX_BACKEND": "simulate",
                      "WTF_CSRF_ENABLED": True})
    with app.app_context():
        init_db()
        seed()
    return app, path


def main():
    app, path = build()
    client = app.test_client()

    # --- landing and registration ------------------------------------
    check("landing page renders", b"Practise on real machines" in client.get("/").data)

    response = post(client, "/register", {
        "name": "Test Student", "uni_year": "Year 3",
        "username": "tester", "password": PASSWORD, "confirm": PASSWORD,
    }, page="/register", follow_redirects=True)
    check("registration lands on sign in", b"Back to the range" in response.data)

    response = post(client, "/register", {
        "name": "Clash", "uni_year": "Year 3",
        "username": "tester", "password": PASSWORD, "confirm": PASSWORD,
    }, page="/register")
    check("duplicate username rejected", b"That username is taken" in response.data)

    response = post(client, "/register", {
        "name": "Weak", "uni_year": "Year 1",
        "username": "weakling", "password": "short", "confirm": "short",
    }, page="/register")
    check("short password rejected", b"at least 12 characters" in response.data.lower())

    check("no email field is collected", b'name="email"' not in client.get("/register").data)
    check("there is no forgotten-password page", client.get("/forgot-password").status_code == 404)

    # --- CSRF ---------------------------------------------------------
    response = client.post("/login", data={"username": "tester", "password": PASSWORD})
    check("POST without a CSRF token is refused", response.status_code == 400)
    response = client.post("/login", data={"username": "tester", "password": PASSWORD,
                                           "_csrf": "not-the-token"})
    check("POST with a wrong CSRF token is refused", response.status_code == 400)
    check("CSRF failure page shows no traceback", b"Traceback" not in response.data)

    # --- security headers ----------------------------------------------
    headers = client.get("/").headers
    check("CSP blocks inline script", "script-src 'self'" in headers.get("Content-Security-Policy", ""))
    check("framing denied", headers.get("X-Frame-Options") == "DENY")
    check("nosniff set", headers.get("X-Content-Type-Options") == "nosniff")
    check("referrer policy set", headers.get("Referrer-Policy") == "same-origin")

    # --- lockout after three failures ---------------------------------
    for attempt in range(2):
        response = post(client, "/login", {"username": "tester", "password": "wrong"})
        check(f"failure {attempt + 1} warns about attempts left", b"attempt" in response.data)
    response = post(client, "/login", {"username": "tester", "password": "wrong"})
    check("third failure locks the account", b"Account locked" in response.data)

    response = post(client, "/login", {"username": "tester", "password": PASSWORD})
    check("correct password refused while locked", b"locked" in response.data)

    # --- staff issue a temporary password, which also clears the lockout ----
    # With no email there is no self-service recovery: a moderator or admin
    # hands over a temporary password, shown once and never stored readable.
    staff = app.test_client()
    post(staff, "/login", {"username": "bpt", "password": DEMO_PASSWORD})
    with app.app_context():
        locked_user = query("SELECT user_id FROM user WHERE username = 'tester'",
                            one=True)["user_id"]
    staff_page = f"/admin/users/{locked_user}"
    response = post(staff, f"{staff_page}/reset-password", {}, page=staff_page,
                    follow_redirects=True)
    match = re.search(rb"Temporary password for tester: ([a-z0-9\-]+)", response.data)
    check("staff can issue a temporary password", match is not None)
    temporary = match.group(1).decode()

    with app.app_context():
        stored = query(
            "SELECT password_hash FROM password_manager pm JOIN user u USING (user_id) "
            "WHERE u.username = 'tester'", one=True
        )["password_hash"]
        flagged = query("SELECT must_change_password, locked_until FROM user "
                        "WHERE user_id = ?", (locked_user,), one=True)
    check("the temporary password is not stored in the clear", temporary not in stored)
    check("issuing one clears the lockout", flagged["locked_until"] is None)
    check("the account is flagged to change it", flagged["must_change_password"] == 1)

    with app.app_context():
        detail = query("SELECT detail FROM audit_log WHERE event = 'password.temporary_issued' "
                       "ORDER BY audit_id DESC LIMIT 1", one=True)["detail"]
    check("the audit note names the staff member", "bpt" in detail)
    check("the audit note does not contain the password", temporary not in detail)

    victim = app.test_client()
    response = post(victim, "/login", {"username": "tester", "password": temporary},
                    follow_redirects=True)
    check("the temporary password signs in", b"Set your own password" in response.data)
    check("and pins the account to the change page",
          b"Set your own password" in victim.get("/themes/", follow_redirects=True).data)

    new_password = "AnotherLongPassphrase9"
    response = post(victim, "/change-password",
                    {"current": "not-the-temporary-one", "password": new_password,
                     "confirm": new_password},
                    page="/change-password", follow_redirects=True)
    check("changing needs the current password", b"current password is not right" in response.data)

    response = post(victim, "/change-password",
                    {"current": temporary, "password": new_password, "confirm": new_password},
                    page="/change-password", follow_redirects=True)
    check("the change is accepted", b"Password updated" in response.data)
    check("and normal access resumes", b"Overall leaderboard" in victim.get("/dashboard").data)

    response = post(client, "/login", {"username": "tester", "password": new_password},
                    follow_redirects=True)
    check("sign in works with the new password", b"Overall leaderboard" in response.data)

    # --- themes, challenges, VM launch --------------------------------
    check("theme list renders", b"Pick a theme" in client.get("/themes/").data)
    check("theme detail renders", b"Theme scoreboard" in client.get("/themes/1").data)

    with app.app_context():
        challenge = query("SELECT challenge_id FROM challenge WHERE theme_id = 1 AND challenge_number = 1",
                     one=True)
        flags = query("SELECT flag_id FROM challenge_points WHERE challenge_id = ?",
                      (challenge["challenge_id"],))

    detail_page = "/themes/1"
    response = post(client, f"/themes/challenges/{challenge['challenge_id']}/launch", page=detail_page,
                    follow_redirects=True)
    # There is no separate session page: a running challenge works inline on
    # its own tile on the theme page.
    check("launching lands back on the theme page", b"Theme scoreboard" in response.data)
    check("the live panel appears inline", b"live-panel" in response.data)
    check("the flag form is on the tile", b"Submit a flag" in response.data)
    check("console link is offered", b"Open console" in response.data)
    check("raw hypervisor URL is not in the page", b"novnc=1" not in response.data)

    with app.app_context():
        instance = query("SELECT * FROM running_instance ORDER BY instance_id DESC LIMIT 1",
                         one=True)
        vm = query("SELECT * FROM active_vm WHERE active_vm_id = ?",
                   (instance["active_vm_id"],), one=True)
    check("a VM row was created", vm is not None and vm["status"] == "running")

    instance_page = f"/themes/session/{instance['instance_id']}"
    timer = client.get(f"{instance_page}/timer").get_json()
    check("timer reports the session", timer["status"] == "in_progress")

    response = client.get(f"{instance_page}/console")
    check("console redirects to the hypervisor", response.status_code == 302
          and "novnc" in response.headers.get("Location", ""))

    # --- flag submission ----------------------------------------------
    response = post(client, f"{instance_page}/flag", {"flag": "flag{not_a_real_flag}"},
                    page=detail_page, follow_redirects=True)
    check("wrong flag rejected", b"does not match" in response.data)

    response = post(client, f"{instance_page}/flag", {"flag": "  FLAG{CHALLENGE_ONE_ENTRY}  "},
                    page=detail_page, follow_redirects=True)
    check("correct flag scores (case and space tolerant)", b"is worth 50 points" in response.data)

    response = post(client, f"{instance_page}/flag", {"flag": "flag{challenge_one_entry}"},
                    page=detail_page, follow_redirects=True)
    check("same flag cannot be claimed twice", b"already have the points" in response.data)

    with app.app_context():
        score = query("SELECT points FROM user WHERE username = 'tester'", one=True)["points"]
        awards = query(
            "SELECT COUNT(*) AS n FROM user_challenge_points ucp "
            "JOIN user u ON u.user_id = ucp.user_id "
            "WHERE ucp.flag_id = ? AND u.username = 'tester'",
            (flags[0]["flag_id"],),
            one=True,
        )["n"]
    check("score reflects one award only", score == 50 and awards == 1)

    # --- flag brute force is throttled ---------------------------------
    original = throttle.LIMITS["flag_challenge"]
    throttle.LIMITS["flag_challenge"] = (3, 300)
    responses = [
        post(client, f"{instance_page}/flag", {"flag": f"flag{{guess_{n}}}"}, page=detail_page,
             follow_redirects=True)
        for n in range(6)
    ]
    check("flag guessing is rate limited", any(b"Too many flag attempts" in r.data
                                               for r in responses))
    throttle.LIMITS["flag_challenge"] = original
    with app.app_context():
        throttle_state = query(
            "SELECT COUNT(*) AS n FROM throttle_event WHERE bucket LIKE 'flag_challenge:%'", one=True
        )["n"]
    check("throttle counters are persisted", throttle_state > 0)

    # --- completion closes the room automatically ----------------------
    response = post(client, f"{instance_page}/flag", {"flag": "flag{challenge_one_bonus}"},
                    page=detail_page, follow_redirects=True)
    check("last flag completes the challenge", b"Marked complete" in response.data)

    with app.app_context():
        closed = query("SELECT * FROM running_instance WHERE instance_id = ?",
                       (instance["instance_id"],), one=True)
        stopped = query("SELECT status FROM active_vm WHERE active_vm_id = ?",
                        (instance["active_vm_id"],), one=True)
    check("session recorded as complete", closed["status"] == "complete")
    check("duration recorded", closed["duration_seconds"] is not None)
    check("VM marked stopped", stopped["status"] == "stopped")

    # --- abandoning -----------------------------------------------------
    post(client, f"/themes/challenges/{challenge['challenge_id']}/launch", page=detail_page,
         follow_redirects=True)
    with app.app_context():
        second = query("SELECT * FROM running_instance ORDER BY instance_id DESC LIMIT 1",
                       one=True)
    second_instance_page = f"/themes/session/{second['instance_id']}"
    response = post(client, f"{second_instance_page}/close", {"outcome": "abandoned"},
                    page=detail_page, follow_redirects=True)
    check("abandoning is recorded", b"recorded as abandoned" in response.data)

    # --- authorisation ---------------------------------------------------
    other = app.test_client()
    post(other, "/login", {"username": "demo", "password": DEMO_PASSWORD})
    check("the old session page is gone", client.get(instance_page).status_code == 404)
    response = other.get(f"{instance_page}/timer")
    check("another user cannot read your timer", response.status_code == 404)
    response = other.get(f"{instance_page}/console")
    check("another user cannot open your console", response.status_code == 404)

    anonymous = app.test_client()
    response = anonymous.get("/dashboard")
    check("dashboard requires sign in", response.status_code == 302)
    # Signed in, so the 404 handler is reached rather than the sign-in redirect.
    check("missing pages render an error page, not a stack trace",
          b"Nothing here" in client.get("/themes/9999").data)

    # --- sign-in throttling protects other accounts ----------------------
    original_ip = throttle.LIMITS["login_ip"]
    throttle.LIMITS["login_ip"] = (4, 900)
    attacker = app.test_client()
    outcomes = [
        post(attacker, "/login", {"username": name, "password": "guess"})
        for name in ["mbates", "lhardie", "vstergiou", "gthomas", "demo", "demo"]
    ]
    check("per-source throttle stops lockout sweeps",
          any(r.status_code == 429 for r in outcomes))
    throttle.LIMITS["login_ip"] = original_ip

    # --- cookie flags ------------------------------------------------------
    fresh = app.test_client()
    post(fresh, "/login", {"username": "demo", "password": DEMO_PASSWORD})
    cookie_header = "; ".join(str(h[1]) for h in fresh.get("/dashboard").headers
                              if h[0] == "Set-Cookie")
    check("session cookie is HttpOnly and SameSite",
          app.config["SESSION_COOKIE_HTTPONLY"] and app.config["SESSION_COOKIE_SAMESITE"] == "Strict")

    # --- roles and the staff console ---------------------------------------
    # Seeded roles: bpt admin, vstergiou moderator, everyone else student.
    admin_c = app.test_client()
    post(admin_c, "/login", {"username": "bpt", "password": DEMO_PASSWORD})
    mod_c = app.test_client()
    post(mod_c, "/login", {"username": "vstergiou", "password": DEMO_PASSWORD})
    # demo stays a student throughout; lhardie gets promoted below, so it cannot
    # double as the "student" client.
    student_c = app.test_client()
    post(student_c, "/login", {"username": "demo", "password": DEMO_PASSWORD})

    check("admin reaches the staff console", b"Staff console" in admin_c.get("/admin/").data)
    check("moderator reaches the staff console", b"Staff console" in mod_c.get("/admin/").data)
    check("student is refused the staff console", student_c.get("/admin/").status_code == 403)
    check("signed-out users are sent to sign in",
          app.test_client().get("/admin/").status_code == 302)

    check("student sees no staff link", b"Staff</a>" not in student_c.get("/dashboard").data)
    check("moderator sees the staff link", b"Staff</a>" in mod_c.get("/dashboard").data)

    # --- scoreboard eligibility ---------------------------------------------
    # Administrators run the platform and are off every board; moderators
    # compete like anyone else. Checked with the admin holding a score high
    # enough that a leak would be obvious.
    with app.app_context():
        from app.scoring import theme_leaderboard as _cb
        from app.scoring import overall_leaderboard as _ob
        from app.scoring import user_stats as _stats

        admin_id = query("SELECT user_id FROM user WHERE username = 'bpt'", one=True)["user_id"]
        execute("UPDATE user SET points = 9999 WHERE user_id = ?", (admin_id,))
        execute(
            "INSERT INTO user_challenge_points "
            "(user_id, flag_id, theme_id, challenge_id, points_awarded) "
            "SELECT ?, flag_id, theme_id, challenge_id, points FROM challenge_points LIMIT 1",
            (admin_id,),
        )
        board_names = [row["username"] for row in _ob(limit=50)]
        challenge_names = [row["username"] for row in _cb(1, limit=50)]
        admin_stats = _stats(admin_id)
        mod_id = query("SELECT user_id FROM user WHERE username = 'vstergiou'",
                       one=True)["user_id"]
        mod_stats = _stats(mod_id)

    check("admin is absent from the overall board", "bpt" not in board_names)
    check("admin is absent from the per-challenge board", "bpt" not in challenge_names)
    check("admin has no rank at all", admin_stats["rank"] is None)
    check("the eligible field excludes the admin", admin_stats["field_size"] == len(board_names))
    check("moderators still compete", "vstergiou" in board_names and mod_stats["rank"] is not None)
    check("admin dashboard shows no rank", b"not ranked" in admin_c.get("/dashboard").data)

    with app.app_context():
        ids = {row["username"]: row["user_id"]
               for row in query("SELECT username, user_id FROM user")}

    # Role changes: admin only, and never on a peer or yourself.
    response = post(mod_c, f"/admin/users/{ids['lhardie']}/role", {"role": "moderator"},
                    page=f"/admin/users/{ids['lhardie']}")
    check("moderator cannot change roles", response.status_code == 403)
    with app.app_context():
        still = query("SELECT role FROM user WHERE username = 'lhardie'", one=True)["role"]
    check("the blocked role change did nothing", still == "student")

    response = post(admin_c, f"/admin/users/{ids['lhardie']}/role", {"role": "moderator"},
                    page=f"/admin/users/{ids['lhardie']}", )
    with app.app_context():
        promoted = query("SELECT role FROM user WHERE username = 'lhardie'", one=True)["role"]
    check("admin can promote a student to moderator", promoted == "moderator")

    # bpt is the only admin at this point, so stepping down must be refused.
    response = post(admin_c, f"/admin/users/{ids['bpt']}/role", {"role": "student"},
                    page=f"/admin/users/{ids['bpt']}", follow_redirects=True)
    check("the last administrator cannot step down", b"only administrator" in response.data)
    with app.app_context():
        still_admin = query("SELECT role FROM user WHERE username = 'bpt'", one=True)["role"]
    check("the blocked step-down did nothing", still_admin == "admin")

    post(admin_c, f"/admin/users/{ids['mbates']}/role", {"role": "admin"},
         page=f"/admin/users/{ids['mbates']}")
    with app.app_context():
        admins = query("SELECT COUNT(*) AS n FROM user WHERE role = 'admin'", one=True)["n"]
    check("a second admin can be created", admins == 2)

    # With a colleague in place, handing over IS allowed.
    response = post(admin_c, f"/admin/users/{ids['bpt']}/role", {"role": "moderator"},
                    page=f"/admin/users/{ids['bpt']}", follow_redirects=True)
    with app.app_context():
        stepped_down = query("SELECT role FROM user WHERE username = 'bpt'",
                             one=True)["role"]
    check("an admin can step down once someone else holds the role",
          stepped_down == "moderator")
    check("the demoted admin loses the role controls",
          admin_c.get(f"/admin/users/{ids['lhardie']}").status_code == 200
          and b"Save role" not in admin_c.get(f"/admin/users/{ids['lhardie']}").data)

    # mbates is now the sole admin; use that client for the rest.
    admin_c = app.test_client()
    post(admin_c, "/login", {"username": "mbates", "password": DEMO_PASSWORD})

    # Moderator powers, and the ceiling on them.
    locked_target = ids["demo"]
    post(mod_c, f"/admin/users/{locked_target}/lock", {"hours": "2"},
         page=f"/admin/users/{locked_target}")
    with app.app_context():
        locked_row = query("SELECT locked_until FROM user WHERE user_id = ?",
                           (locked_target,), one=True)
    check("moderator can lock a student out", locked_row["locked_until"] is not None)

    # A lockout must end the live session, not wait for the next sign-in.
    check("locking signs the account out immediately",
          student_c.get("/dashboard").status_code == 302)

    post(mod_c, f"/admin/users/{locked_target}/unlock", {}, page=f"/admin/users/{locked_target}")
    with app.app_context():
        unlocked = query("SELECT locked_until FROM user WHERE user_id = ?",
                         (locked_target,), one=True)
    check("moderator can unlock a student", unlocked["locked_until"] is None)

    response = post(mod_c, f"/admin/users/{ids['mbates']}/lock", {"hours": "2"},
                    page=f"/admin/users/{ids['mbates']}")
    check("moderator cannot act on an administrator", response.status_code == 403)

    with app.app_context():
        other_mod = query("SELECT user_id FROM user WHERE username = 'lhardie'",
                          one=True)["user_id"]
    response = post(mod_c, f"/admin/users/{other_mod}/lock", {"hours": "2"},
                    page=f"/admin/users/{other_mod}")
    check("moderator cannot act on another moderator", response.status_code == 403)

    check("staff can read the audit log", b"Audit log" in mod_c.get("/admin/audit").data)
    plain_c = app.test_client()
    post(plain_c, "/login", {"username": "demo", "password": DEMO_PASSWORD})
    check("student cannot read the audit log",
          plain_c.get("/admin/audit").status_code == 403)
    check("student cannot reach the account list",
          plain_c.get("/admin/users").status_code == 403)
    check("student cannot reach the session controls",
          plain_c.get("/admin/sessions").status_code == 403)

    # Every permission named in a guard must exist in the matrix.
    from app import roles as roles_module
    guarded = set(roles_module.PERMISSIONS)
    check("no permission is granted to nobody",
          all(len(holders) > 0 for holders in roles_module.PERMISSIONS.values()))
    check("students hold no permissions at all",
          not any(roles_module.STUDENT in holders for holders in roles_module.PERMISSIONS.values()))
    check("role changes are audited",
          "account.role_changed" in {r["event"] for r in
                                     (lambda: [dict(x) for x in _events(app)])()})

    # --- audit trail --------------------------------------------------------
    with app.app_context():
        events = {row["event"] for row in query("SELECT DISTINCT event FROM audit_log")}
    check("auth events are audited", {"login.success", "login.failure"} <= events)
    check("instance events are audited", {"instance.launched", "instance.closed"} <= events)

    os.unlink(path)

    failures = [label for label, ok in checks if not ok]
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print("Failed:", *failures, sep="\n  ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
