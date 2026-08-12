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
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import quote

import websocket as ws_client
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sock import Sock

import provisioner
from cli import load_challenge, next_free_vmid
from db.store import InstanceStore
from provisioner import get_console_ticket
from db.orm import db

PROJECT_ROOT = Path(__file__).parent
CHALLENGES_DIR = PROJECT_ROOT / "vars" / "challenges"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-for-production")
sock = Sock(app)
app.config["SQLALCHEMY_BINDS"] = {
	"pond": "sqlite:///the_pond.db"
}

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


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
        node = config["proxmox_node"]

        vmid = int(vmid) if vmid else next_free_vmid(
            client, node, store, challenge["vmid_range_start"], challenge["vmid_range_end"]
        )
        full_name = f"{challenge_name}-{name}"
        ttl_hours = int(ttl_hours) if ttl_hours else challenge["default_ttl_hours"]

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


@app.route("/console/<session_id>")
def console_page(session_id):
    return render_template("console.html", session_id=session_id)


@sock.route("/console/<session_id>/ws")
def console_relay(ws, session_id):
    """Bidirectional pump between the browser's noVNC socket and Proxmox's
    vncwebsocket. `client` is the module-global proxmoxer client set up in
    the __main__ block below - reused here rather than reinitialized."""
    store = InstanceStore()
    instance = store.get_instance_by_session(session_id)
    if instance is None:
        ws.close(reason=1008, message="no such session")
        return

    node, vmid = instance["node"], instance["vmid"]
    ticket = get_console_ticket(client, node, vmid)

    config = provisioner.load_config()
    token_secret = os.environ.get(provisioner.TOKEN_SECRET_ENV)
    auth_header = (
        f"Authorization: PVEAPIToken="
        f"{config['proxmox_api_user']}!{config['proxmox_api_token_id']}={token_secret}"
    )
    upstream_url = (
        f"wss://{config['proxmox_api_host']}:8006/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket"
        f"?port={ticket['port']}&vncticket={quote(ticket['ticket'], safe='')}"
    )
    # ponytail: verify_ssl=False to match provisioner.get_client's self-signed
    # cert handling; swap for real cert validation once pve has a trusted cert.
    upstream = ws_client.create_connection(
        upstream_url, header=[auth_header], sslopt={"cert_reqs": ssl.CERT_NONE}
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
            ws.close()

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


if __name__ == "__main__":
    InstanceStore().init_db()

    config = provisioner.load_config()
    client = provisioner.get_client(config)
    node = config["proxmox_node"]

    print("--- LXC containers on pve ---")
    for ct in client.nodes(node).lxc.get():
        print(" ", ct["vmid"], ct.get("name"))

    print("--- QEMU VMs on pve ---")
    for vm in client.nodes(node).qemu.get():
        print(" ", vm["vmid"], vm.get("name"))

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
