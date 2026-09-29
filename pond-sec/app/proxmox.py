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

import os
import re
import time
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

# ------------------------------------------------ credentials and trust
#
# H3 in docs/SECURITY_ASSESSMENT.md: the adapter used to default to a
# root@pam API token and to skip TLS verification. A root token can do
# anything to every VM on the cluster, including the VMs of other projects, and
# without verification anyone on the path can capture it. So the adapter now
# decides what it is willing to hold, and refuses rather than degrades:
#
#   * the token must be a named @pve token; @pam users are Linux accounts on
#     the hypervisor and root@pam bypasses every permission check;
#   * the connection is verified against the system CA store or
#     PROXMOX_CA_BUNDLE (off only by explicit opt-out, never in production);
#   * every clone goes in one pool, and other projects' VMs are never touched;
#   * before a launch the token's own permissions are read back and compared
#     with an allowlist, so a token that was widened later is caught even
#     though it is a perfectly valid token.
#
# None of this needs the network except _check_token_privileges, so the rules
# are pure functions that tests/test_credentials.py can drive directly.

_TOKEN_ID_RE = re.compile(r"^(?P<user>[^\s@!:]+)@(?P<realm>[A-Za-z][A-Za-z0-9._-]*)!(?P<name>[A-Za-z][A-Za-z0-9._-]*)$")

MSG_TOKEN_MISSING = ("PROXMOX_TOKEN_ID is not set. Create The Pond's own token (docs/README.md, "
                     "'Least-privilege Proxmox token') and set PROXMOX_TOKEN_ID=pond@pve!launcher.")
MSG_TOKEN_MALFORMED = "PROXMOX_TOKEN_ID must look like user@realm!tokenname, for example pond@pve!launcher."
MSG_TOKEN_PAM = ("PROXMOX_TOKEN_ID belongs to a @pam account. Refusing to use it: @pam users are Linux "
                 "accounts on the hypervisor and root@pam bypasses every permission check. Use The Pond's "
                 "own privilege-separated @pve token.")
MSG_SECRET_MISSING = "PROXMOX_TOKEN_SECRET is not set."
MSG_POOL_MISSING = "PROXMOX_POOL is not set. Clones must be created in the pool the API token is scoped to."
MSG_CA_MISSING = ("PROXMOX_CA_BUNDLE is set to {path!r}, but no such file exists. Copy "
                  "/etc/pve/pve-root-ca.pem from the hypervisor to that path.")
MSG_TLS_OFF_PROD = ("PROXMOX_VERIFY_SSL=0 is not allowed in production. Set PROXMOX_CA_BUNDLE to a copy "
                    "of the cluster CA (/etc/pve/pve-root-ca.pem) instead.")
MSG_PROTECTED_BAD = "PROXMOX_PROTECTED_VMIDS must be a comma-separated list of vmids or ranges, e.g. 300-303."
MSG_PROTECTED_VM = ("vmid {vmid} is on the protected list (PROXMOX_PROTECTED_VMIDS); The Pond will not "
                    "clone, start or delete it.")
MSG_OVERPRIVILEGED = ("The platform's Proxmox token is over-privileged, so launches are disabled until an "
                      "administrator reduces it.")
MSG_PERMS_UNREADABLE = "Could not read the Proxmox token's permissions, so the launch was refused."


def parse_token_id(token_id) -> tuple:
    """Split user@realm!tokenname into ("user@realm", "tokenname"), or refuse."""
    if not token_id:
        raise ProxmoxError(MSG_TOKEN_MISSING)
    match = _TOKEN_ID_RE.fullmatch(token_id)   # fullmatch: a trailing newline is not a token
    if not match:
        raise ProxmoxError(MSG_TOKEN_MALFORMED)
    if match.group("realm").lower() == "pam":
        raise ProxmoxError(MSG_TOKEN_PAM)
    return f"{match.group('user')}@{match.group('realm')}", match.group("name")


