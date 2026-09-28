"""Build the use case diagram.

    python tools/build_use_case_diagram.py

Writes docs/diagrams/use-case-diagram.svg, plus a PNG beside it if cairosvg is
installed (pip install cairosvg). The PNG renders at 2x so it stays sharp when
pasted into Word.

Everything is driven by the tables below: UC holds the use cases and their
positions, then ACTORS, LINKS, INCLUDES and EXTENDS hold the relationships. To
move a use case, change its coordinates here rather than editing the SVG by
hand.

docs/diagrams/use-case-diagram.puml is the same diagram as PlantUML source for
anyone who prefers editing it that way. Change one, change the other.
"""

W, H = 1460, 1270
BOX = dict(x=300, y=60, w=580, h=1115)          # system boundary
INK, SOFT, RULE = "#16232b", "#55676f", "#9fada7"
ACCENT, SIGNAL = "#14706b", "#b8701a"

# (id, label, cx, cy, column-width)
UC = [
    ("register",  "Register account",            440, 120, 105),
    ("signin",    "Sign in",                     440, 195, 105),
    ("chgpw",     "Change own password",         440, 270, 105),
    ("boards",    "View dashboard\nand leaderboards", 440, 350, 105),
    ("browse",    "Browse themes\nand challenges",    440, 435, 105),
    ("launch",    "Launch challenge",            440, 520, 105),
    ("console",   "Open VM console",             440, 595, 105),
    ("flag",      "Submit flag",                 440, 670, 105),
    ("close",     "Close challenge",             440, 750, 105),

    ("lockout",   "Lock account after\n3 failures",   735, 195, 100),
    ("clone",     "Clone and start VM",          735, 520, 100),
    ("grade",     "Grade flag,\naward points",   735, 670, 100),
    ("destroy",   "Stop and destroy VM",         735, 750, 100),

    ("accounts",  "View accounts\nand sessions",  440, 860, 105),
    ("unlock",    "Unlock or lock\nan account",   440, 945, 105),
    ("forceclose","Force-close a session",        440, 1025, 105),
    ("temppw",    "Issue temporary\npassword",    735, 860, 100),
    ("audit",     "Read audit log",               735, 945, 100),
    ("roles",     "Grant or remove\nmoderator / admin", 735, 1025, 100),
    ("approve",   "Approve or reject\nregistration",   440, 1105, 105),
    ("upload",    "Upload VM image",              735, 1105, 100),
]
POS = {i: (x, y, r) for i, _, x, y, r in UC}

ACTORS = [
    ("student", "Student",       155, 400),
    ("mod",     "Moderator",     155, 880),
    ("admin",   "Administrator", 155, 1105),
    ("pve",     "Proxmox VE",   1135, 620),
]
APOS = {i: (x, y) for i, _, x, y in ACTORS}

# actor -> use case (plain association)
LINKS = [
    ("student", "register"), ("student", "signin"), ("student", "chgpw"),
    ("student", "boards"), ("student", "browse"), ("student", "launch"),
    ("student", "console"), ("student", "flag"), ("student", "close"),
    ("mod", "accounts"), ("mod", "unlock"), ("mod", "forceclose"),
    ("mod", "temppw"), ("mod", "audit"),
    ("admin", "roles"), ("admin", "approve"), ("admin", "upload"),
]
PVE_LINKS = ["clone", "destroy"]
INCLUDES = [("launch", "clone"), ("flag", "grade"), ("close", "destroy")]
EXTENDS = [("lockout", "signin")]


