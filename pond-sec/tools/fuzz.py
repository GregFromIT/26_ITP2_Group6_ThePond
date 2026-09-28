"""Fuzz the platform and report what breaks.

    python tools/fuzz.py              # full run
    python tools/fuzz.py --quick      # fewer payloads, for a pre-commit check
    python tools/fuzz.py --seed 7     # reproduce a specific run

Drives a real app against a throwaway database through Flask's test client,
firing malformed and hostile input at every route, then reports anything that
looks like a defect.

WHAT COUNTS AS A FINDING
------------------------
Not "the app said no". Refusing bad input is the app working. A finding is:

  * a 500, or any traceback reaching the response
  * an unhandled exception escaping a view
  * a payload appearing unescaped in the HTML (stored or reflected XSS)
  * a signed-out or under-privileged client reaching something it should not
  * a file written outside the upload directory
  * database state that should be impossible (duplicate flag awards, negative
    scores, roles outside the allowed set)
  * an input that is accepted when the rules say it should not be

Everything else — 400, 403, 404, 429, a flash message saying no — is a pass.

WHY THIS IS NOT THE SAME AS RUNNING ZAP OR BURP
-----------------------------------------------
This runs inside the process, against the test client. It never touches the
network stack, TLS, the reverse proxy or the browser, so it cannot find anything
in those layers, and it only fires payloads someone thought to write down. It is
a cheap regression net, not an assessment. The to-do list still says the
platform needs testing by somebody outside the group.
"""

import argparse
import io
import os
import random
import re
import sys
import tempfile
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from app.db import init_db, query               # noqa: E402
from app.seed import DEMO_PASSWORD, seed        # noqa: E402

TOKEN_RE = re.compile(rb'name="_csrf" value="([^"]+)"')

# A marker that would be visible in rendered HTML if escaping ever failed.
XSS_MARKER = "pondsecxss"

PAYLOADS = [
    # --- injection ---------------------------------------------------------
    "' OR '1'='1",
    "'; DROP TABLE user; --",
    "1; DELETE FROM user_challenge_points WHERE 1=1; --",
    "admin'--",
    "\" OR \"\"=\"",
    "1 UNION SELECT password_hash FROM password_manager",
    "%27%20OR%201=1--",
    # --- cross-site scripting ---------------------------------------------
    f"<script>{XSS_MARKER}()</script>",
    f"<img src=x onerror={XSS_MARKER}>",
    f"\"><svg/onload={XSS_MARKER}>",
    f"javascript:{XSS_MARKER}",
    f"<iframe src=javascript:{XSS_MARKER}>",
    # --- template injection (Jinja is right there) -------------------------
    "{{ 7*7 }}",
    "{{ config }}",
    "{{ config.SECRET_KEY }}",
    "{% for x in ().__class__.__base__.__subclasses__() %}{{ x }}{% endfor %}",
    "${7*7}",
    # --- path traversal ----------------------------------------------------
    "../../../etc/passwd",
    "..\\..\\..\\windows\\system32\\config\\sam",
    "/etc/shadow",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    # --- command injection -------------------------------------------------
    "; cat /etc/passwd",
    "| whoami",
    "$(id)",
    "`id`",
    "&& rm -rf /",
    # --- encoding and control characters -----------------------------------
    "\x00",
    "a\x00b",
    "\r\nSet-Cookie: admin=1",
    "\n\nHTTP/1.1 200 OK",
    "\u202e\u0000",
    "\ufeff",
    "％２７",
    "𝕏" * 50,
    "🦆" * 100,
    "\u0301" * 500,
    # --- numeric and type confusion ----------------------------------------
    "-1", "0", "999999999999999999999", "1e309", "NaN", "Infinity",
    "0x41414141", "1.0", "-0", "[]", "{}", "null", "true",
    # --- size --------------------------------------------------------------
    "A" * 5000,
    " " * 2000,
    "",
    # --- format strings ----------------------------------------------------
    "%s%s%s%s", "%n", "{0}", "{}",
]

QUICK_PAYLOADS = PAYLOADS[::4]