def protected_vmids(spec) -> frozenset:
    """Parse "300-303,410" into {300, 301, 302, 303, 410}. Empty means none."""
    vmids = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                low, _, high = part.partition("-")
                low, high = int(low), int(high)
                if low > high:
                    raise ValueError(part)
                vmids.update(range(low, high + 1))
            else:
                vmids.add(int(part))
        except ValueError as exc:
            raise ProxmoxError(MSG_PROTECTED_BAD) from exc
    return frozenset(vmids)


def settings_problems(cfg) -> list:
    """Everything wrong with the Proxmox settings, in the order to fix it.

    Pure: reads cfg and the filesystem, opens no connection. Used both by
    _connect() (refuse to connect) and by create_app() (refuse to start in
    production), so the two can never disagree."""
    problems = []
    try:
        parse_token_id(cfg.get("PROXMOX_TOKEN_ID"))
    except ProxmoxError as exc:
        problems.append(str(exc))
    if not cfg.get("PROXMOX_TOKEN_SECRET"):
        problems.append(MSG_SECRET_MISSING)
    if not cfg.get("PROXMOX_POOL"):
        problems.append(MSG_POOL_MISSING)
    ca = cfg.get("PROXMOX_CA_BUNDLE")
    if ca and not os.path.isfile(ca):
        problems.append(MSG_CA_MISSING.format(path=ca))
    if cfg.get("IS_PRODUCTION") and not ca and not cfg.get("PROXMOX_VERIFY_SSL"):
        problems.append(MSG_TLS_OFF_PROD)
    try:
        protected_vmids(cfg.get("PROXMOX_PROTECTED_VMIDS"))
    except ProxmoxError as exc:
        problems.append(str(exc))
    return problems


def _tls_verify(cfg):
    """What to hand proxmoxer as verify_ssl: the CA bundle path if there is
    one (requests accepts a path there), else the on/off flag."""
    return cfg.get("PROXMOX_CA_BUNDLE") or bool(cfg["PROXMOX_VERIFY_SSL"])


def _connect():
    try:
        from proxmoxer import ProxmoxAPI
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise ProxmoxError(
            "proxmoxer is not installed. Run: pip install proxmoxer requests"
        ) from exc

    cfg = current_app.config
    # Refuse before constructing the client, so a bad setting can never result
    # in even one request carrying the token.
    problems = settings_problems(cfg)
    if problems:
        raise ProxmoxError(problems[0])

    user, token_name = parse_token_id(cfg["PROXMOX_TOKEN_ID"])
    verify = _tls_verify(cfg)
    if verify is False:
        current_app.logger.warning(
            "TLS verification to Proxmox at %s is OFF (PROXMOX_VERIFY_SSL=0); anyone on the path "
            "can impersonate the hypervisor and capture the API token.", cfg["PROXMOX_HOST"])
    return ProxmoxAPI(
        cfg["PROXMOX_HOST"],
        user=user,
        token_name=token_name,
        token_value=cfg["PROXMOX_TOKEN_SECRET"],
        verify_ssl=verify,
    )


# What the token may hold, per ACL path. These mirror the roles in
# playbooks/pond_least_privilege.yml; tests/test_credentials.py builds the
# playbook's grants and runs them through excess_privileges() so the two
# cannot drift. A new privilege need means editing both.
#
# VM.Console is deliberately absent: themes.console() only redirects to the
# console URL, so nothing here opens a console. If a relay returns (M6), add
# VM.Console to the playbook's PondClones role and _VM_PRIVS_CLONES together.
_VM_PRIVS_CLONES = frozenset({"VM.Allocate", "VM.Audit", "VM.Config.Network", "VM.PowerMgmt"})
_VM_PRIVS_TEMPLATES = frozenset({"VM.Audit", "VM.Clone"})
_SDN_ZONE_PRIVS = frozenset({"SDN.Allocate", "SDN.Audit", "SDN.Use"})
# Refused wherever they appear, even if a path rule would otherwise allow them.
DANGEROUS_PRIVILEGES = frozenset({
    "Permissions.Modify", "Sys.Modify", "Sys.Console", "Sys.PowerMgmt", "Sys.Incoming",
    "User.Modify", "Realm.Allocate", "Realm.AllocateUser", "Group.Allocate",
    "Pool.Allocate", "Datastore.Allocate", "Mapping.Modify",
})