def ellipse(uc_id, label, rx):
    x, y, _ = POS[uc_id]
    lines = label.split("\n")
    ry = 34 if len(lines) == 1 else 40
    out = [f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="#fff" stroke="{INK}" stroke-width="1.5"/>']
    start = y + 5 - (len(lines) - 1) * 9
    for i, line in enumerate(lines):
        out.append(f'<text x="{x}" y="{start + i * 18}" text-anchor="middle" '
                   f'font-family="Inter, sans-serif" font-size="14" fill="{INK}">{line}</text>')
    return "\n".join(out)


def actor(aid, label, x, y):
    return f"""
<g stroke="{INK}" stroke-width="1.8" fill="none">
  <circle cx="{x}" cy="{y - 34}" r="12" fill="#fff"/>
  <line x1="{x}" y1="{y - 22}" x2="{x}" y2="{y + 8}"/>
  <line x1="{x - 18}" y1="{y - 12}" x2="{x + 18}" y2="{y - 12}"/>
  <line x1="{x}" y1="{y + 8}" x2="{x - 14}" y2="{y + 32}"/>
  <line x1="{x}" y1="{y + 8}" x2="{x + 14}" y2="{y + 32}"/>
</g>
<text x="{x}" y="{y + 52}" text-anchor="middle" font-family="Inter, sans-serif"
      font-size="15" font-weight="600" fill="{INK}">{label}</text>"""


def edge_point(uc_id, from_x):
    """Where a line should meet an ellipse, on whichever side the actor is."""
    x, y, rx = POS[uc_id]
    return (x - rx, y) if from_x < x else (x + rx, y)


parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
         f'<rect width="{W}" height="{H}" fill="#edf0ee"/>',
         '<defs>',
         f'<marker id="open" markerWidth="10" markerHeight="10" refX="9" refY="3.5" orient="auto">'
         f'<path d="M0,0 L9,3.5 L0,7" fill="none" stroke="{SOFT}" stroke-width="1.4"/></marker>',
         f'<marker id="tri" markerWidth="12" markerHeight="12" refX="11" refY="5" orient="auto">'
         f'<path d="M0,0 L11,5 L0,10 Z" fill="#fff" stroke="{INK}" stroke-width="1.4"/></marker>',
         '</defs>']

# title
parts.append(f'<text x="40" y="46" font-family="Space Grotesk, sans-serif" font-size="24" '
             f'font-weight="700" fill="{INK}">Pond Sec — use case diagram</text>')

# system boundary
parts.append(f'<rect x="{BOX["x"]}" y="{BOX["y"]}" width="{BOX["w"]}" height="{BOX["h"]}" '
             f'rx="4" fill="#f7f9f8" stroke="{RULE}" stroke-width="1.5"/>')
parts.append(f'<text x="{BOX["x"] + BOX["w"] / 2}" y="{BOX["y"] + 28}" text-anchor="middle" '
             f'font-family="IBM Plex Mono, monospace" font-size="13" letter-spacing="2" '
             f'fill="{SOFT}">POND SEC</text>')

# group labels, rotated up the inside edge so they never sit under an ellipse
for label, y in [("Accounts", 265), ("Challenges", 660), ("Staff console", 1010)]:
    x = BOX["x"] + 18
    parts.append(f'<text x="{x}" y="{y}" transform="rotate(-90 {x} {y})" '
                 f'font-family="IBM Plex Mono, monospace" font-size="10" letter-spacing="2" '
                 f'fill="{RULE}">{label.upper()}</text>')

# associations
for aid, uc in LINKS:
    ax, ay = APOS[aid]
    ex, ey = edge_point(uc, ax)
    parts.append(f'<line x1="{ax + 22}" y1="{ay - 6}" x2="{ex}" y2="{ey}" '
                 f'stroke="{INK}" stroke-width="1.2" opacity="0.55"/>')

for uc in PVE_LINKS:
    ax, ay = APOS["pve"]
    ex, ey = edge_point(uc, ax)
    parts.append(f'<line x1="{ax - 22}" y1="{ay - 6}" x2="{ex}" y2="{ey}" '
                 f'stroke="{INK}" stroke-width="1.2" opacity="0.55"/>')

# include / extend
for src, dst in INCLUDES:
    sx, sy, srx = POS[src]
    dx, dy, drx = POS[dst]
    parts.append(f'<line x1="{sx + srx}" y1="{sy}" x2="{dx - drx}" y2="{dy}" stroke="{SOFT}" '
                 f'stroke-width="1.4" stroke-dasharray="6 4" marker-end="url(#open)"/>')
    parts.append(f'<text x="{(sx + srx + dx - drx) / 2}" y="{(sy + dy) / 2 - 8}" text-anchor="middle" '
                 f'font-family="IBM Plex Mono, monospace" font-size="11" fill="{SOFT}">&#171;include&#187;</text>')

