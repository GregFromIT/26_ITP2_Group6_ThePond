#!/usr/bin/env python3
"""
app.py - The Pond web GUI (MVP boilerplate).

Thin Flask front end over the same building blocks cli.py uses:
provisioner.py for Proxmox calls, db/store.py for state. Deliberately
minimal - plain server-rendered forms, no auth, no JS, no API layer - so
it's cheap to rip out and replace with a real frontend later without
touching provisioner.py or db/store.py. Every route here is a direct
stand-in for one cli.py subcommand.

Usage:
    python3 app.py
"""
import os
import time
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

import provisioner
from cli import load_challenge, next_free_vmid
from db.store import InstanceStore

PROJECT_ROOT = Path(__file__).parent
CHALLENGES_DIR = PROJECT_ROOT / "vars" / "challenges"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-for-production")


def list_challenge_names() -> list[str]:
    return sorted(p.stem for p in CHALLENGES_DIR.glob("*.yml"))


@app.route("/")
def index():
    store = InstanceStore()
    status = request.args.get("status") or None
    instances = store.list_all(status=status)
    now = int(time.time())
    for row in instances:
        row["expires_in"] = row["expires_at"] - now
    return render_template(
        "index.html",
        instances=instances,
        challenges=list_challenge_names(),
        status=status,
    )


@app.route("/create", methods=["POST"])
def create():
    challenge_name = request.form["challenge"]
    name = request.form["name"]
    ttl_hours = request.form.get("ttl_hours") or None
    vmid = request.form.get("vmid") or None

    try:
        challenge = load_challenge(challenge_name)
        config = provisioner.load_config()
        client = provisioner.get_client(config)
        store = InstanceStore()

        vmid = int(vmid) if vmid else next_free_vmid(
            store, challenge["vmid_range_start"], challenge["vmid_range_end"]
        )
        full_name = f"{challenge_name}-{name}"
        ttl_hours = int(ttl_hours) if ttl_hours else challenge["default_ttl_hours"]
        node = config["proxmox_node"]

        provisioner.create_instance(
            client,
            node=node,
            vm_template=challenge["vm_template"],
            vm_name=full_name,
            vmid=vmid,
            storage=config["vm_storage"],
        )
        store.create(name=full_name, challenge=challenge_name, vmid=vmid, node=node, ttl_hours=ttl_hours)
        flash(f"created {full_name} (vmid {vmid})", "success")
    except Exception as exc:
        flash(f"create failed: {exc}", "error")

    return redirect(url_for("index"))


@app.route("/destroy/<name>", methods=["POST"])
def destroy(name):
    try:
        config = provisioner.load_config()
        client = provisioner.get_client(config)
        store = InstanceStore()
        record = store.get_by_name(name)
        if record is None:
            flash(f"no instance named {name}", "error")
        else:
            provisioner.destroy_instance(client, node=record["node"], vmid=record["vmid"])
            store.mark_destroyed(record["vmid"])
            flash(f"destroyed {name}", "success")
    except Exception as exc:
        flash(f"destroy failed: {exc}", "error")

    return redirect(url_for("index"))


@app.route("/sweep", methods=["POST"])
def sweep():
    try:
        config = provisioner.load_config()
        client = provisioner.get_client(config)
        store = InstanceStore()
        expired = store.list_expired()
        for record in expired:
            provisioner.destroy_instance(client, node=record["node"], vmid=record["vmid"])
            store.mark_destroyed(record["vmid"])
        flash(f"swept {len(expired)} instance(s)", "success")
    except Exception as exc:
        flash(f"sweep failed: {exc}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    InstanceStore().init_db()
    app.run(debug=True)