def _csv(value) -> frozenset:
    return frozenset(x.strip() for x in (value or "").split(",") if x.strip())


def allowed_privileges(path: str, cfg, membership=None) -> frozenset:
    """Privileges the token may hold on one ACL path; anything else is excess.

    membership maps vmid -> pool name (from /cluster/resources). A /vms/<id>
    path is how Proxmox reports pool members expanded, and what a VM may hold
    depends on which pool it is in: clone rights only on a clone, template
    rights only on a template. A VM in neither pool, one whose pool is unknown,
    and every protected VM get nothing. Bridges, storages and the node are
    limited to the ones the playbook grants (PROXMOX_TEMPLATE_BRIDGES,
    PROXMOX_TEMPLATE_STORAGES, PROXMOX_STORAGE, PROXMOX_NODE)."""
    membership = membership or {}
    parts = [p for p in path.split("/") if p]
    if not parts:
        return frozenset({"Sys.Audit"})
    if parts == ["sdn"]:
        return frozenset({"SDN.Allocate"})
    if parts[:3] == ["sdn", "zones", cfg["PROXMOX_SDN_ZONE"]]:
        return _SDN_ZONE_PRIVS
    if len(parts) == 4 and parts[:3] == ["sdn", "zones", "localnetwork"]:
        return frozenset({"SDN.Use"}) if parts[3] in _csv(cfg.get("PROXMOX_TEMPLATE_BRIDGES")) else frozenset()
    if parts == ["pool", cfg["PROXMOX_POOL"]]:
        return _VM_PRIVS_CLONES
    if parts == ["pool", cfg["PROXMOX_TEMPLATE_POOL"]]:
        return _VM_PRIVS_TEMPLATES
    if len(parts) == 2 and parts[0] == "vms" and re.fullmatch(r"[0-9]+", parts[1]):
        vmid = int(parts[1])
        if vmid in protected_vmids(cfg["PROXMOX_PROTECTED_VMIDS"]):
            return frozenset()
        pool = membership.get(vmid)
        if pool is not None and pool == cfg["PROXMOX_POOL"]:
            return _VM_PRIVS_CLONES
        if pool is not None and pool == cfg["PROXMOX_TEMPLATE_POOL"]:
            return _VM_PRIVS_TEMPLATES
        return frozenset()
    if len(parts) == 2 and parts[0] == "storage":
        granted = _csv(cfg.get("PROXMOX_TEMPLATE_STORAGES")) | {cfg.get("PROXMOX_STORAGE")}
        return frozenset({"Datastore.AllocateSpace"}) if parts[1] in granted else frozenset()
    if len(parts) == 2 and parts[0] == "nodes":
        return frozenset({"Sys.Audit"}) if parts[1] == cfg["PROXMOX_NODE"] else frozenset()
    return frozenset()


def excess_privileges(perms, cfg, membership=None) -> list:
    """[(path, [privileges])] the token holds beyond the allowlist.

    perms is the reply of GET /access/permissions: {path: {privilege: propagate}}.
    Only the keys matter. A reply of any other shape is refused, not guessed at."""
    if not isinstance(perms, dict) or any(not isinstance(v, dict) for v in perms.values()):
        raise ProxmoxError(MSG_PERMS_UNREADABLE)
    excess = []
    for path in sorted(perms):
        allowed = allowed_privileges(path, cfg, membership)
        extra = sorted(p for p in perms[path] if p in DANGEROUS_PRIVILEGES or p not in allowed)
        if extra:
            excess.append((path, extra))
    return excess