for src, dst in EXTENDS:
    sx, sy, srx = POS[src]
    dx, dy, drx = POS[dst]
    parts.append(f'<line x1="{sx - srx}" y1="{sy}" x2="{dx + drx}" y2="{dy}" stroke="{SIGNAL}" '
                 f'stroke-width="1.4" stroke-dasharray="6 4" marker-end="url(#open)"/>')
    parts.append(f'<text x="{(sx - srx + dx + drx) / 2}" y="{sy - 8}" text-anchor="middle" '
                 f'font-family="IBM Plex Mono, monospace" font-size="11" fill="{SIGNAL}">&#171;extend&#187;</text>')

# actor generalisation: moderator is a student, admin is a moderator
for child, parent in [("mod", "student"), ("admin", "mod")]:
    cx, cy = APOS[child]
    px, py = APOS[parent]
    parts.append(f'<line x1="{cx}" y1="{cy - 62}" x2="{px}" y2="{py + 60}" stroke="{INK}" '
                 f'stroke-width="1.5" marker-end="url(#tri)"/>')

# use cases and actors on top of the lines
for uc_id, label, _, _, rx in UC:
    parts.append(ellipse(uc_id, label, rx))
for aid, label, x, y in ACTORS:
    parts.append(actor(aid, label, x, y))

# legend
lx, ly = 980, 800
parts.append(f'<rect x="{lx}" y="{ly}" width="300" height="126" rx="3" fill="#fff" stroke="{RULE}"/>')
parts.append(f'<text x="{lx + 14}" y="{ly + 24}" font-family="IBM Plex Mono, monospace" font-size="10" '
             f'letter-spacing="1.5" fill="{SOFT}">KEY</text>')
rows = [
    (f'stroke="{INK}" stroke-width="1.2" opacity="0.55"', "association"),
    (f'stroke="{SOFT}" stroke-width="1.4" stroke-dasharray="6 4" marker-end="url(#open)"', "\u00abinclude\u00bb"),
    (f'stroke="{SIGNAL}" stroke-width="1.4" stroke-dasharray="6 4" marker-end="url(#open)"', "\u00abextend\u00bb"),
    (f'stroke="{INK}" stroke-width="1.5" marker-end="url(#tri)"', "generalisation"),
]
for i, (style, label) in enumerate(rows):
    y = ly + 48 + i * 22
    parts.append(f'<line x1="{lx + 14}" y1="{y}" x2="{lx + 64}" y2="{y}" {style}/>')
    parts.append(f'<text x="{lx + 76}" y="{y + 4}" font-family="Inter, sans-serif" font-size="12" '
                 f'fill="{INK}">{label}</text>')

# note
nx, ny = 980, 960
parts.append(f'<rect x="{nx}" y="{ny}" width="300" height="128" rx="3" fill="#fff" stroke="{RULE}"/>')
note = ["Moderators inherit every", "student use case, and appear", "on the leaderboards.",
        "", "Administrators inherit every", "moderator use case, but are", "excluded from every board."]
for i, line in enumerate(note):
    parts.append(f'<text x="{nx + 14}" y="{ny + 26 + i * 16}" font-family="Inter, sans-serif" '
                 f'font-size="12" fill="{SOFT}">{line}</text>')

parts.append('</svg>')
import os

out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "diagrams")
os.makedirs(out_dir, exist_ok=True)
svg_path = os.path.join(out_dir, "use-case-diagram.svg")
with open(svg_path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(parts))
print("wrote", svg_path)

try:
    import cairosvg
except ImportError:
    print("cairosvg not installed, skipping the PNG (pip install cairosvg)")
else:
    png_path = os.path.join(out_dir, "use-case-diagram.png")
    cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=W * 2)
    print("wrote", png_path)
