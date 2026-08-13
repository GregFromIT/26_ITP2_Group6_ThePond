"""Seed data for development and demos.

Loaded by `flask --app wsgi seed-db`. Three themes x six challenges, matching the
MVP scope in the client contract.

THIS IS WHERE CONTENT LIVES until someone builds the staff interface. Adding a
theme needs no code change anywhere else:

    1. append a dict to THEMES with exactly six challenges
    2. each challenge tuple is (name, brief, difficulty, vm_index) where
       vm_index points into the VMS list above
    3. add a tile SVG to app/static/img/ and name it in "tile"
    4. re-run init-db then seed-db

The dashboard and the theme pages iterate over whatever is in the database, so a
fourth theme appears on both without touching a template.

FLAGS: the plaintext here exists only so the team can test the submission flow.
Every flag is stored as a hash (see scoring.hash_flag), and all six challenges in
every theme currently share the same flag values — fine for a demo, useless
for an assessment. Before real students use this, give each challenge its own flags,
and generate them per-VM rather than storing one shared value, or the first
student to solve one can hand the answer to everyone.

_fabricate_scores() invents leaderboard history so the boards are not empty in a
demo. Delete that call before any real cohort uses the platform.
"""

import random
from datetime import datetime, timedelta

from .db import execute, query
from .scoring import hash_flag
from .security import set_password

# (name, node, template_vmid, cores, memory_mb, notes)
#
# The node is "pve" because that is the only node on this cluster. The
# template_vmids are PLACEHOLDERS — nothing at 9101 etc. exists yet. Before
# PROXMOX_BACKEND=api is switched on, build the templates in Proxmox and put
# their real vmids here, or every launch will fail with "no such VM".
VMS = [
    ("net-core-l1", "pve", 9101, 2, 2048, "Small routed lab, two subnets"),
    ("net-core-l2", "pve", 9102, 2, 4096, "Adds VLANs and a firewall"),
    ("forensics-w10", "pve", 9201, 4, 4096, "Windows 10 disk image workstation"),
    ("forensics-lin", "pve", 9202, 2, 4096, "Linux host with deleted artefacts"),
    ("web-dvwa", "pve", 9301, 2, 2048, "Deliberately vulnerable web stack"),
    ("web-api", "pve", 9302, 2, 2048, "REST API with broken access control"),
] # change to vars/challenges

THEMES = [
    {
        "name": "Networking",
        "category": "networking",
        "summary": "Read a network you did not build: map it, follow the traffic, "
                   "then work out what is being hidden inside it.",
        "weighting": 1.0,
        "tile": "tile-networking.svg",
        "challenges": [
            ("Link check", "Two hosts cannot see each other. Find out where the path breaks.", "Entry", 0),
            ("Sweep", "Enumerate the subnet and identify every listening service.", "Entry", 0),
            ("Capture", "A packet capture holds a credential sent in the clear.", "Working", 1),
            ("Segmented", "VLANs stand between you and the file server.", "Working", 1),
            ("Filtered", "The firewall drops most of what you try. Find what it allows.", "Hard", 1),
            ("Tunnelled", "Traffic is leaving on a port that should not carry it.", "Hard", 1),
        ],
    },
    {
        "name": "Digital Forensics",
        "category": "forensics",
        "summary": "Recover what someone tried to remove. Disk artefacts, deleted "
                   "files, timestamps that do not agree with each other.",
        "weighting": 1.1,
        "tile": "tile-forensics.svg",
        "challenges": [
            ("First look", "Mount the image read-only and describe what you have.", "Entry", 2),
            ("Deleted", "A file was removed. It is still on the disk.", "Entry", 2),
            ("Timeline", "Build a timeline and find the hour that does not fit.", "Working", 3),
            ("Carved", "No file table entry. Carve the file out by its header.", "Working", 3),
            ("Registry", "The registry records a device that is no longer attached.", "Hard", 2),
            ("Anti-forensics", "Timestamps were edited. Prove it.", "Hard", 3),
        ],
    },
    {
        "name": "Web Application Security",
        "category": "web",
        "summary": "A running web stack with real defects. Find them the way an "
                   "attacker would, then write down how you would fix them.",
        "weighting": 1.2,
        "tile": "tile-web.svg",
        "challenges": [
            ("Recon", "Map the application: routes, parameters, error behaviour.", "Entry", 4),
            ("Injection", "One input reaches the database without being handled.", "Working", 4),
            ("Session", "The session cookie tells you more than it should.", "Working", 4),
            ("Access control", "The API trusts an identifier the client controls.", "Hard", 5),
            ("Upload", "File upload validation checks the wrong thing.", "Hard", 5),
            ("Chain", "Three small defects add up to an account takeover.", "Hard", 5),
        ],
    },
]

