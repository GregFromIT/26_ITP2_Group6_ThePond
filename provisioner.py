"""
provisioner.py

Direct Proxmox REST calls via proxmoxer. The web app uses this module through
pond-sec/app/proxmox.py, which adds the token, privilege and protected-VM
checks (H3) in front of every call; the legacy CLI tools call it directly.

Replaces the ansible-playbook shell-outs in playbooks/create_instance.yml and
playbooks/destroy_instance.yml. Same clone+start / stop+delete lifecycle.

Connection settings: the web app passes its Flask config (PROXMOX_HOST,
PROXMOX_TOKEN_ID, PROXMOX_TOKEN_SECRET, PROXMOX_VERIFY_SSL, PROXMOX_CA_BUNDLE -
see pond-sec/app/config.py). Legacy callers pass group_vars/all.yml instead,
with the token secret in the THEPOND_PROXMOX_TOKEN_SECRET env var and TLS
settings in THEPOND_PROXMOX_CA_BUNDLE / THEPOND_PROXMOX_VERIFY_SSL.
"""

import os
import re
import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import paramiko
import yaml
from proxmoxer import ProxmoxAPI
from proxmoxer.tools import Tasks
from proxmoxer.core import ResourceException
from sqlalchemy.exc import IntegrityError

from db.database_app import app as _db_app
from db.orm import db
from db.runtime_models import VMInstance

PROJECT_ROOT = Path(__file__).parent
GROUP_VARS_PATH = PROJECT_ROOT / "group_vars" / "all.yml"
TOKEN_SECRET_ENV = "THEPOND_PROXMOX_TOKEN_SECRET"
SSH_KEY_ENV = "THEPOND_PROXMOX_SSH_KEY"  # mirrors TOKEN_SECRET_ENV's pattern - path to a private key, not committed
# This range overlaps VMs 300-303, which belong to another project on the same
# cluster. It is kept for the legacy callers and guarded instead: PROTECTED_VMIDS
# is never allocated, cloned over, stopped or deleted (H3).
DEFAULT_VMID_RANGE = (301, 399)
PROTECTED_VMIDS = frozenset(range(300, 304))

class ProxmoxError(RuntimeError):
    pass


def _refuse_protected(vmid: int) -> None:
    if int(vmid) in PROTECTED_VMIDS:
        raise ProxmoxError(f"vmid {vmid} belongs to another project and is protected; refusing.")

@dataclass
class Clone:
    vmid: int
    node: str
    console_url: str
    status: str

@dataclass
class ConsoleTicket:
    ticket: str
    port: str

@contextmanager
def _db_context():
    from flask import has_app_context

    if has_app_context():
        yield
    else:
        with _db_app.app_context():
            yield



def load_config() -> dict:
    with open(GROUP_VARS_PATH) as f:
        return yaml.safe_load(f)


