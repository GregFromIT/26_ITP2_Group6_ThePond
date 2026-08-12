"""
provisioner.py

Direct Proxmox REST calls via proxmoxer - replaces the ansible-playbook
shell-outs in playbooks/create_instance.yml and playbooks/destroy_instance.yml.
Same clone+start / stop+delete lifecycle, called directly from cli.py instead
of shelling out to `ansible-playbook`.

Connection settings (node/host/user/token id) come from group_vars/all.yml,
same values the playbooks used via Jinja vars. The token secret is never
committed - set it via the THEPOND_PROXMOX_TOKEN_SECRET env var (this replaces
what group_vars/vault.yml held for Ansible).
"""

import os
from pathlib import Path

import yaml
from proxmoxer import ProxmoxAPI
from proxmoxer.tools import Tasks
from proxmoxer.core import ResourceException   

PROJECT_ROOT = Path(__file__).parent
GROUP_VARS_PATH = PROJECT_ROOT / "group_vars" / "all.yml"
TOKEN_SECRET_ENV = "THEPOND_PROXMOX_TOKEN_SECRET"


def load_config() -> dict:
    with open(GROUP_VARS_PATH) as f:
        return yaml.safe_load(f)


def get_client(config: dict) -> ProxmoxAPI:
    token_secret = os.environ.get(TOKEN_SECRET_ENV)
    if not token_secret:
        raise RuntimeError(
            f"Set {TOKEN_SECRET_ENV} to the Proxmox API token secret "
            "(see group_vars/vault.yml.example for where this used to live)."
        )
    return ProxmoxAPI(
        config["proxmox_api_host"],
        user=config["proxmox_api_user"],
        token_name=config["proxmox_api_token_id"],
        token_value=token_secret,
        verify_ssl=False,
    )


def _resolve_template_vmid(client: ProxmoxAPI, node: str, template_name: str) -> int:
    """proxmox_kvm's `clone:` param takes a template name; the REST clone
    endpoint needs the template's VMID, so resolve name -> vmid first.

    NOTE: this queries the QEMU VM tree, not LXC. v1Template (LockedShields,
    vmid 1000) is a QEMU VM - confirmed live against pve. If a future
    challenge template is an LXC container instead, this function (and
    create_instance/destroy_instance below) will need a variant that hits
    client.nodes(node).lxc.get() / .lxc(vmid) instead, since Proxmox splits
    VMs and containers into separate API trees with different clone params
    (qemu clone takes name=, lxc clone takes hostname=).
    """
    for vm in client.nodes(node).qemu.get():
        if vm.get("name") == template_name:
            return vm["vmid"]
    raise RuntimeError(f"no template named {template_name!r} found on node {node!r}")


def create_instance(
    client: ProxmoxAPI,
    node: str,
    vm_template: str,
    vm_name: str,
    vmid: int,
    storage: str,
    timeout: int = 120,
) -> None:
    """Clone `vm_template` to `vmid` and start it. Mirrors
    playbooks/create_instance.yml's clone + start tasks."""
    template_vmid = _resolve_template_vmid(client, node, vm_template)
    task = client.nodes(node).qemu(template_vmid).clone.post(
        newid=vmid, name=vm_name, full=1, storage=storage
    )
    Tasks.blocking_status(client, task, timeout=timeout)
    client.nodes(node).qemu(vmid).status.start.post()


def get_console_ticket(client: ProxmoxAPI, node: str, vmid: int) -> dict:
    """One-time VNC ticket + port for the noVNC console proxy."""
    return client.nodes(node).qemu(vmid).vncproxy.post(websocket=1)

def instance_exists(client: ProxmoxAPI, node: str, vmid: int) -> bool:
    """Check a single VM directly via its status endpoint, rather than
    fetching the full VM list for the node and searching it - O(1) request
    instead of O(n)."""
    try:
        client.nodes(node).qemu(vmid).status.current.get()
        return True
    except ResourceException as exc:
        if exc.status_code == 500 and "does not exist" in (exc.content or ""):
            return False
        raise

def destroy_instance(client: ProxmoxAPI, node: str, vmid: int, timeout: int = 60) -> None:
    """Force-stop then delete a VM. Mirrors
    playbooks/destroy_instance.yml's stop (force) + delete tasks.

    If the VM's config is already gone on Proxmox (e.g. it was destroyed
    outside this tool, via the GUI or `qm destroy`), Proxmox returns a 500
    rather than a 404 - this is treated as already-destroyed rather than
    an error, so the caller's DB cleanup still runs instead of the whole
    operation failing."""
    if not instance_exists(client, node, vmid):
        return
    task = client.nodes(node).qemu(vmid).status.stop.post()
    Tasks.blocking_status(client, task, timeout=timeout)
    client.nodes(node).qemu(vmid).delete()


def check_flag(submitted: str, expected: str) -> bool:
    return submitted.strip() == expected.strip()