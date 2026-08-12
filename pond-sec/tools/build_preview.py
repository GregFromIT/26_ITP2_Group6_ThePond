"""Build a single-file, no-install preview of the interface.

    python tools/build_preview.py

Writes preview/pond-sec-preview.html — one HTML file with every screen in it,
a switcher across the top, the stylesheet inlined and the tile graphics embedded
as data URIs. Open it by double-clicking; no Python, no server, no network.

It renders the REAL templates through Flask's test client rather than
duplicating the markup, so re-running it after a template or CSS change gives an
accurate preview. Nothing in it works when clicked — links are neutralised and
forms are inert. It is for showing people what the platform looks like (in a
report, a demo, or on a machine with no Python), not for testing behaviour.

Runs on Linux, macOS and Windows.
"""

import base64
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                      # noqa: E402
from app.db import init_db, query               # noqa: E402
from app.seed import DEMO_PASSWORD, seed        # noqa: E402

# Who the signed-in screens are rendered as, and whose seeded scores that
# account should carry. Set SCORES_FROM to None to use the account's own.
PREVIEW_USER = "gthomas"
SCORES_FROM = "mbates"

# The staff screens are rendered as this account instead — seed.py makes bpt the
# system administrator, so the role controls are visible on the account page.
STAFF_USER = "bpt"

TOKEN_RE = re.compile(rb'name="_csrf" value="([^"]+)"')
BODY_RE = re.compile(r"<body>(.*)</body>", re.S)

SCREENS = [
    ("landing", "Landing", "The public page: what the range is and how a session runs."),
    ("register", "Register", "Name, year of study, username and password."),
    ("login", "Sign in", "Three attempts before lockout. Recovery is staff-issued."),
    ("change_password", "Change password", "Self-service, and where a staff-issued temporary password lands."),
    ("dashboard", "Dashboard", "Your standing, the overall board, and one board per theme."),
    ("themes", "Themes", "Pick a theme."),
    ("theme", "Theme detail", "Challenge tiles, one running inline, and the theme scoreboard."),
    ("admin", "Staff console", "Moderator and administrator view: accounts, sessions, events."),
    ("admin_users", "Accounts", "Every account with its role and lockout state."),
    ("admin_user", "Account detail", "Unlock, issue a temporary password, and (admins only) set the role."),
    ("admin_sessions", "Sessions", "Close a stuck session and release its machine."),
    ("admin_audit", "Audit log", "Read-only record of auth, role and session events."),
]


def token(client, path):
    match = TOKEN_RE.search(client.get(path).data)
    return match.group(1).decode() if match else ""


def post(client, path, data, page):
    payload = dict(data)
    payload["_csrf"] = token(client, page)
    return client.post(path, data=payload, follow_redirects=True)


def swap_identities(app, first: str, second: str):
    """Make the account named `first` carry the seeded scores of `second`.

    Rather than moving hundreds of score rows between accounts — which trips the
    foreign keys and the UNIQUE(user_id, flag_id) lock on the award ledger — this
    swaps the identity fields on the two user rows. The scores never move; the
    names do. The leaderboards keep exactly the same totals with the two names
    traded, and the demo password is shared, so signing in as `first` still
    works.

    Preview build only. Nothing in the app does this.
    """
    from app.db import get_db, query

    with app.app_context():
        rows = {
            name: query(
                "SELECT user_id, name, uni_year, username, role FROM user WHERE username = ?",
                (name,),
                one=True,
            )
            for name in (first, second)
        }
        if not all(rows.values()):
            raise SystemExit(f"seed data has no {first} or {second} to swap")

        a, b = rows[first], rows[second]
        db = get_db()
        # Usernames are UNIQUE, so one row is parked on a scratch value while
        # the other moves.
        db.execute(
            "UPDATE user SET username = ? WHERE user_id = ?",
            ("__swap__", a["user_id"]),
        )
        # Role travels with the identity, not with the score: gthomas stays an
        # administrator whichever row now carries the name.
        db.execute(
            "UPDATE user SET name = ?, uni_year = ?, username = ?, role = ? WHERE user_id = ?",
            (a["name"], a["uni_year"], a["username"], a["role"], b["user_id"]),
        )
        db.execute(
            "UPDATE user SET name = ?, uni_year = ?, username = ?, role = ? WHERE user_id = ?",
            (b["name"], b["uni_year"], b["username"], b["role"], a["user_id"]),
        )
        db.commit()


