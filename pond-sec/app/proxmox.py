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
                     *, instance_id: int, template_id: int) -> Clone:
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    return _core.clone_and_start(
        _client(), template_vmid, node, label=label,
        full_clone=cfg["PROXMOX_FULL_CLONE"], storage=cfg["PROXMOX_STORAGE"],
        instance_id=instance_id, template_id=template_id,
    )


def stop_and_destroy(vmid: int, node: str = None):
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    _core.stop_and_destroy(_client(), vmid, node)


def get_console_ticket(vmid: int, node: str = None) -> ConsoleTicket:
    node = node or current_app.config["PROXMOX_NODE"]
    try:
        return _core.web_console_ticket(_client(), node, vmid)
    except Exception as exc:
        raise ProxmoxError(f"Proxmox refused the console ticket: {exc}") from exc   