class Fuzzer:
    def __init__(self, app, seed_value):
        self.app = app
        self.random = random.Random(seed_value)
        self.findings = []
        self.checked = 0
        self.status_counts = Counter()

    # -------------------------------------------------------------- helpers

    def note(self, category, detail):
        self.findings.append((category, detail))

    def token(self, client, path="/login"):
        match = TOKEN_RE.search(client.get(path).data)
        return match.group(1).decode() if match else ""

    def send(self, client, method, path, data=None, token_page=None, **kwargs):
        """Fire one request and judge the response. Never raises."""
        self.checked += 1
        payload = dict(data or {})
        if method == "POST" and "_csrf" not in payload and "data" not in kwargs:
            payload["_csrf"] = self.token(client, token_page or path)
        try:
            if method == "POST":
                response = client.post(path, data=payload, **kwargs)
            else:
                response = client.get(path, **kwargs)
        except Exception:
            self.note("unhandled exception",
                      f"{method} {path} raised:\n{traceback.format_exc(limit=4)}")
            return None

        self.status_counts[response.status_code] += 1

        if response.status_code >= 500:
            self.note("server error", f"{method} {path} -> {response.status_code}")
        body = response.data
        for leak in (b"Traceback (most recent call last)", b"werkzeug.exceptions",
                     b"sqlite3.", b"sqlalchemy.exc", b"File \"/"):
            if leak in body:
                self.note("internals leaked",
                          f"{method} {path} response contains {leak.decode(errors='replace')}")
                break
        return response

    def check_escaping(self, response, sent):
        """Did a payload come back in a form a browser would execute?"""
        if response is None:
            return
        body = response.data.decode("utf-8", errors="replace")
        # Every pattern starts with a raw "<" on purpose. Escaping only rewrites
        # < > " &, so a payload that came back correctly escaped still contains
        # the literal text `onerror=pondsecxss`. Matching on that alone reports
        # working escaping as a vulnerability, which is worse than useless.
        dangerous = [f"<script>{XSS_MARKER}",
                     f"<img src=x onerror={XSS_MARKER}",
                     f"<svg/onload={XSS_MARKER}",
                     f"<iframe src=javascript:{XSS_MARKER}"]
        for pattern in dangerous:
            if pattern in body:
                self.note("XSS", f"unescaped {pattern!r} after sending {sent[:60]!r}")
        # Jinja evaluating user input would turn {{ 7*7 }} into 49.
        if sent.strip() == "{{ 7*7 }}" and re.search(r"\b49\b", body):
            if "{{ 7*7 }}" not in body:
                self.note("template injection", "{{ 7*7 }} appears to have been evaluated")
        if "SECRET_KEY" in sent and self.app.config["SECRET_KEY"] in body:
            self.note("secret leaked", "the secret key appeared in a response")

    # ----------------------------------------------------------- fuzz cases

    def fuzz_public_forms(self, payloads):
        """Registration and sign-in, signed out."""
        for value in payloads:
            client = self.app.test_client()
            response = self.send(client, "POST", "/register", {
                "name": value, "uni_year": value, "username": value,
                "password": value, "confirm": value,
            }, token_page="/register", follow_redirects=True)
            self.check_escaping(response, value)

            client = self.app.test_client()
            response = self.send(client, "POST", "/login",
                                 {"username": value, "password": value},
                                 follow_redirects=True)
            self.check_escaping(response, value)

    def fuzz_authenticated_forms(self, payloads, client):
        """Flag submission and password change, signed in as a student."""
        with self.app.app_context():
            challenge = query(
                "SELECT challenge_id FROM challenge WHERE theme_id = 1 "
                "AND challenge_number = 1", one=True)["challenge_id"]
        self.send(client, "POST", f"/themes/challenges/{challenge}/launch", {},
                  token_page="/themes/1", follow_redirects=True)
        with self.app.app_context():
            row = query("SELECT instance_id FROM running_instance "
                        "ORDER BY instance_id DESC LIMIT 1", one=True)
        instance = row["instance_id"] if row else 1

        for value in payloads:
            response = self.send(client, "POST", f"/themes/session/{instance}/flag",
                                 {"flag": value}, token_page="/themes/1",
                                 follow_redirects=True)
            self.check_escaping(response, value)

            response = self.send(client, "POST", "/change-password",
                                 {"current": value, "password": value, "confirm": value},
                                 token_page="/change-password", follow_redirects=True)
            self.check_escaping(response, value)

    def fuzz_url_parameters(self, client):
        """Integer route parameters, and ids belonging to other people."""
        nasty = ["-1", "0", "99999999", "999999999999999999999", "abc", "1.5",
                 "1%20OR%201=1", "../1", "%2e%2e", "null", "0x01", "＋１", "1;1"]
        templates = [
            "/themes/{}",
            "/themes/challenges/{}/launch",
            "/themes/session/{}",
            "/themes/session/{}/flag",
            "/themes/session/{}/close",
            "/themes/session/{}/console",
            "/themes/session/{}/timer",
            "/admin/users/{}",
            "/admin/users/{}/unlock",
            "/admin/users/{}/role",
            "/admin/users/{}/approve",
            "/admin/users/{}/reject",
            "/admin/sessions/{}/close",
            "/admin/uploads/{}/delete",
            "/admin/uploads/{}/status",
        ]
        for template in templates:
            for value in nasty:
                path = template.format(value)
                self.send(client, "GET", path, follow_redirects=False)
                self.send(client, "POST", path, {}, token_page="/dashboard",
                          follow_redirects=False)

    def fuzz_access_control(self):
        """Can a student, moderator or stranger reach staff-only routes?"""
        staff_only = [
            ("/admin/", "moderator"), ("/admin/users", "moderator"),
            ("/admin/sessions", "moderator"), ("/admin/audit", "moderator"),
            ("/admin/approvals", "admin"), ("/admin/uploads/", "admin"),
        ]
        clients = {
            "anonymous": self.app.test_client(),
            "student": self._signed_in("demo"),
            "moderator": self._signed_in("vstergiou"),
        }
        for path, needed in staff_only:
            for who, client in clients.items():
                if who == "moderator" and needed == "moderator":
                    continue
                response = self.send(client, "GET", path, follow_redirects=False)
                if response is None:
                    continue
                if response.status_code == 200:
                    self.note("access control",
                              f"{who} reached {path}, which needs {needed}")

        # Write endpoints, not just the pages.
        writes = [("/admin/uploads/url", {"source_url": "https://e.org/x.qcow2"}),
                  ("/admin/users/1/approve", {}),
                  ("/admin/users/1/role", {"role": "admin"})]
        for path, data in writes:
            for who in ("student", "moderator"):
                client = clients[who]
                response = self.send(client, "POST", path, data,
                                     token_page="/dashboard", follow_redirects=False)
                if response is not None and response.status_code in (200, 302):
                    self.note("access control",
                              f"{who} was not refused on POST {path} "
                              f"(got {response.status_code})")

    def fuzz_session_isolation(self):
        """One student must not touch another student's instance."""
        owner = self._signed_in("mbates")
        with self.app.app_context():
            challenge = query("SELECT challenge_id FROM challenge WHERE theme_id = 2 "
                              "AND challenge_number = 1", one=True)["challenge_id"]
        self.send(owner, "POST", f"/themes/challenges/{challenge}/launch", {},
                  token_page="/themes/2", follow_redirects=True)
        with self.app.app_context():
            row = query("SELECT instance_id FROM running_instance WHERE status='in_progress' "
                        "ORDER BY instance_id DESC LIMIT 1", one=True)
        if row is None:
            return
        instance = row["instance_id"]

        intruder = self._signed_in("lhardie")
        for method, path in [
            ("GET", f"/themes/session/{instance}/timer"),
            ("GET", f"/themes/session/{instance}/console"),
            ("POST", f"/themes/session/{instance}/flag"),
            ("POST", f"/themes/session/{instance}/close"),
        ]:
            data = {"flag": "flag{challenge_one_entry}"} if method == "POST" else None
            response = self.send(intruder, method, path, data,
                                 token_page="/dashboard", follow_redirects=False)
            if response is not None and response.status_code not in (400, 403, 404):
                self.note("session isolation",
                          f"another user got {response.status_code} on {method} {path}")

    def fuzz_csrf(self):
        """Tokens must not be missing, wrong, or borrowed from another session."""
        client = self._signed_in("demo")
        other = self._signed_in("lhardie")
        borrowed = self.token(other, "/dashboard")

        cases = [("no token", {}),
                 ("empty token", {"_csrf": ""}),
                 ("wrong token", {"_csrf": "x" * 43}),
                 ("another session's token", {"_csrf": borrowed})]
        for label, extra in cases:
            self.checked += 1
            data = {"username": "demo", "password": DEMO_PASSWORD}
            data.update(extra)
            response = client.post("/login", data=data)
            self.status_counts[response.status_code] += 1
            if response.status_code != 400:
                self.note("CSRF", f"{label} gave {response.status_code}, expected 400")

    def fuzz_uploads(self, admin, payloads):
        """Filenames, contents and URLs."""
        with self.app.app_context():
            from app.uploads import upload_dir
            directory = upload_dir()
        before = set(os.listdir(directory))
        # Only a loose file appearing in the parent means something escaped; a
        # new subdirectory would not.
        parent = os.path.dirname(directory.rstrip("/"))
        parent_files_before = {name for name in os.listdir(parent)
                               if os.path.isfile(os.path.join(parent, name))}

        for value in payloads:
            name = f"{value}.qcow2"
            self.send(admin, "POST", "/admin/uploads/",
                      {"display_name": value, "notes": value,
                       "vm_file": (io.BytesIO(b"\x00" * 128), name)},
                      token_page="/admin/uploads/",
                      content_type="multipart/form-data", follow_redirects=True)

            response = self.send(admin, "POST", "/admin/uploads/url",
                                 {"source_url": value, "display_name": value},
                                 token_page="/admin/uploads/", follow_redirects=True)
            self.check_escaping(response, value)

        # Names crafted specifically to escape the directory.
        for name in ["../escaped.qcow2", "../../escaped.qcow2",
                     "..\\escaped.qcow2", "/tmp/escaped.qcow2",
                     "a/../../escaped.qcow2", "\x00escaped.qcow2"]:
            self.send(admin, "POST", "/admin/uploads/",
                      {"vm_file": (io.BytesIO(b"\x00" * 64), name)},
                      token_page="/admin/uploads/",
                      content_type="multipart/form-data", follow_redirects=True)

        after = set(os.listdir(directory))
        for written in after - before:
            if not written.endswith(".upload"):
                self.note("upload naming",
                          f"a file was written with an unexpected name: {written}")
        parent_files_after = {name for name in os.listdir(parent)
                              if os.path.isfile(os.path.join(parent, name))}
        escaped = parent_files_after - parent_files_before
        if escaped:
            self.note("path traversal",
                      f"a file appeared outside the upload directory: {sorted(escaped)}")
        if os.path.exists("/tmp/escaped.qcow2"):
            self.note("path traversal", "an upload was written to /tmp")

        # Uploads must never be reachable over HTTP.
        for written in list(after)[:5]:
            for path in (f"/static/{written}", f"/static/../instance/uploads/{written}"):
                response = self.send(admin, "GET", path, follow_redirects=False)
                if response is not None and response.status_code == 200:
                    self.note("upload exposure", f"{path} returned 200")

    def fuzz_headers_and_methods(self, client):
        """Wrong methods, odd headers, absurd content types."""
        for path in ["/", "/login", "/dashboard", "/themes/", "/admin/", "/admin/uploads/"]:
            for method in ("PUT", "DELETE", "PATCH", "TRACE"):
                self.checked += 1
                try:
                    response = client.open(path, method=method)
                    self.status_counts[response.status_code] += 1
                    if response.status_code >= 500:
                        self.note("server error", f"{method} {path} -> {response.status_code}")
                except Exception:
                    self.note("unhandled exception",
                              f"{method} {path} raised:\n{traceback.format_exc(limit=3)}")

        for header, value in [("X-Forwarded-For", "'; DROP TABLE user;--"),
                              ("X-Forwarded-For", "A" * 5000),
                              ("User-Agent", "\x00\x01\x02"),
                              ("Content-Type", "application/json"),
                              ("Content-Type", "x" * 500),
                              ("Accept-Encoding", "\r\ninjected: 1")]:
            self.checked += 1
            try:
                response = client.get("/login", headers={header: value})
                self.status_counts[response.status_code] += 1
                if response.status_code >= 500:
                    self.note("server error", f"header {header} -> {response.status_code}")
            except Exception:
                # Werkzeug refusing a malformed header at the client is fine.
                pass

    def check_invariants(self):
        """Things that must be true no matter what was thrown at the app."""
        with self.app.app_context():
            duplicate = query(
                "SELECT user_id, flag_id, COUNT(*) AS n FROM user_challenge_points "
                "GROUP BY user_id, flag_id HAVING n > 1")
            if duplicate:
                self.note("data integrity", f"{len(duplicate)} flags awarded more than once")

            negative = query("SELECT username FROM user WHERE points < 0")
            if negative:
                self.note("data integrity",
                          f"negative score on {[r['username'] for r in negative]}")

            bad_role = query(
                "SELECT username, role FROM user "
                "WHERE role NOT IN ('student', 'moderator', 'admin')")
            if bad_role:
                self.note("data integrity", f"unexpected role: {bad_role[0]['role']}")

            bad_status = query(
                "SELECT username FROM user "
                "WHERE approval_status NOT IN ('pending', 'approved', 'rejected')")
            if bad_status:
                self.note("data integrity", "unexpected approval_status")

            drifted = query(
                "SELECT u.username FROM user u WHERE u.points != "
                "(SELECT COALESCE(SUM(points_awarded), 0) FROM user_challenge_points x "
                " WHERE x.user_id = u.user_id)")
            if drifted:
                self.note("data integrity",
                          f"points total disagrees with the award ledger for "
                          f"{[r['username'] for r in drifted]}")

            escalated = query(
                "SELECT username FROM user WHERE role != 'student' "
                "AND username NOT IN ('bpt', 'vstergiou')")
            if escalated:
                self.note("privilege escalation",
                          f"unexpected staff account: {[r['username'] for r in escalated]}")

            # Nothing should have wiped a table.
            for table, minimum in [("user", 6), ("theme", 3), ("challenge", 18),
                                   ("challenge_points", 36)]:
                count = query(f"SELECT COUNT(*) AS n FROM {table}", one=True)["n"]
                if count < minimum:
                    self.note("data loss",
                              f"{table} has {count} rows, expected at least {minimum}")

    # ----------------------------------------------------------------- setup

    def _signed_in(self, username):
        client = self.app.test_client()
        client.post("/login", data={"username": username, "password": DEMO_PASSWORD,
                                    "_csrf": self.token(client, "/login")},
                    follow_redirects=True)
        return client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="fewer payloads")
    parser.add_argument("--seed", type=int, default=1, help="random seed")
    args = parser.parse_args()

    payloads = QUICK_PAYLOADS if args.quick else PAYLOADS

    scratch = tempfile.mkdtemp(prefix="pondsec-fuzz-")
    app = create_app({
        "DATABASE": os.path.join(scratch, "fuzz.sqlite"),
        "TESTING": True,
        "SECRET_KEY": "fuzzing-only-key",
        "PROXMOX_BACKEND": "simulate",
        "SQLALCHEMY_BINDS": {"pond": f"sqlite:///{scratch}/pond.db"},
        "UPLOAD_DIR": os.path.join(scratch, "uploads"),
        # Rate limits would stop the fuzzer long before it finished, and this
        # run is about correctness rather than throttling, which the test suite
        # covers separately.
        "MAX_LOGIN_ATTEMPTS": 10_000,
    })
    with app.app_context():
        init_db()
        seed()
        from app.uploads import db as sa_db
        sa_db.create_all(bind_key="pond")

    from app import throttle
    for action in list(throttle.LIMITS):
        throttle.LIMITS[action] = (10_000_000, 1)

    fuzzer = Fuzzer(app, args.seed)
    admin = fuzzer._signed_in("bpt")
    student = fuzzer._signed_in("demo")

    stages = [
        ("public forms", lambda: fuzzer.fuzz_public_forms(payloads)),
        ("authenticated forms", lambda: fuzzer.fuzz_authenticated_forms(payloads, student)),
        ("url parameters", lambda: fuzzer.fuzz_url_parameters(admin)),
        ("access control", fuzzer.fuzz_access_control),
        ("session isolation", fuzzer.fuzz_session_isolation),
        ("csrf", fuzzer.fuzz_csrf),
        ("uploads", lambda: fuzzer.fuzz_uploads(admin, payloads)),
        ("methods and headers", lambda: fuzzer.fuzz_headers_and_methods(admin)),
    ]

    print(f"Fuzzing Pond Sec (seed {args.seed}, "
          f"{len(payloads)} payloads{', quick' if args.quick else ''})\n")
    for label, run in stages:
        before = len(fuzzer.findings)
        try:
            run()
        except Exception:
            fuzzer.note("harness error", f"stage {label!r} crashed:\n{traceback.format_exc()}")
        found = len(fuzzer.findings) - before
        print(f"  {label:22} {fuzzer.checked:>6} requests so far"
              f"{'   <-- ' + str(found) + ' finding(s)' if found else ''}")

    fuzzer.check_invariants()

    print(f"\n{fuzzer.checked} requests sent")
    print("status codes:", dict(sorted(fuzzer.status_counts.items())))

    if not fuzzer.findings:
        print("\nNo findings.")
        print("That means nothing crashed, nothing leaked and nothing got past a guard —")
        print("not that the platform is secure. See the note at the top of this file.")
        return 0

    print(f"\n{len(fuzzer.findings)} FINDING(S):\n")
    grouped = {}
    for category, detail in fuzzer.findings:
        grouped.setdefault(category, []).append(detail)
    for category, details in grouped.items():
        print(f"  [{category}] x{len(details)}")
        for detail in details[:5]:
            print(f"      {detail}")
        if len(details) > 5:
            print(f"      ... and {len(details) - 5} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