def capture(app):
    """Render every screen and return {name: inner HTML of <body>}."""
    pages = {}
    anon = app.test_client()

    def grab(name, client, path):
        html = client.get(path).data.decode("utf-8")
        match = BODY_RE.search(html)
        pages[name] = match.group(1) if match else html

    grab("landing", anon, "/")
    grab("register", anon, "/register")
    grab("login", anon, "/login")

    user = app.test_client()
    post(user, "/login", {"username": PREVIEW_USER, "password": DEMO_PASSWORD}, "/login")
    grab("change_password", user, "/change-password")
    grab("dashboard", user, "/dashboard")
    grab("themes", user, "/themes/")

    # Launch BEFORE capturing the theme page, so the preview shows a challenge
    # running inline — that panel is where a session is worked now, and it also
    # gives the staff Sessions page below something real to show.
    with app.app_context():
        challenge = query(
            "SELECT challenge_id FROM challenge WHERE theme_id = 1 AND challenge_number = 2",
            one=True,
        )
    post(user, f"/themes/challenges/{challenge['challenge_id']}/launch", {}, "/themes/1")
    grab("theme", user, "/themes/1")

    # Staff screens need an account that holds the permissions, so they are
    # rendered as STAFF_USER rather than the student above.
    staff = app.test_client()
    post(staff, "/login", {"username": STAFF_USER, "password": DEMO_PASSWORD}, "/login")
    grab("admin", staff, "/admin/")
    grab("admin_users", staff, "/admin/users")
    with app.app_context():
        subject = query("SELECT user_id FROM user WHERE username = 'lhardie'", one=True)
    grab("admin_user", staff, f"/admin/users/{subject['user_id']}")
    grab("admin_sessions", staff, "/admin/sessions")
    grab("admin_audit", staff, "/admin/audit")
    return pages


MIME = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}


