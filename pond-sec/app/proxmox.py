"""Hypervisor adapter - thin shim over the shared provisioner.

Always talks to a real Proxmox cluster - there is no simulate/fake mode.

Cloning, per-session VNets, firewalling and console tickets are done by
provisioner.py. This module adds the credential and trust rules (H3) in front
of every call into it: which token the app will hold, which VMs it will never
touch, and a per-launch check that the token has not been widened.
"""

import os
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import provisioner as _core  # noqa: E402
from flask import current_app  # noqa: E402

Clone = _core.Clone
ConsoleTicket = _core.ConsoleTicket
ProxmoxError = _core.ProxmoxError


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
#   * other projects' VMs (PROXMOX_PROTECTED_VMIDS) are never touched;
#   * before a launch the token's own permissions are read back and compared
#     with an allowlist, so a token that was widened later is caught even
#     though it is a perfectly valid token.
#
# None of this needs the network except _check_token_privileges, so the rules
# are pure functions that tests/test_credentials.py can drive directly.
#
# provisioner.get_client() builds the connection from this same Flask config
# (token ID, secret, PROXMOX_VERIFY_SSL, PROXMOX_CA_BUNDLE), so what is checked
# here is what is used. Clones are created in PROXMOX_POOL.

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
    _client() (refuse to connect) and by create_app() (refuse to start in
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


# What the token may hold, per ACL path. These mirror the roles in
# playbooks/pond_least_privilege.yml; tests/test_credentials.py builds the
# playbook's grants and runs them through excess_privileges() so the two
# cannot drift. A new privilege need means editing both.
#
# The integrated app has a console relay (themes.console_relay), which needs a
# vncproxy ticket and therefore VM.Console on the clones. It is in
# _VM_PRIVS_CLONES here and must be in the playbook's PondClones role too.
_VM_PRIVS_CLONES = frozenset({"VM.Allocate", "VM.Audit", "VM.Config.Network", "VM.PowerMgmt",
                              "VM.Console"})
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

    Runs first in clone_and_start, before anything is cloned or networked. The
    check trusts the hypervisor's own report, which is acceptable because the
    channel is verified. Students get a generic error; the offending paths go
    to the log."""
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


# ------------------------------------------------------------ client

def _client():
    """The provisioner's client, but only once the settings pass.

    Refuses before a client is built, so a bad setting can never result in even
    one request carrying the token."""
    cfg = current_app.config
    problems = settings_problems(cfg)
    if problems:
        raise ProxmoxError(problems[0])
    if not _tls_verify(cfg):
        current_app.logger.warning(
            "TLS verification to Proxmox at %s is OFF (PROXMOX_VERIFY_SSL=0); anyone on the path "
            "can impersonate the hypervisor and capture the API token.", cfg["PROXMOX_HOST"])
    return _core.get_client(cfg)


# ------------------------------------------------------------ operations

def clone_and_start(template_vmid: int, node: str = None, label: str = "challenge",
                     *, instance_id: int, template_id: int, vnet: str | None = None,
                     static_ip: str | None = None, gateway: str | None = None) -> Clone:
    """
    static_ip: pass the launching challenge's VMTemplate.static_ip to bake
    a static network config into this clone before boot (see provisioner's
    inject_instance_network()). None skips it entirely - the clone boots
    with whatever the template's own disk already has.

    gateway: only meaningful when vnet is None (single-VM challenges,
    which stay on the template's own flat/shared bridge). launch() decides
    which case applies and should only pass a gateway in the no-vnet case -
    provisioner.clone_and_start() drops it anyway if vnet is set, but don't
    rely on that from here; pass it deliberately, not by default.

    proxmox_host is not a parameter here - it's derived from
    cfg["PROXMOX_HOST"] below, same source node already uses, so a caller
    can't accidentally point the SSH injection step at the wrong cluster.

    Before anything is cloned: the template must not be a protected VM, and
    the token must pass the privilege self-check. A token wider than The Pond
    needs means launches are disabled, not merely logged.
    """
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    _refuse_protected(template_vmid)
    client = _client()
    _check_token_privileges(client)
    proxmox_host = cfg["PROXMOX_HOST"] if static_ip is not None else None
    return _core.clone_and_start(
        client, template_vmid, node, label=label,
        full_clone=cfg["PROXMOX_FULL_CLONE"], storage=cfg["PROXMOX_STORAGE"],
        instance_id=instance_id, template_id=template_id, vnet=vnet,
        static_ip=static_ip, proxmox_host=proxmox_host, gateway=gateway,
        pool=cfg["PROXMOX_POOL"],   # the only pool the token can create VMs in
    )


def stop_and_destroy(vmid: int, node: str = None, vnet: str | None = None):
    # Teardown deliberately skips the privilege self-check: refusing here
    # because the token was over-privileged would orphan a student's running
    # VM. The protected-vmid guard still applies.
    _refuse_protected(vmid)
    cfg = current_app.config
    node = node or cfg["PROXMOX_NODE"]
    _core.stop_and_destroy(_client(), vmid, node, vnet=vnet)


def get_console_ticket(vmid: int, node: str = None) -> ConsoleTicket:
    _refuse_protected(vmid)
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
    _refuse_protected(vmid)
    node = node or current_app.config["PROXMOX_NODE"]
    _core.enable_vm_firewall(_client(), node, vmid)


def apply_network_rule(dest_vmid: int, source_ip: str, port: int,
                        node: str = None, proto: str = "tcp") -> None:
    _refuse_protected(dest_vmid)
    node = node or current_app.config["PROXMOX_NODE"]
    _core.apply_network_rule(_client(), node, dest_vmid, source_ip, port, proto)
