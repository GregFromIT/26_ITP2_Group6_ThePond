#!/usr/bin/env python3
"""Generate The Pond challenge YAMLs + a Docker-host deploy script for BSidesSF CTF 2024.

Source: https://github.com/BSidesSF/ctf-2024-release - each challenge folder has
metadata.yml (name, flag, description, tags, port), challenge/Dockerfile (hosted
ones) and distfiles/ (the handouts).

Same pattern as tools/gen_bsides_challenges.py (BSides CBR 2025):
  * every challenge launches the shared Red Duck template (Kali-attacker)
  * hosted challenges run as one container each on the Docker host (10.1.30.10)
  * handouts are served by pond-files on :8000 and pulled onto Red Duck at first boot

Usage (repo root, venv active):
    git clone --depth 1 https://github.com/BSidesSF/ctf-2024-release ~/ctf-2024-release
    python3 tools/gen_bsidessf24_challenges.py ~/ctf-2024-release
Writes vars/challenges/<slug>.yml and tools/pond-deploy-sf24.sh.
"""
import sys
from pathlib import Path

import yaml

HOST = "10.1.30.10"
FILES_PORT = 8000
ENTRY_TEMPLATE = "Kali-attacker"
REPO_URL = "https://github.com/BSidesSF/ctf-2024-release"

# (slug, host_port) - one container each. Host ports carry on from CBR's 31301-31315.
HOSTED = [
    ("cant-give-in", 31316), ("cant-give-in-secure", 31317), ("cant-give-in-securer", 31318),
    # cwa/cwa2 (31319/31320) dropped: the release is missing cmd/cwa/main.go - the
    # repo's own .gitignore line "cwa" excluded it - so `go build ./cmd/cwa` cannot work.
    ("cyberdyneadc", 31321), ("humain", 31322),
    ("meow", 31323), ("no-tools", 31324), ("opnukeagi", 31325), ("safer-streets", 31326),
    ("slay-the-spider", 31327), ("truthescrow", 31328), ("turing-complete", 31329),
    ("turing-incomplete", 31330), ("turing-incomplete64", 31331),
]
# Second flag on another challenge's service: slug -> service slug.
SHARED_SERVICE = {"truthescrow-2": "truthescrow"}
# File-only challenges (no server). Value = folder whose distfiles/ are the handouts.
OFFLINE = {
    "aicodebook": "aicodebook", "crossworthy": "crossworthy", "doctor": "doctor",
    "javai": "javai", "kodakthriller": "kodakthriller", "llflagprinter": "llflagprinter",
    "lostinthevoid": "lostinthevoid", "missing-characters": "missing-characters",
    "redacted": "redacted", "sgai": "sgai", "sgai-2": "sgai", "sgai-3": "sgai", "sgai-4": "sgai",
    "shinji": "shinji", "undelete": "undelete", "wishing-well": "wishing-well", "ztxt": "ztxt",
}
# First matching tag decides the category; values match the existing BSides CBR categories.
CATEGORY = {"pwn": "pwn", "web": "web", "crypto": "crypto", "re": "rev", "mobile": "rev",
            "forensics": "forensics", "misc": "misc", "terminal": "misc"}


def category(tags):
    for t in tags:
        if t.lower() in CATEGORY:
            return CATEGORY[t.lower()]
    return "misc"


def difficulty(tags):
    # BSidesSF scores dynamically (value: 1000 for all), so '101' is the only signal.
    return ("easy", 100) if "101" in tags else ("medium", 200)


def files_line(slug):
    return (f"Handout files (if any) are on Red Duck in ~/Desktop/{slug} "
            f"(also http://{HOST}:{FILES_PORT}/{slug}/).")


# metadata.yml says 'tcp' for these, but they are Apache CGI apps (FROM httpd) - i.e. HTTP.
HTTP_OVERRIDE = {"cant-give-in", "cant-give-in-secure", "cant-give-in-securer"}