def pool_membership(resources) -> dict:
    """{vmid: pool} from the reply of GET /cluster/resources?type=vm. A VM with
    no pool maps to "". Any other shape is refused."""
    if not isinstance(resources, list) or any(not isinstance(r, dict) for r in resources):
        raise ProxmoxError(MSG_PERMS_UNREADABLE)
    try:
        return {int(r["vmid"]): r.get("pool") or "" for r in resources}
    except (KeyError, TypeError, ValueError) as exc:
        raise ProxmoxError(MSG_PERMS_UNREADABLE) from exc


# Passed checks only, keyed by (host, token id) -> time.monotonic(). A failure
# or an unreadable reply is never cached: the next launch must look again.
_privilege_cache: dict = {}


def _check_token_privileges(proxmox):
    """Refuse to launch unless the token holds only what The Pond needs.

    Runs first in _api_clone, before anything is cloned or networked. The check
    trusts the hypervisor's own report, which is acceptable because the channel
    is verified. Students get a generic error; the offending paths go to the log."""
    cfg = current_app.config
    key = (cfg["PROXMOX_HOST"], cfg["PROXMOX_TOKEN_ID"])
    passed_at = _privilege_cache.get(key)
    if passed_at is not None and time.monotonic() - passed_at < cfg["PROXMOX_PRIVILEGE_CHECK_TTL"]:
        return
    try:
        perms = proxmox.access.permissions.get()
        # Which pool each VM is in decides what /vms/<id> grants are acceptable.
        # The token sees these through the VM.Audit it already holds.
        membership = pool_membership(proxmox.cluster.resources.get(type="vm"))
    except ProxmoxError:
        raise
    except Exception as exc:
        raise ProxmoxError(MSG_PERMS_UNREADABLE) from exc
    excess = excess_privileges(perms, cfg, membership)
    if excess:
        current_app.logger.error(
            "Proxmox token %s holds privileges The Pond must not have: %s",
            cfg["PROXMOX_TOKEN_ID"], excess)
        raise ProxmoxError(MSG_OVERPRIVILEGED)
    _privilege_cache[key] = time.monotonic()


def _refuse_protected(vmid):
    if int(vmid) in protected_vmids(current_app.config["PROXMOX_PROTECTED_VMIDS"]):
        raise ProxmoxError(MSG_PROTECTED_VM.format(vmid=vmid))


# --------------------------------------------------------- session isolation
#
# Every clone gets its own SDN VNet in a Simple zone, with no subnet and so no
# gateway: the only thing on that bridge is the one VM. Every NIC is moved onto
# it and the VM firewall drops inbound by default. Left on the template's
# bridge, a clone would share the lab VLAN with every other student's machine
# and have a route off it.
#
# This is fail-closed. The VM is not started until its config has been read
# back and every NIC confirmed isolated; if any step fails the clone and its
# VNet are destroyed and the launch is refused. A VM on the wrong network is
# worse than no VM.

_NIC_RE = re.compile(r"^net\d+$")
_VNET_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")   # SDN VNet ids: <=8 chars, lowercase first


def _vnet_name(vmid: int) -> str:
    """One VNet per clone, named from its vmid so teardown can find it without
    a database column. SDN caps ids at 8 characters (they become Linux bridge
    names), which fits any vmid below 16**7."""
    name = f"p{vmid:x}"
    if not _VNET_RE.match(name):
        raise ProxmoxError(f"vmid {vmid} is too large to name an isolation network")
    return name


