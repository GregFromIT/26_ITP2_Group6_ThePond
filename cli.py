#!/usr/bin/env python3
"""
cli.py - The Pond MVP front end.

The one entrypoint a user runs. Wraps two things:
  1. provisioner.py, for the actual Proxmox clone/start/stop/delete calls
  2. db/store.py's InstanceStore, for tracking state and expiry in SQLite

Usage:
    python3 cli.py init-db
    python3 cli.py create --challenge lockedshields --name team01 [--ttl-hours 3] [--vmid 301]
    python3 cli.py list [--status active]
    python3 cli.py destroy --name lockedshields-team01
    python3 cli.py sweep
"""
import argparse
import sys
import time
from pathlib import Path

import yaml

import provisioner
from db.store import InstanceStore

PROJECT_ROOT = Path(__file__).parent
CHALLENGES_DIR = PROJECT_ROOT / "vars" / "challenges"


def load_challenge(challenge_name: str) -> dict:
    path = CHALLENGES_DIR / f"{challenge_name}.yml"
    if not path.exists():
        print(f"error: no challenge config found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def next_free_vmid(store: InstanceStore, start: int, end: int) -> int:
    """Naive allocation: lowest VMID in range not already tracked in the db.
    Known limitation: this checks the db, not live Proxmox state, so a VMID
    created outside this tool won't be seen. Fine for a single-operator MVP;
    worth reconciling against Proxmox directly in v2."""
    used = {row["vmid"] for row in store.list_all()}
    for vmid in range(start, end + 1):
        if vmid not in used:
            return vmid
    raise RuntimeError(f"no free VMIDs in range {start}-{end}")


def cmd_init_db(args):
    store = InstanceStore()
    store.init_db()
    print(f"initialized db at {store.db_path}")


def cmd_create(args):
    challenge = load_challenge(args.challenge)
    config = provisioner.load_config()
    client = provisioner.get_client(config)
    store = InstanceStore()

    vmid = args.vmid or next_free_vmid(store, challenge["vmid_range_start"], challenge["vmid_range_end"])
    name = f"{args.challenge}-{args.name}"
    ttl_hours = args.ttl_hours or challenge["default_ttl_hours"]
    node = config["proxmox_node"]

    print(f"creating {name} (vmid {vmid}) from template {challenge['vm_template']}...")
    provisioner.create_instance(
        client,
        node=node,
        vm_template=challenge["vm_template"],
        vm_name=name,
        vmid=vmid,
        storage=config["vm_storage"],
    )

    record = store.create(
        name=name,
        challenge=args.challenge,
        vmid=vmid,
        node=node,
        ttl_hours=ttl_hours,
    )
    print(f"created: {record}")


def cmd_list(args):
    store = InstanceStore()
    for row in store.list_all(status=args.status):
        expires_in = row["expires_at"] - int(time.time())
        print(f"  vmid={row['vmid']:<5} name={row['name']:<30} status={row['status']:<10} "
              f"expires_in={expires_in}s")


def cmd_destroy(args):
    config = provisioner.load_config()
    client = provisioner.get_client(config)
    store = InstanceStore()
    record = store.get_by_name(args.name)
    if record is None:
        print(f"error: no instance named {args.name}", file=sys.stderr)
        sys.exit(1)

    print(f"destroying {record['name']} (vmid {record['vmid']})...")
    provisioner.destroy_instance(client, node=record["node"], vmid=record["vmid"])
    store.mark_destroyed(record["vmid"])
    print("destroyed.")


def cmd_sweep(args):
    config = provisioner.load_config()
    client = provisioner.get_client(config)
    store = InstanceStore()
    expired = store.list_expired()
    if not expired:
        print("no expired instances.")
        return
    for record in expired:
        print(f"expired: {record['name']} (vmid {record['vmid']}) - destroying...")
        provisioner.destroy_instance(client, node=record["node"], vmid=record["vmid"])
        store.mark_destroyed(record["vmid"])
    print(f"swept {len(expired)} instance(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Pond - CTF instance manager")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    p = sub.add_parser("create")
    p.add_argument("--challenge", required=True, help="challenge name, matches vars/challenges/<name>.yml")
    p.add_argument("--name", required=True, help="instance suffix, e.g. team01")
    p.add_argument("--ttl-hours", type=int, default=None)
    p.add_argument("--vmid", type=int, default=None, help="override auto-allocated VMID")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("list")
    p.add_argument("--status", default=None, choices=["active", "destroyed"])
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("destroy")
    p.add_argument("--name", required=True, help="full instance name, e.g. lockedshields-team01")
    p.set_defaults(func=cmd_destroy)

    sub.add_parser("sweep").set_defaults(func=cmd_sweep)

    args = parser.parse_args()
    args.func(args)
