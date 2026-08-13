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


@dataclass
class ConsoleTicket:
    """A one-time VNC ticket for a single console session.

    Never render .ticket into a template or log it - it is a short-lived
    credential, same handling rule as console_url on Clone."""
    ticket: str
    port: str


def get_console_ticket(vmid: int, node: str = None) -> ConsoleTicket:
    """One-time VNC ticket + port for a session's console, via the same
    proxmoxer connection every other real-API call in this file uses."""
    node = node or current_app.config["PROXMOX_NODE"]
    proxmox = _connect()
    try:
        result = proxmox.nodes(node).qemu(vmid).vncproxy.post(websocket=1)
    except Exception as exc:
        raise ProxmoxError(f"Proxmox refused the console ticket: {exc}") from exc
    return ConsoleTicket(ticket=result["ticket"], port=result["port"])


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


def instance_exists(vmid: int, node: str) -> bool:
    """Check a single VM directly via its status endpoint, rather than
    fetching the whole node's VM list - one request instead of every VM's
    config. Used to make teardown idempotent: a VM already gone on Proxmox
    (closed by hand, or a retried teardown) is not an error."""
    from proxmoxer.core import ResourceException

    proxmox = _connect()
    try:
        proxmox.nodes(node).qemu(vmid).status.current.get()
        return True
    except ResourceException as exc:
        if exc.status_code == 500 and "does not exist" in (exc.content or ""):
            return False
        raise


def _api_destroy(vmid: int, node: str):
    """Stop-then-delete, made idempotent and race-safe.

    Two failure modes this guards against, both seen in practice:
      - calling delete() right after stop() without waiting for the stop
        task to finish - Proxmox rejects the delete while the VM is still
        mid-shutdown, since stop is asynchronous (Tasks.blocking_status
        waits for the real completion rather than guessing a sleep).
      - the VM already being gone (closed by hand via the GUI, or a retried
        teardown after a previous partial failure) - Proxmox returns a 500
        with a missing-config message rather than a 404, and that is treated
        as already-torn-down rather than an error.
    """
    from proxmoxer.tools import Tasks

    if not instance_exists(vmid, node):
        return
    proxmox = _connect()
    try:
        task = proxmox.nodes(node).qemu(vmid).status.stop.post()
        Tasks.blocking_status(proxmox, task)
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