def _wait(proxmox, node: str, upid, what: str):
    """Block until a Proxmox task finishes; raise unless it finished OK.

    Clone, stop and delete are asynchronous. Carrying on before they finish
    means reconfiguring a disk that is still locked, or deleting a VNet a VM
    is still attached to."""
    if not upid:
        return
    deadline = time.monotonic() + current_app.config["PROXMOX_TASK_TIMEOUT"]
    while time.monotonic() < deadline:
        status = proxmox.nodes(node).tasks(upid).status.get()
        if status.get("status") == "stopped":
            if status.get("exitstatus") != "OK":
                raise ProxmoxError(f"{what} failed: {status.get('exitstatus')}")
            return
        time.sleep(1)
    raise ProxmoxError(f"{what} did not finish in time")


def _check_isolation_preconditions(proxmox, zone: str):
    """Refuse to launch unless isolation can actually take effect.

    A zone of another type (VLAN, VXLAN, EVPN) can bridge sessions onto a
    shared network, and VM firewall rules are ignored entirely while the
    datacenter firewall is off — Proxmox raises no error for either."""
    zones = {z.get("zone"): z for z in proxmox.cluster.sdn.zones.get()}
    if zones.get(zone, {}).get("type") != "simple":
        raise ProxmoxError(f"SDN zone {zone!r} is missing or is not a Simple zone")
    if not int(proxmox.cluster.firewall.options.get().get("enable", 0)):
        raise ProxmoxError("the datacenter firewall is disabled, so VM firewalls would not apply")


def _isolated_nic(value: str, vnet: str) -> str:
    """Rewrite one netN line onto the session VNet: new bridge, no VLAN tag,
    firewall on."""
    parts = [p for p in value.split(",")
             if not p.startswith(("bridge=", "tag=", "trunks=", "firewall="))]
    return ",".join(parts + [f"bridge={vnet}", "firewall=1"])


def _nic_is_isolated(value: str, vnet: str) -> bool:
    parts = value.split(",")
    return (f"bridge={vnet}" in parts and "firewall=1" in parts
            and not any(p.startswith(("tag=", "trunks=")) for p in parts))


def _isolate(proxmox, node: str, vmid: int, vnet: str):
    """Move every NIC of a stopped clone onto vnet, turn its firewall on, and
    confirm both by reading the config back."""
    vm = proxmox.nodes(node).qemu(vmid)
    nics = {k: v for k, v in vm.config.get().items() if _NIC_RE.match(k)}
    if not nics:
        raise ProxmoxError(f"clone {vmid} has no network interface to isolate")
    vm.config.put(**{k: _isolated_nic(v, vnet) for k, v in nics.items()})
    vm.firewall.options.put(enable=1, policy_in="DROP")

    # Read back rather than trust the writes.
    config = vm.config.get()
    leaked = [k for k, v in config.items() if _NIC_RE.match(k) and not _nic_is_isolated(v, vnet)]
    if leaked:
        raise ProxmoxError(f"clone {vmid} still has NICs outside its session network: {leaked}")
    if not int(vm.firewall.options.get().get("enable", 0)):
        raise ProxmoxError(f"clone {vmid} firewall did not enable")


def _remove_vnet(proxmox, vnet: str):
    proxmox.cluster.sdn.vnets(vnet).delete()
    proxmox.cluster.sdn.put()


