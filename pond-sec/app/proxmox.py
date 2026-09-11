"""Hypervisor adapter - thin shim over the shared provisioner.

Always talks to a real Proxmox cluster - there is no simulate/fake mode.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import provisioner as _core  # noqa: E402
from flask import current_app  # noqa: E402

Clone = _core.Clone
ConsoleTicket = _core.ConsoleTicket
ProxmoxError = _core.ProxmoxError


def _client():
    return _core.get_client(current_app.config)


def clone_and_start(template_vmid: int, node: str = None, label: str = "challenge",
                     *, instance_id: int, template_id: int, vnet: str | None = None) -> Clone:
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    return _core.clone_and_start(
        _client(), template_vmid, node, label=label,
        full_clone=cfg["PROXMOX_FULL_CLONE"], storage=cfg["PROXMOX_STORAGE"],
        instance_id=instance_id, template_id=template_id, vnet=vnet,
    )


def stop_and_destroy(vmid: int, node: str = None, vnet: str | None = None):
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    _core.stop_and_destroy(_client(), vmid, node, vnet=vnet)


def get_console_ticket(vmid: int, node: str = None) -> ConsoleTicket:
    node = node or current_app.config["PROXMOX_NODE"]
    try:
        return _core.web_console_ticket(_client(), node, vmid)
    except Exception as exc:
        raise ProxmoxError(f"Proxmox refused the console ticket: {exc}") from exc


def create_session_vnet(instance_id: int) -> str:
    """One VNet per ChallengeInstance (session) - call once in launch(),
    before cloning any of that session's VMs, and pass the result as
    vnet= to every clone_and_start() call for that session."""
    return _core.create_session_vnet(_client(), instance_id)


def destroy_session_vnet(vnet: str) -> None:
    """Call only if you need this directly - normally pass vnet= to the
    LAST stop_and_destroy() call for a session instead, which does this
    for you after that VM is torn down."""
    _core.destroy_session_vnet(_client(), vnet)


def enable_vm_firewall(vmid: int, node: str = None) -> None:
    node = node or current_app.config["PROXMOX_NODE"]
    _core.enable_vm_firewall(_client(), node, vmid)


def apply_network_rule(dest_vmid: int, source_ip: str, port: int,
                        node: str = None, proto: str = "tcp") -> None:
    node = node or current_app.config["PROXMOX_NODE"]
    _core.apply_network_rule(_client(), node, dest_vmid, source_ip, port, proto)