FLAG_SETS = [
    [("First flag", "flag{challenge_one_entry}", 50), ("Bonus flag", "flag{challenge_one_bonus}", 25)],
    [("First flag", "flag{challenge_two_entry}", 75), ("Bonus flag", "flag{challenge_two_bonus}", 25)],
    [("First flag", "flag{challenge_three_entry}", 100), ("Bonus flag", "flag{challenge_three_bonus}", 50)],
    [("First flag", "flag{challenge_four_entry}", 125), ("Bonus flag", "flag{challenge_four_bonus}", 50)],
    [("First flag", "flag{challenge_five_entry}", 150), ("Bonus flag", "flag{challenge_five_bonus}", 75)],
    [("First flag", "flag{challenge_six_entry}", 200), ("Bonus flag", "flag{challenge_six_bonus}", 100)],
]

# (name, year, username, role). Roles here are for the demo only — a
# real deployment starts with everyone as a student and promotes the first
# administrator with `flask --app wsgi set-role <username> admin`.
DEMO_USERS = [
    ("Ben Turnbull", "Staff", "bpt", "admin"),
    ("Vasili Stergiou", "Year 3", "vstergiou", "moderator"),
    ("Megan Bates", "Year 3", "mbates", "student"),
    ("Lochlan Hardie", "Year 3", "lhardie", "student"),
    ("Gareth Thomas", "Year 3", "gthomas", "student"),
    ("Demo Student", "Year 2", "demo", "student"),
]
# Shared password for every seeded account. Deliberately short and memorable so
# the team can demo quickly.
#
# NOTE FOR WHOEVER DEPLOYS THIS: "rootroot" is 8 characters and is below the
# platform's own MIN_PASSWORD_LENGTH of 12. It works here only because seeding
# calls set_password() directly, and the length rules live in the registration
# and reset VIEWS rather than in the hashing layer. A student could not choose
# this password through the web forms, and neither should a real account.
#
# Before a real cohort touches this platform: delete the seeded accounts, or at
# minimum run `flask --app wsgi set-role` on nothing and reset every demo
# password. A shared credential published in a public repo is the first thing
# anyone will try.
DEMO_PASSWORD = "rootroot"


def seed():
    """Load VMs, themes, challenges, flags and demo accounts, in that order.

    Order matters: challenges reference VMs, and flags reference challenges.
    """
    vm_ids = [
        execute(
            "INSERT INTO vm (name, proxmox_node, template_vmid, cores, memory_mb, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            vm,
        )
        for vm in VMS
    ]

    challenge_ids = []
    for order, spec in enumerate(THEMES):
        theme_id = execute(
            "INSERT INTO theme (name, category, summary, weighting, tile_image, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec["name"], spec["category"], spec["summary"], spec["weighting"],
             spec["tile"], order),
        )
        for number, (name, brief, difficulty, vm_index) in enumerate(spec["challenges"], start=1):
            challenge_id = execute(
                "INSERT INTO challenge "
                "(theme_id, challenge_number, name, brief, difficulty, vm_id, tile_image) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (theme_id, number, name, brief, difficulty, vm_ids[vm_index], spec["tile"]),
            )
            challenge_ids.append(challenge_id)
            for label, flag, points in FLAG_SETS[number - 1]:
                execute(
                    "INSERT INTO challenge_points "
                    "(challenge_id, theme_id, label, flag_hash, points) VALUES (?, ?, ?, ?, ?)",
                    (challenge_id, theme_id, label, hash_flag(flag), points),
                )

    for name, year, username, role in DEMO_USERS:
        user_id = execute(
            "INSERT INTO user (name, uni_year, username, role) VALUES (?, ?, ?, ?)",
            (name, year, username, role),
        )
        set_password(user_id, DEMO_PASSWORD)

    _fabricate_scores()


def _fabricate_scores():
    """Give the leaderboards something to show. Demo data only.

    Everyone eligible for the boards, which is everyone except administrators.
    Moderators are usually students helping run a class, so they compete like
    anyone else.
    """
    random.seed(12)
    users = query(
        "SELECT user_id, username FROM user WHERE role != 'admin' AND username != 'demo'"
    )
    flags = query("SELECT flag_id, theme_id, challenge_id, points FROM challenge_points")

    for user in users:
        captured = random.sample(flags, k=random.randint(6, 18))
        total = 0
        for offset, flag in enumerate(captured):
            awarded_at = (
                datetime.utcnow() - timedelta(days=random.randint(0, 9), minutes=offset * 37)
            ).strftime("%Y-%m-%d %H:%M:%S")
            execute(
                "INSERT OR IGNORE INTO user_challenge_points "
                "(user_id, flag_id, theme_id, challenge_id, points_awarded, awarded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["user_id"], flag["flag_id"], flag["theme_id"], flag["challenge_id"],
                 flag["points"], awarded_at),
            )
            execute(
                "INSERT INTO flag_submission (user_id, challenge_id, was_correct, submitted_at) "
                "VALUES (?, ?, 1, ?)",
                (user["user_id"], flag["challenge_id"], awarded_at),
            )
            total += flag["points"]

        for _ in range(random.randint(2, 9)):   # the misses count as flags played
            miss = random.choice(flags)
            execute(
                "INSERT INTO flag_submission (user_id, challenge_id, was_correct) VALUES (?, ?, 0)",
                (user["user_id"], miss["challenge_id"]),
            )

        execute("UPDATE user SET points = ? WHERE user_id = ?", (total, user["user_id"]))