def connect_lines(meta, host_port, slug):
    if meta.get("protocol") in ("http", "https") or slug in HTTP_OVERRIDE:
        return f"Open http://{HOST}:{host_port}/ from Red Duck (browser or curl)."
    return f"From Red Duck's terminal:\n  nc {HOST} {host_port}"


def main(src):
    src = Path(src).expanduser()
    out = Path("vars/challenges")
    out.mkdir(parents=True, exist_ok=True)
    ports = dict(HOSTED)
    meta = {}

    def load(slug):
        if slug not in meta:
            meta[slug] = yaml.safe_load((src / slug / "metadata.yml").read_text())
        return meta[slug]

    for slug in [s for s, _ in HOSTED] + list(SHARED_SERVICE) + list(OFFLINE):
        m = load(slug)
        tags = [str(t) for t in (m.get("tags") or [])]
        diff, points = difficulty(tags)
        if slug in ports:
            how = connect_lines(m, ports[slug], slug)
        elif slug in SHARED_SERVICE:
            svc = SHARED_SERVICE[slug]
            how = connect_lines(load(svc), ports[svc], svc)
        else:
            how = "No server for this one - everything you need is in the handout files."
        cfg = {
            "challenge": slug,
            "vm_templates": [{"name": ENTRY_TEMPLATE, "role": "attacker"}],
            "category": category(tags),
            "difficulty": diff,
            "description": f"{m['description'].strip()}\n\nAuthor: {m.get('author', 'unknown')} (BSidesSF CTF 2024)",
            "instructions": f"{how}\n{files_line(slug)}",
            "flag": m["flag"],
            "points": points,
        }
        (out / f"{slug}.yml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        print(f"wrote {out / (slug + '.yml')}")

    # Docker-host deploy script, built from the same table so ports always match the YAMLs.
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by tools/gen_bsidessf24_challenges.py - run on the Docker host with sudo.",
        "set -euo pipefail",
        f"REPO=/opt/bsidessf24; FILES=/opt/pond-files; BIND={HOST}",
        f'[ -d "$REPO" ] || git clone --depth 1 {REPO_URL} "$REPO"',
        "",
        "# --memory/--cpus mirror the limits in each challenge's deployment/deployment.yml.",
        "run() {  # slug container_port host_port",
        '  echo "== $1"',
        '  docker build -q -t "pond-sf24-$1" "$REPO/$1/challenge"',
        '  docker rm -f "pond-$1" >/dev/null 2>&1 || true',
        '  docker run -d --name "pond-$1" --restart unless-stopped --memory 1g --cpus 1 \\',
        '    -p "$BIND:$3:$2" "pond-sf24-$1" >/dev/null',
        "}",
        "stage() {  # slug source_folder  (cp -L: several distfiles are symlinks into challenge/)",
        '  mkdir -p "$FILES/$1"',
        '  if compgen -G "$REPO/$2/distfiles/*" >/dev/null; then cp -L "$REPO/$2"/distfiles/* "$FILES/$1/"; fi',
        "}",
        "",
    ]
    for slug, hp in HOSTED:
        lines.append(f"run {slug} {int(load(slug)['port'])} {hp}")
    lines.append("")
    for slug, _ in HOSTED:
        lines.append(f"stage {slug} {slug}")
    for slug, svc in SHARED_SERVICE.items():
        lines.append(f"stage {slug} {svc}")
    for slug, folder in OFFLINE.items():
        lines.append(f"stage {slug} {folder}")
    lines.append('chmod -R a+rX "$FILES"')
    lines.append('echo "done: $(docker ps --filter name=pond- -q | wc -l) pond containers running"')
    dep = Path("tools/pond-deploy-sf24.sh")
    dep.parent.mkdir(exist_ok=True)
    dep.write_text("\n".join(lines) + "\n")
    dep.chmod(0o755)
    print(f"wrote {dep}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])