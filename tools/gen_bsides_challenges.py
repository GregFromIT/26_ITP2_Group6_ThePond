#!/usr/bin/env python3
"""Generate vars/challenges/<slug>.yml for the BSides Canberra 2025 container
challenges, reading title/category/difficulty/description/flag from each
challenge's noctf.yaml so nothing is copied by hand.

    python3 tools/gen_bsides_challenges.py /path/to/bsides-cbr-2025-challenges

Writes into vars/challenges/ (relative to the repo root). Existing files are
left alone unless --force is given.
"""
import argparse
from pathlib import Path

import yaml

HOST = "10.1.30.10"          # Docker host address on pondnc
FILES_PORT = 8000            # pond-files static server
ENTRY_TEMPLATE = "Kali-attacker"
POINTS = {"easy": 100, "medium": 200, "hard": 300}

# slug, path in the BSides repo, host port (must match /opt/pond-deploy.sh)
CHALLENGES = [
    ("dockjmp", "pwn/dockjmp", 31301),
    ("encrypted-file-server", "pwn/encrypted-file-server", 31302),
    ("wordgame", "pwn/wordgame", 31303),
    ("libeatpan", "misc/libeatpan", 31304),
    ("supersanic-v1.0", "crypto/supersanic-v1.0", 31305),
    ("supersanic-v2.0", "crypto/supersanic-v2.0", 31306),
    ("cheesecake-shop", "crypto/cheesecake-shop", 31307),
    ("curveball", "crypto/curveball", 31308),
    ("password-game", "misc/password-game", 31309),
    ("python-crossword", "misc/python-crossword", 31310),
    ("shadow-the-hedgedog", "web/shadow-the-hedgedog", 31311),
    ("dog-blog", "web/dog-blog", 31312),
    ("reverse-pawxy", "web/reverse-pawxy", 31313),
    ("pensig", "rev/pensig", 31314),
    ("logbook", "web/logbook", 31315),
]


def instructions(slug, port, hosting, files):
    lines = ["Open the console to use your attacker machine."]
    if hosting == "tcp":
        lines.append(f"Connect to the challenge:  nc {HOST} {port}")
    else:  # "tls" in the original event = a web challenge; served as plain HTTP here
        lines.append(f"Open the challenge:  http://{HOST}:{port}/")
    for f in files:
        name = Path(f).name
        lines.append(f"Download:  curl -O http://{HOST}:{FILES_PORT}/{slug}/{name}")
    lines.append("Flag format: skbdg{...}")
    return "\n".join(lines) + "\n"


def build(bsides: Path, slug, path, port):
    meta = yaml.safe_load((bsides / path / "noctf.yaml").read_text())
    flags = meta.get("flags") or []
    if len(flags) != 1:
        raise SystemExit(f"{slug}: expected exactly 1 flag, found {len(flags)}")
    difficulty = meta["difficulty"]
    return {
        "challenge": slug,
        "category": meta["categories"][0],
        "difficulty": difficulty,
        "description": meta["description"].strip() + "\n",
        "instructions": instructions(slug, port, meta["hosting"], meta.get("files") or []),
        "vm_templates": [{"name": ENTRY_TEMPLATE, "role": "attacker"}],
        "flag": flags[0],
        "points": POINTS[difficulty],
    }


class _Literal(str):
    pass


yaml.SafeDumper.add_representer(
    _Literal, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bsides", type=Path, help="path to the bsides-cbr-2025-challenges clone")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "vars" / "challenges")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for slug, path, port in CHALLENGES:
        data = build(args.bsides, slug, path, port)
        for key in ("description", "instructions"):
            data[key] = _Literal(data[key])
        target = args.out / f"{slug}.yml"
        if target.exists() and not args.force:
            print(f"skip  {target} (exists; use --force)")
            continue
        target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        print(f"wrote {target}  ({data['difficulty']}, {data['points']} pts)")


if __name__ == "__main__":
    main()