def data_uri(path: pathlib.Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{MIME[path.suffix.lower()]};base64,{encoded}"


def inline_assets(html: str) -> str:
    """Embed the tile images and neutralise everything that would need a server.

    The masthead logo is deliberately NOT inlined here: it appears on all twelve
    screens, and a copy of the data URI per screen made the file three times
    larger. It is swapped for a <span> and painted once from the stylesheet by
    logo_rule() below.
    """
    # Both logo images become spans painted from the stylesheet — see the note
    # on logo_rule() below.
    for css_class in ("wordmark-logo", "brand-logo"):
        html = re.sub(
            rf'<img class="{css_class}"[^>]*>',
            f'<span class="{css_class}" aria-hidden="true"></span>',
            html,
        )
    for image in sorted((ROOT / "app" / "static" / "img").iterdir()):
        if image.suffix.lower() in MIME and image.name != "logo.png":
            html = html.replace(f"/static/img/{image.name}", data_uri(image))

    html = re.sub(r'<link rel="stylesheet"[^>]*>', "", html)
    html = re.sub(r"<script[^>]*></script>", "", html)
    html = re.sub(r'href="/[^"]*"', 'href="#" data-inert="1"', html)
    html = re.sub(r"<form ", '<form onsubmit="return false" ', html)
    # The clock is normally driven by session.js; show a plausible reading instead.
    html = html.replace(">--:--:--<", ">00:07:24<")
    return html


def logo_rule() -> str:
    """The logo as one stylesheet rule, rather than a copy per screen.

    It appears on all twelve screens plus the landing hero, and inlining the
    data URI at each one tripled the file size.
    """
    logo = ROOT / "app" / "static" / "img" / "logo.png"
    return (
        "\n/* preview only: the duck, embedded once and reused */\n"
        ".wordmark-logo, .brand-logo {\n"
        f"  background: url('{data_uri(logo)}') center/contain no-repeat;\n"
        "}\n"
    )


def build():
    handle, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(handle)
    app = create_app(
        {
            "DATABASE": db_path,
            "SECRET_KEY": "preview-build-only",
            "PROXMOX_BACKEND": "simulate",
            "SERVER_NAME": "cyber-range.local",
        }
    )
    with app.app_context():
        init_db()
        seed()

    if SCORES_FROM and SCORES_FROM != PREVIEW_USER:
        swap_identities(app, PREVIEW_USER, SCORES_FROM)

    pages = capture(app)
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    css += logo_rule()

    tabs = "\n".join(
        f'      <button class="switch-tab" data-screen="{name}">{label}</button>'
        for name, label, _ in SCREENS
    )
    panels = "\n".join(
        f'  <section class="screen" id="screen-{name}" hidden>\n'
        f'    <p class="screen-note"><strong>{label}.</strong> {note}</p>\n'
        f"{inline_assets(pages[name])}\n  </section>"
        for name, label, note in SCREENS
    )

    document = TEMPLATE.replace("/*CSS*/", css).replace("<!--TABS-->", tabs).replace(
        "<!--PANELS-->", panels
    )

    out_dir = ROOT / "preview"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "pond-sec-preview.html"
    out_file.write_text(document, encoding="utf-8")
    os.unlink(db_path)

    size = out_file.stat().st_size / 1024
    print(f"Wrote {out_file} ({size:.0f} KB, {len(SCREENS)} screens)")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pond Sec — interface preview</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
/*CSS*/

/* --- preview chrome (not part of the platform) --- */
.switch {
  position: sticky; top: 0; z-index: 20;
  display: flex; flex-wrap: wrap; gap: .35rem; align-items: center;
  padding: .7rem clamp(1rem, 4vw, 3rem);
  background: #0e1a20; border-bottom: 1px solid #2c3d45;
}
.switch-title {
  font-family: var(--mono); font-size: .7rem; letter-spacing: .16em;
  text-transform: uppercase; color: #7f948e; margin-right: .9rem;
}
.switch-tab {
  font-family: var(--body); font-size: .85rem; color: #cfd8d5;
  background: transparent; border: 1px solid #2c3d45; border-radius: 3px;
  padding: .35rem .8rem; cursor: pointer;
}
.switch-tab:hover { border-color: var(--accent); color: #fff; }
.switch-tab[aria-current="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.screen-note {
  max-width: 1180px; margin: 0 auto; padding: .85rem clamp(1rem, 4vw, 3rem) 0;
  font-size: .88rem; color: var(--ink-soft);
}
.preview-foot {
  max-width: 1180px; margin: 0 auto; padding: 0 clamp(1rem, 4vw, 3rem) 3rem;
  font-size: .82rem; color: var(--ink-soft);
}
[data-inert] { cursor: default; }
</style>
</head>
<body>

<nav class="switch">
  <span class="switch-title">Interface preview</span>
<!--TABS-->
</nav>

<!--PANELS-->

<p class="preview-foot">
  Static preview — nothing here is clickable. Rendered from the live templates by
  <span class="mono">tools/build_preview.py</span>; run the app itself for the working version.
</p>

<script>
(function () {
  var tabs = document.querySelectorAll(".switch-tab");
  function show(name) {
    document.querySelectorAll(".screen").forEach(function (panel) {
      panel.hidden = panel.id !== "screen-" + name;
    });
    tabs.forEach(function (tab) {
      tab.setAttribute("aria-current", String(tab.dataset.screen === name));
    });
    window.scrollTo(0, 0);
  }
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { show(tab.dataset.screen); });
  });
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-inert]");
    if (link) event.preventDefault();
  });
  show(tabs[0].dataset.screen);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