def get_client(config: dict) -> ProxmoxAPI:
    # The web app's Flask config carries the secret and TLS settings itself
    # (pond-sec/app/config.py), so the adapter's checks and this connection read
    # the same values. Legacy callers pass group_vars and use the env vars.
    token_secret = config.get("PROXMOX_TOKEN_SECRET") or os.environ.get(TOKEN_SECRET_ENV)
    if not token_secret:
        raise RuntimeError(
            f"Set {TOKEN_SECRET_ENV} to the Proxmox API token secret "
            "(see group_vars/vault.yml.example for where this used to live)."
        )
    # H3: TLS is verified (a CA bundle path may be given), and a @pam token -
    # a Linux account on the hypervisor, root@pam bypassing every permission
    # check - is refused outright.
    if "PROXMOX_VERIFY_SSL" in config:
        verify = config.get("PROXMOX_CA_BUNDLE") or bool(config["PROXMOX_VERIFY_SSL"])
    else:
        verify = os.environ.get("THEPOND_PROXMOX_CA_BUNDLE") or (
            os.environ.get("THEPOND_PROXMOX_VERIFY_SSL", "1") == "1")
    if "proxmox_api_host" in config:
        host = config["proxmox_api_host"]
        user = config["proxmox_api_user"]
        token_name = config["proxmox_api_token_id"]
    else:
        host = config["PROXMOX_HOST"]
        user, _, token_name = config["PROXMOX_TOKEN_ID"].partition("!")
    user = user.strip()
    realm = user.rpartition("@")[2].strip().lower() if "@" in user else ""
    if not realm or realm == "pam":
        # A realm-less user is refused too: Proxmox would read it as @pam.
        raise RuntimeError("Refusing a @pam API token (H3); use a scoped @pve token.")
    return ProxmoxAPI(
        host,
        user=user,
        token_name=token_name,
        token_value=token_secret,
        verify_ssl=verify,
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
    _refuse_protected(vmid)
    template_vmid = _resolve_template_vmid(client, node, vm_template)
    _refuse_protected(template_vmid)
    task = client.nodes(node).qemu(template_vmid).clone.post(
        newid=vmid, name=vm_name, full=1, storage=storage
    )
    Tasks.blocking_status(client, task, timeout=timeout)
    client.nodes(node).qemu(vmid).status.start.post()

def next_free_vmid(client: ProxmoxAPI, node: str, start: int = DEFAULT_VMID_RANGE[0],
                    end: int = DEFAULT_VMID_RANGE[1], extra_used: frozenset = frozenset()) -> int:
    proxmox_used = {vm["vmid"] for vm in client.nodes(node).qemu.get()}
    proxmox_used |= {ct["vmid"] for ct in client.nodes(node).lxc.get()}

    with _db_context():
        ledger_used = {
            row.proxmox_vmid
            for row in db.session.query(VMInstance.proxmox_vmid)
            .filter(VMInstance.deleted_at.is_(None))
            .all()
        }

    used = proxmox_used | ledger_used | set(extra_used) | PROTECTED_VMIDS
    for vmid in range(start, end + 1):
        if vmid not in used:
            return vmid
    raise RuntimeError(f"no free VMIDs in range {start}-{end}")

def claim_vmid(
    client: ProxmoxAPI,
    node: str,
    instance_id: int,
    template_id: int,
    start: int = DEFAULT_VMID_RANGE[0],
    end: int = DEFAULT_VMID_RANGE[1],
    *, challenge_template_id: int | None = None,
) -> VMInstance:
    """Atomically reserve a free vmid by inserting a real VMInstance row for
    it (status='reserving'), inside the same transaction that proves the
    reservation. next_free_vmid()'s check-then-act pattern has a gap
    between "ask what's free" and "write down that I'm taking it" - the
    partial unique index on (proxmox_vmid WHERE deleted_at IS NULL) is what
    actually closes that gap; this function's job is only to retry the next
    candidate when a collision happens, not to prevent the race itself.

    instance_id/template_id are required up front, not filled in after the
    fact, because VMInstance.instance_id/.template_id are NOT NULL foreign
    keys - there is no valid placeholder-row state with them left unset.

    With a least-privilege token, qemu.get()/lxc.get() only list the VMs the
    token can see (VM.Audit). A VM outside its pools in this range is
    invisible here, so its vmid can be chosen and the clone then fails with
    "already exists". That fails safe, but keep other projects' VMs out of
    this range or in PROTECTED_VMIDS.
    """
    proxmox_used = {vm["vmid"] for vm in client.nodes(node).qemu.get()}
    proxmox_used |= {ct["vmid"] for ct in client.nodes(node).lxc.get()}

    with _db_context():
        ledger_used = {
            row.proxmox_vmid
            for row in db.session.query(VMInstance.proxmox_vmid)
            .filter(VMInstance.deleted_at.is_(None)).all()
        }
        used_hint = proxmox_used | ledger_used | PROTECTED_VMIDS

        for candidate in range(start, end + 1):
            if candidate in used_hint:
                continue
            row = VMInstance(
                instance_id=instance_id,
                template_id=template_id,
                challenge_template_id=challenge_template_id,
                proxmox_vmid=candidate,
                proxmox_node=node,
                status="reserving",
            )
            db.session.add(row)
            try:
                db.session.commit()
                return row
            except IntegrityError:
                db.session.rollback()
                continue
    raise RuntimeError(f"no free VMIDs in range {start}-{end}")

VNET_ZONE = "pondz"  # created once, by hand - see Phase 0. Not managed by this code.
VNET_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")  # SDN's own 8-char, lowercase-start rule


def _session_vnet_name(instance_id: int) -> str:
    """Deterministic, <=8-char VNet ID for one ChallengeInstance. Proxmox
    SDN caps VNet IDs at 8 characters because the ID becomes a literal
    Linux bridge interface name, with suffixes appended for VLAN/VXLAN
    sub-devices, and has to stay inside the kernel's IFNAMSIZ limit -
    confirmed on the Proxmox forum, not a GUI-only restriction."""
    name = "s" + format(instance_id, "x")
    if not VNET_ID_RE.match(name):
        raise ValueError(f"generated vnet id {name!r} violates SDN's 8-char rule")
    return name


def create_session_vnet(client: ProxmoxAPI, instance_id: int) -> str:
    """One isolated Simple-zone VNet per ChallengeInstance (session), not
    per VM - every VM cloned into this session shares this same VNet, so
    they can reach each other, while no VM outside this session's VNet has
    any path in. No VLAN tag or Subnet needed: Simple-zone isolation comes
    from the bridge itself, not from addressing - every session's clones
    can safely reuse the same static IP because each session is its own
    broadcast domain."""
    vnet = _session_vnet_name(instance_id)
    client.cluster.sdn.vnets.post(vnet=vnet, zone=VNET_ZONE)
    client.cluster.sdn.put()  # equivalent to `pvesh set /cluster/sdn` - applies + reloads
    return vnet


def destroy_session_vnet(client: ProxmoxAPI, vnet: str) -> None:
    client.cluster.sdn.vnets(vnet).delete()
    client.cluster.sdn.put()


def enable_vm_firewall(client: ProxmoxAPI, node: str, vmid: int) -> None:
    """Deny-by-default inbound posture for one VM. Must be paired with
    firewall=1 on that VM's net0 line (done in clone_and_start's net0
    rewrite below) - enabling the VM-level firewall alone does nothing if
    the NIC itself doesn't have filtering turned on; Proxmox raises no
    error for that mismatch; it just silently doesn't enforce."""
    _refuse_protected(vmid)
    client.nodes(node).qemu(vmid).firewall.options.put(enable=1, policy_in="DROP")


def apply_network_rule(
    client: ProxmoxAPI, node: str, dest_vmid: int,
    source_ip: str, port: int, proto: str = "tcp",
) -> None:
    """One inbound allow rule on the DESTINATION vm - the resource being
    protected owns the rule that lets someone in, not the source."""
    _refuse_protected(dest_vmid)
    client.nodes(node).qemu(dest_vmid).firewall.rules.post(
        type="in", action="ACCEPT", source=source_ip, dport=port, proto=proto,
    )

def get_console_ticket(client: ProxmoxAPI, node: str, vmid: int) -> dict:
    """One-time VNC ticket + port for the noVNC console proxy."""
    _refuse_protected(vmid)
    return client.nodes(node).qemu(vmid).vncproxy.post(websocket=1)

def web_console_ticket(client: ProxmoxAPI, node: str, vmid: int) -> ConsoleTicket:
    result = get_console_ticket(client, node, vmid)
    return ConsoleTicket(ticket=result["ticket"], port=result["port"])

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
    _refuse_protected(vmid)
    if not instance_exists(client, node, vmid):
        return
    task = client.nodes(node).qemu(vmid).status.stop.post()
    Tasks.blocking_status(client, task, timeout=timeout)
    client.nodes(node).qemu(vmid).delete()


def check_flag(submitted: str, expected: str) -> bool:
    return submitted.strip() == expected.strip()

def _get_console_url(node: str, vmid: int, host: str | None = None) -> str:
    host = host or (yaml.safe_load(open(GROUP_VARS_PATH)).get("proxmox_api_host") if GROUP_VARS_PATH.exists() else "pve")
    return f"https://{host}:8006/?console=kvm&novnc=1&vmid={vmid}&node={node}"


# --------------------------------------------------------------- networking

DISK_KEY_RE = re.compile(r"^(virtio|scsi|sata|ide)\d+$")


def _find_disk_volid(client: ProxmoxAPI, node: str, vmid: int) -> str:
    """Scans the VM's config for whichever disk-slot key it actually has
    (sata0 confirmed on the DC-1 template; could be scsi0/virtio0 for
    others) rather than assuming a fixed slot name - works for linked or
    full clones since the key mirrors whatever the source template used."""
    _refuse_protected(vmid)   # the result feeds a disk write
    config = client.nodes(node).qemu(vmid).config.get()
    for key, value in config.items():
        if DISK_KEY_RE.match(key):
            return value.split(":", 1)[1].split(",")[0]  # storage:volid,size=... -> volid
    raise RuntimeError(f"no disk found in VM {vmid}'s config")


def _ssh_client(host: str) -> paramiko.SSHClient:
    key_path = os.environ.get(SSH_KEY_ENV)
    if not key_path:
        raise RuntimeError(f"Set {SSH_KEY_ENV} to an SSH private key path for the Proxmox host")
    # H3: the account is named explicitly (no default), root is refused unless
    # knowingly allowed, and the host key must already be known - an unknown
    # host is rejected rather than trusted on first sight.
    user = os.environ.get("THEPOND_PROXMOX_SSH_USER")
    if not user:
        raise RuntimeError("Set THEPOND_PROXMOX_SSH_USER to the restricted account used to reach the Proxmox host")
    if user == "root" and os.environ.get("THEPOND_PROXMOX_ALLOW_ROOT_SSH") != "1":
        raise RuntimeError(
            "Root SSH to the hypervisor is refused (H3). Use a restricted account with a "
            "forced-command wrapper, or set THEPOND_PROXMOX_ALLOW_ROOT_SSH=1 knowingly.")
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    known_hosts = os.environ.get("THEPOND_PROXMOX_KNOWN_HOSTS")
    if known_hosts:
        ssh.load_host_keys(known_hosts)
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(host, username=user, key_filename=key_path)
    return ssh


def inject_instance_network(
    client: ProxmoxAPI, node: str, vmid: int, static_ip: str,
    proxmox_host: str, netmask_bits: int = 24, vg_name: str = "pve",
    gateway: str | None = None,
) -> None:
    """
    Writes a static /etc/network/interfaces into a freshly-cloned VM's disk
    while it's still offline, via libguestfs's virt-customize run remotely
    over SSH (this codebase talks to Proxmox purely over the REST API -
    group_vars/all.yml points at 10.1.21.151 - so there is no local
    filesystem access to the LV from wherever this code runs; SSH is the
    only path to a tool that needs to touch the block device directly).

    Call this from clone_and_start() AFTER Tasks.blocking_status() confirms
    the clone's disk exists, and BEFORE status.start.post() - virt-customize
    requires the target to be shut down (libguestfs.org/virt-customize.1.html),
    which is guaranteed at this point since the clone hasn't been started yet.

    Runs once per CLONE rather than once per template: every session's
    clones stay safe reusing the same static_ip regardless of which
    session, per create_session_vnet()'s isolation (each session is its
    own broadcast domain) - this just means a template nobody remembered
    to pre-bake still works, at the cost of one SSH round-trip per launch.

    gateway: pass this when the clone is NOT going onto an isolated
    session VNet (i.e. launch()'s single-VM path, which stays on the
    template's own flat/shared bridge per themes.py's `len(templates) > 1`
    check) - without a route out, the guest can answer ARP (pure L2, no
    routing involved) but any reply addressed back to a host on a
    different segment silently never leaves. Confirmed exactly this
    failure mode testing vmid 308 on 2026-09-16: ARP replied correctly,
    ping got 100% loss, fixed immediately by adding this line - not a
    virt-customize/SSH problem, a missing-route problem. Omit this when
    vnet IS set (create_session_vnet()'s isolated segments have no router
    at all - see that function's own docstring).
    """
    _refuse_protected(vmid)   # this writes into the VM's disk over SSH
    volid = _find_disk_volid(client, node, vmid)
    disk_path = f"/dev/{vg_name}/{volid}"  # confirmed vg_name="pve" for every disk on this host via `lvs -o vg_name,lv_name`

    gateway_line = f"    gateway {gateway}\n" if gateway else ""
    interfaces_content = (
        "# The loopback network interface\n"
        "auto lo\n"
        "iface lo inet loopback\n\n"
        "# The primary network interface\n"
        "allow-hotplug eth0\n"
        "iface eth0 inet static\n"
        f"    address {static_ip}/{netmask_bits}\n"
        f"{gateway_line}"
    )
    remote_tmp = f"/tmp/interfaces-{vmid}"

    ssh = _ssh_client(proxmox_host)
    try:
        sftp = ssh.open_sftp()
        with sftp.file(remote_tmp, "w") as f:
            f.write(interfaces_content)
        sftp.close()

        cmd = f"virt-customize -a {shlex.quote(disk_path)} --upload {shlex.quote(remote_tmp)}:/etc/network/interfaces"
        _, stdout, stderr = ssh.exec_command(cmd)
        if stdout.channel.recv_exit_status() != 0:
            raise ProxmoxError(f"virt-customize failed for vmid {vmid}: {stderr.read().decode()}")
    finally:
        ssh.exec_command(f"rm -f {shlex.quote(remote_tmp)}")
        ssh.close()


def inject_firstboot_command(
    client: ProxmoxAPI, node: str, vmid: int, command: str,
    proxmox_host: str, vg_name: str = "pve",
) -> None:
    """
    Queues a shell command that the clone runs once, as root, on its first
    boot (virt-customize --firstboot-command, libguestfs.org/virt-customize.1.html
    "FIRSTBOOT SCRIPTS"). Same SSH + offline-disk path as
    inject_instance_network(), so it needs no extra API privilege.

    Used to pull a challenge's handout files onto the entry-point VM before
    the student opens the console. Tested by hand on a linked clone of
    template 10001 on 2026-10-02.
    """
    _refuse_protected(vmid)   # this writes into the VM's disk over SSH
    disk_path = f"/dev/{vg_name}/{_find_disk_volid(client, node, vmid)}"

    ssh = _ssh_client(proxmox_host)
    try:
        cmd = f"virt-customize -a {shlex.quote(disk_path)} --firstboot-command {shlex.quote(command)}"
        _, stdout, stderr = ssh.exec_command(cmd)
        if stdout.channel.recv_exit_status() != 0:
            raise ProxmoxError(f"virt-customize failed for vmid {vmid}: {stderr.read().decode()}")
    finally:
        ssh.close()


# ------------------------------------------------------------------ clone

def clone_and_start(
    client: ProxmoxAPI,
    template_vmid: int,
    node: str,
    label: str = "challenge",
    full_clone: bool = False,
    storage: str | None = None,
    vmid_range: tuple[int, int] = DEFAULT_VMID_RANGE,
    *,
    instance_id: int,
    template_id: int,
    vnet: str | None = None,
    static_ip: str | None = None,
    proxmox_host: str | None = None,
    gateway: str | None = None,
    pool: str | None = None,
    challenge_template_id: int | None = None,
    firstboot_command: str | None = None,
) -> Clone:
    """
    static_ip/proxmox_host: pass both together to bake a static IP into
    this clone's disk before boot (see inject_instance_network()). Sourced
    at the call site from the launching challenge's ChallengeTemplate.static_ip -
    that field already exists in db/VMs_models.py and is already read
    elsewhere (themes.py's firewall-rule logic), confirming one fixed IP
    per template is the intended design, not per-clone allocation.

    gateway: only meaningful when vnet is None (single-VM challenges,
    which stay on the template's own flat/shared bridge - see
    themes.py's `len(templates) > 1` check). When vnet IS set, the clone
    lands on an isolated session VNet with no router at all
    (create_session_vnet()'s own docstring), so a gateway is never
    written regardless of what's passed here - forced below rather than
    left to the caller to get right, since passing one there would write
    a gateway line pointing at nothing.

    pool: the Proxmox pool to create the clone in. The web app's
    least-privilege token (playbooks/pond_least_privilege.yml) may only
    create VMs in its own pool, so without this every clone is a 403.
    """
    if (static_ip is not None or firstboot_command is not None) and proxmox_host is None:
        raise ValueError("proxmox_host is required when static_ip or firstboot_command is set - both inject over SSH")

    _refuse_protected(template_vmid)
    reserved = claim_vmid(client, node, instance_id, template_id, *vmid_range, challenge_template_id=challenge_template_id)
    vmid = reserved.proxmox_vmid
    options = {"newid": vmid, "name": label[:63], "full": 1 if full_clone else 0, "target": node}
    if full_clone:
        options["storage"] = storage
    if pool:
        options["pool"] = pool
    try:
        _refuse_protected(vmid)   # claim_vmid never returns one; belt and braces
        task = client.nodes(node).qemu(template_vmid).clone.post(**options)
        Tasks.blocking_status(client, task)

        if static_ip is not None:
            effective_gateway = gateway if vnet is None else None
            inject_instance_network(client, node, vmid, static_ip, proxmox_host, gateway=effective_gateway)

        if firstboot_command is not None:
            inject_firstboot_command(client, node, vmid, firstboot_command, proxmox_host)

        if vnet is not None:
            current_net0 = client.nodes(node).qemu(vmid).config.get()["net0"]
            new_net0 = re.sub(r"bridge=[^,]+", f"bridge={vnet}", current_net0)
            new_net0 = re.sub(r",tag=\d+", "", new_net0)
            if "firewall=1" not in new_net0:
                new_net0 += ",firewall=1"
            client.nodes(node).qemu(vmid).config.put(net0=new_net0)

        client.nodes(node).qemu(vmid).status.start.post()
    except Exception as exc:
        with _db_context():
            from datetime import datetime, timezone
            row = db.session.get(VMInstance, reserved.vm_instance_id)
            row.status = "error"
            row.deleted_at = datetime.now(timezone.utc)
            db.session.commit()
        raise ProxmoxError(f"Proxmox refused the clone: {exc}") from exc

    clone = Clone(vmid=vmid, node=node, console_url=_get_console_url(node, vmid), status="running")

    with _db_context():
        from datetime import datetime, timezone
        row = db.session.get(VMInstance, reserved.vm_instance_id)
        row.hostname = label
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
        db.session.commit()
    return clone

def stop_and_destroy(client: ProxmoxAPI, vmid: int, node: str, vnet: str | None = None) -> None:
    """vnet: pass the session's vnet name only on the LAST VM being torn
    down for that session - destroying it while sibling VMs in the same
    session are still attached would sever their connectivity too."""
    _refuse_protected(vmid)
    destroy_instance(client, node, vmid)

    with _db_context():
        row = (
            db.session.query(VMInstance)
            .filter(VMInstance.proxmox_vmid == vmid, VMInstance.deleted_at.is_(None))
            .one_or_none()
        )
        if row is not None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            row.stopped_at = now
            row.deleted_at = now
            row.status = "destroyed"
            db.session.commit()

        if vnet is not None:
            destroy_session_vnet(client, vnet)