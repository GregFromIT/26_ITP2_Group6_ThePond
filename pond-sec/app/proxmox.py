"""Proxmox adapter.

Two backends behind one interface:

  simulate  (default) invents a vmid and a console URL so the whole flow —
            launch, timer, flag submission, teardown — can be demonstrated
            without a cluster.
  api       talks to a real Proxmox VE cluster over the REST API using an API
            token. Requires `proxmoxer` and `requests`.

Nothing else in the codebase imports proxmoxer, so swapping the backend is a
config change rather than a rewrite.
"""

from dataclasses import dataclass

from flask import current_app


# ADDING A BACKEND (a different hypervisor, or a container runtime):
#   1. write _yourbackend_clone() and _yourbackend_destroy()
#   2. add the branch in clone_and_start() and stop_and_destroy() at the bottom
#   3. return a Clone from the create path — the rest of the app only knows
#      about this dataclass, not about any hypervisor
# No view imports proxmoxer, so nothing outside this file needs to change.


@dataclass
class Clone:
    """What the rest of the app gets back from a launch.

    console_url is handed out only through the ownership-checked redirect in
    challenges.console — do not render it into a template.
    """
    vmid: int
    node: str
    console_url: str
    status: str


class ProxmoxError(RuntimeError):
    """Raised when the hypervisor refuses a clone, start or stop."""


# ------------------------------------------------------------------ simulate

def _next_simulated_vmid() -> int:
    from .db import query

    start = current_app.config["PROXMOX_CLONE_POOL_START"]
    row = query("SELECT MAX(proxmox_vmid) AS top FROM active_vm", one=True)
    top = row["top"] if row and row["top"] else start
    return max(top + 1, start + 1)


def _simulate_clone(template_vmid: int, node: str, label: str) -> Clone:
    vmid = _next_simulated_vmid()
    return Clone(
        vmid=vmid,
        node=node,
        console_url=f"https://{current_app.config['PROXMOX_HOST']}:8006/?console=kvm&novnc=1&vmid={vmid}&node={node}",
        status="running",
    )


# ----------------------------------------------------------------- real API

def _connect():
    try:
        from proxmoxer import ProxmoxAPI
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise ProxmoxError(
            "proxmoxer is not installed. Run: pip install proxmoxer requests"
        ) from exc

    cfg = current_app.config
    if not cfg["PROXMOX_TOKEN_ID"] or not cfg["PROXMOX_TOKEN_SECRET"]:
        raise ProxmoxError("PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET are not set.")

    user, _, token_name = cfg["PROXMOX_TOKEN_ID"].partition("!")
    return ProxmoxAPI(
        cfg["PROXMOX_HOST"],
        user=user,
        token_name=token_name,
        token_value=cfg["PROXMOX_TOKEN_SECRET"],
        verify_ssl=cfg["PROXMOX_VERIFY_SSL"],
    )


def _api_clone(template_vmid: int, node: str, label: str) -> Clone:
    """Real clone against Proxmox VE.

    Linked clone by default (PROXMOX_FULL_CLONE=0): near-instant and small,
    because it shares the template's disk. That is right for short teaching
    sessions, and it means the storage setting is not consulted — the clone
    inherits the template's. Set PROXMOX_FULL_CLONE=1 if a challenge must
    survive the template changing underneath it, and the disk then lands on
    PROXMOX_STORAGE (local-lvm on this cluster).

    The API token needs VM.Clone, VM.Config.*, VM.PowerMgmt, VM.Audit and
    VM.Allocate on the template pool — and nothing else.
    """
    cfg = current_app.config
    proxmox = _connect()
    try:
        new_vmid = int(proxmox.cluster.nextid.get())
        options = {
            "newid": new_vmid,
            "name": label[:63],
            "full": 1 if cfg["PROXMOX_FULL_CLONE"] else 0,
            "target": node,
        }
        if cfg["PROXMOX_FULL_CLONE"]:
            # Proxmox rejects `storage` on a linked clone, so it is only sent
            # when the clone is full.
            options["storage"] = cfg["PROXMOX_STORAGE"]
        proxmox.nodes(node).qemu(template_vmid).clone.post(**options)
        proxmox.nodes(node).qemu(new_vmid).status.start.post()
    except Exception as exc:  # proxmoxer raises library-specific errors
        raise ProxmoxError(f"Proxmox refused the clone: {exc}") from exc

    return Clone(
        vmid=new_vmid,
        node=node,
        console_url=f"https://{current_app.config['PROXMOX_HOST']}:8006/?console=kvm&novnc=1&vmid={new_vmid}&node={node}",
        status="running",
    )


def _api_destroy(vmid: int, node: str):
    proxmox = _connect()
    try:
        proxmox.nodes(node).qemu(vmid).status.stop.post()
        proxmox.nodes(node).qemu(vmid).delete()
    except Exception as exc:
        raise ProxmoxError(f"Proxmox refused the teardown: {exc}") from exc


# -------------------------------------------------------------- public face

def clone_and_start(template_vmid: int, node: str = None, label: str = "challenge") -> Clone:
    """Create and start a machine for one session. Raises ProxmoxError.

    Callers must catch ProxmoxError and tell the student the challenge could not
    start — a hypervisor at capacity is a normal Tuesday, not an exception.
    """
    node = node or current_app.config["PROXMOX_NODE"]
    if current_app.config["PROXMOX_BACKEND"] == "api":
        return _api_clone(template_vmid, node, label)
    return _simulate_clone(template_vmid, node, label)


def stop_and_destroy(vmid: int, node: str = None):
    """Tear a clone down at the end of a session.

    challenges._close() deliberately records the session BEFORE calling this, so
    a hypervisor that will not release a VM cannot lose a student's result. If
    you change the ordering, keep that property.
    """
    node = node or current_app.config["PROXMOX_NODE"]
    if current_app.config["PROXMOX_BACKEND"] == "api":
        _api_destroy(vmid, node)
    # Simulated clones need no teardown; the active_vm row records the stop.