def _api_clone(template_vmid: int, node: str, label: str) -> Clone:
    """Real clone against Proxmox VE, isolated on its own network before boot.

    Linked clone by default (PROXMOX_FULL_CLONE=0): near-instant and small,
    because it shares the template's disk. That is right for short teaching
    sessions, and it means the storage setting is not consulted — the clone
    inherits the template's. Set PROXMOX_FULL_CLONE=1 if a challenge must
    survive the template changing underneath it, and the disk then lands on
    PROXMOX_STORAGE (local-lvm on this cluster).

    The API token needs VM.Clone on the template pool, VM.Allocate, VM.Audit,
    VM.Config.Network and VM.PowerMgmt on the clone pool, SDN rights on the
    session zone, and Sys.Audit to read the datacenter firewall state — and
    nothing else: the adapter checks this itself before every launch (see
    _check_token_privileges). The exact table is in docs/README.md.
    """
    cfg = current_app.config
    zone = cfg["PROXMOX_SDN_ZONE"]
    if not cfg["PROXMOX_POOL"]:
        raise ProxmoxError(MSG_POOL_MISSING)
    _refuse_protected(template_vmid)
    proxmox = _connect()
    # Cleanup only ever removes what THIS call created. nextid can race with
    # another launch, and a clone that failed because the vmid was taken must
    # not then delete the other student's machine.
    new_vmid = None
    vnet = None
    created_vm = created_vnet = False
    try:
        # First, before anything is cloned or networked: a token wider than
        # The Pond needs means launches are disabled, not merely logged.
        _check_token_privileges(proxmox)
        _check_isolation_preconditions(proxmox, zone)
        new_vmid = int(proxmox.cluster.nextid.get())
        # nextid knows nothing about other projects' vmids; never land on one.
        _refuse_protected(new_vmid)
        vnet = _vnet_name(new_vmid)
        options = {
            "newid": new_vmid,
            "name": label[:63],
            "full": 1 if cfg["PROXMOX_FULL_CLONE"] else 0,
            "target": node,
            "pool": cfg["PROXMOX_POOL"],   # the only pool the token can create VMs in
        }
        if cfg["PROXMOX_FULL_CLONE"]:
            # Proxmox rejects `storage` on a linked clone, so it is only sent
            # when the clone is full.
            options["storage"] = cfg["PROXMOX_STORAGE"]
        _wait(proxmox, node, proxmox.nodes(node).qemu(template_vmid).clone.post(**options), "clone")
        created_vm = True
        proxmox.cluster.sdn.vnets.post(vnet=vnet, zone=zone)
        created_vnet = True
        proxmox.cluster.sdn.put()   # apply pending SDN config so the bridge exists
        _isolate(proxmox, node, new_vmid, vnet)
        proxmox.nodes(node).qemu(new_vmid).status.start.post()
    except Exception as exc:  # proxmoxer raises library-specific errors
        _discard(proxmox, node, new_vmid if created_vm else None, vnet if created_vnet else None)
        if isinstance(exc, ProxmoxError):
            raise
        raise ProxmoxError(f"Proxmox refused the clone: {exc}") from exc

    return Clone(
        vmid=new_vmid,
        node=node,
        console_url=f"https://{current_app.config['PROXMOX_HOST']}:8006/?console=kvm&novnc=1&vmid={new_vmid}&node={node}",
        status="running",
    )


def _discard(proxmox, node: str, vmid: int, vnet):
    """Best-effort cleanup after a failed launch. Never raises: the launch is
    already failing and the original error is the one worth reporting."""
    if vmid is not None:
        try:
            _wait(proxmox, node, proxmox.nodes(node).qemu(vmid).delete(), "delete")
        except Exception as exc:
            print(f"[proxmox] could not remove failed clone {vmid}: {exc}")
    if vnet:
        try:
            _remove_vnet(proxmox, vnet)
        except Exception as exc:
            print(f"[proxmox] could not remove network {vnet}: {exc}")


def _api_destroy(vmid: int, node: str):
    # Teardown deliberately skips the privilege self-check: refusing here
    # because the token was over-privileged would orphan a student's running
    # VM. The protected-vmid guard still applies.
    _refuse_protected(vmid)
    proxmox = _connect()
    try:
        vm = proxmox.nodes(node).qemu(vmid)
        _wait(proxmox, node, vm.status.stop.post(), "stop")
        _wait(proxmox, node, vm.delete(), "delete")
        # Only after the VM is gone: a VNet with a VM still attached cannot be
        # removed, and removing it early would drop the student mid-session.
        # Clones launched before per-session networks existed have none.
        vnet = _vnet_name(vmid)
        if vnet in {v.get("vnet") for v in proxmox.cluster.sdn.vnets.get()}:
            _remove_vnet(proxmox, vnet)
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
