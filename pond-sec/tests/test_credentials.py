"""Checks that The Pond only ever holds, and trusts, what it should (H3).

Run with:  python -m tests.test_credentials      (no pytest needed)

H3 was a root@pam API token that could do anything to every VM on the cluster,
reached over a connection that did not verify the hypervisor's certificate.
This file pins the fix: which tokens the adapter will accept, when TLS may be
off, that clones stay in their pool and away from other projects' VMs, that the
adapter refuses to launch with a token wider than it needs, and that the
artefacts shipped alongside (the Ansible playbook, the legacy tools' settings)
agree with all of that.

Nothing here opens a network connection. A fake `proxmoxer` module is installed
before the app is imported, so even a regression that reaches for the real
client only records the attempt; everything else is driven through the shared
in-memory FakeProxmox (tests/fake_proxmox.py).

ADDING A CHECK: put it in the section it belongs to. Sections run through
section(), so a section that crashes against old code reports a FAIL instead of
taking the rest of the file down with it.
"""

import ast
import copy
import itertools
import json
import os
import re
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Nothing in the developer's shell may change what these checks see.
for _name in ("PROXMOX_TOKEN_ID", "PROXMOX_VERIFY_SSL", "PROXMOX_CA_BUNDLE", "PROXMOX_POOL",
              "PROXMOX_PROTECTED_VMIDS", "RANGE_ENV", "PROXMOX_BACKEND", "PROXMOX_SDN_ZONE",
              "PROXMOX_TEMPLATE_POOL", "PROXMOX_PRIVILEGE_CHECK_TTL", "PROXMOX_HOST",
              "PROXMOX_TEMPLATE_BRIDGES", "PROXMOX_TEMPLATE_STORAGES", "PROXMOX_STORAGE",
              "PROXMOX_NODE"):
    os.environ.pop(_name, None)

# A stand-in proxmoxer that only records how it was called.
_connections = []


class _RecordingAPI:
    def __init__(self, host, **kwargs):
        _connections.append((host, kwargs))


_fake_module = types.ModuleType("proxmoxer")
_fake_module.ProxmoxAPI = _RecordingAPI
sys.modules["proxmoxer"] = _fake_module

import jinja2                                   # noqa: E402
import yaml                                     # noqa: E402

from app import create_app, proxmox             # noqa: E402
from app.config import Config, verify_flag      # noqa: E402
from tests.fake_proxmox import FakeProxmox, MINIMAL_PERMISSIONS, ROOT_PERMISSIONS   # noqa: E402

checks = []


def check(label, condition):
    checks.append((label, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {label}")


def section(name, fn):
    print(f"\n-- {name}")
    try:
        fn()
    except Exception as exc:    # a crash is a failed check, not a dead test run
        check(f"{name}: section ran without crashing ({type(exc).__name__}: {exc})", False)


_real_connect = proxmox._connect
_MISSING = object()
_HERE = os.path.dirname(os.path.abspath(__file__))
SEC_ROOT = os.path.dirname(_HERE)                       # pond-sec/
REPO_ROOT = os.path.dirname(SEC_ROOT)


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# ----------------------------------------------------------------- helpers

def _scratch_config(**overrides):
    scratch = tempfile.mkdtemp(prefix="pondsec-credentials-")
    config = {"DATABASE": os.path.join(scratch, "range.sqlite"), "TESTING": True,
              "SECRET_KEY": "test", "PROXMOX_BACKEND": "api",
              "PROXMOX_TOKEN_ID": "pond@pve!launcher", "PROXMOX_TOKEN_SECRET": "unused",
              "PROXMOX_TASK_TIMEOUT": 5,
              "SQLALCHEMY_BINDS": {"pond": f"sqlite:///{scratch}/pond.db"},
              "UPLOAD_DIR": os.path.join(scratch, "uploads")}
    config.update(overrides)
    return config


app = create_app(_scratch_config())

_ca_dir = tempfile.mkdtemp(prefix="pondsec-ca-")
CA_PATH = os.path.join(_ca_dir, "pve-root-ca.pem")
with open(CA_PATH, "w", encoding="utf-8") as _handle:
    _handle.write("not a real certificate; only its existence is checked\n")


def make_app(**overrides):
    """(app, None) if create_app succeeded, (None, exception) if it refused."""
    try:
        return create_app(_scratch_config(**overrides)), None
    except Exception as exc:
        return None, exc


def connect_with(**overrides):
    """Run the REAL _connect() under temporary config. Returns
    (kwargs the client was built with or None, exception or None)."""
    saved = {k: app.config.get(k, _MISSING) for k in overrides}
    app.config.update(overrides)
    _connections.clear()
    try:
        with app.app_context():
            try:
                _real_connect()
            except proxmox.ProxmoxError as exc:
                return None, exc
        return (_connections[-1][1] if _connections else None), None
    finally:
        for key, value in saved.items():
            if value is _MISSING:
                app.config.pop(key, None)
            else:
                app.config[key] = value


def _reset_cache():
    getattr(proxmox, "_privilege_cache", {}).clear()


def launch(fake, template=FakeProxmox.TEMPLATE, keep_cache=False, connect=None, **cfg):
    """Run clone_and_start against fake; return (clone, error)."""
    proxmox._connect = connect or (lambda: fake)
    if not keep_cache:
        _reset_cache()
    saved = {k: app.config.get(k, _MISSING) for k in cfg}
    app.config.update(cfg)
    try:
        with app.app_context():
            try:
                return proxmox.clone_and_start(template, "pve", "student-c1"), None
            except proxmox.ProxmoxError as exc:
                return None, exc
    finally:
        proxmox._connect = _real_connect
        for key, value in saved.items():
            if value is _MISSING:
                app.config.pop(key, None)
            else:
                app.config[key] = value


def destroy(fake, vmid, connect=None):
    proxmox._connect = connect or (lambda: fake)
    try:
        with app.app_context():
            try:
                proxmox.stop_and_destroy(vmid, "pve")
                return None
            except proxmox.ProxmoxError as exc:
                return exc
    finally:
        proxmox._connect = _real_connect


def calls(fake, method, suffix):
    return [c for c in fake.calls if c[0] == method and c[1].endswith(suffix)]


def with_permission(path, *privileges):
    perms = copy.deepcopy(MINIMAL_PERMISSIONS)
    perms.setdefault(path, {}).update({p: 1 for p in privileges})
    return perms


def message(exc):
    return str(exc) if exc else ""


# ---------------------------------------------------------------- defaults

def defaults():
    protected = getattr(proxmox, "protected_vmids", lambda spec: None)
    check("no usable default token ID", not Config.PROXMOX_TOKEN_ID)
    check("TLS verification is on by default outside production",
          Config.PROXMOX_VERIFY_SSL is True and not Config.IS_PRODUCTION)
    check("no CA bundle is assumed", getattr(Config, "PROXMOX_CA_BUNDLE", "missing") is None)
    check("clones go to the pond-clones pool by default",
          getattr(Config, "PROXMOX_POOL", None) == "pond-clones")
    check("VMs 300-303 are protected by default",
          protected(getattr(Config, "PROXMOX_PROTECTED_VMIDS", "")) == {300, 301, 302, 303})
    with open(os.path.join(SEC_ROOT, "app", "config.py"), encoding="utf-8") as handle:
        check("config.py no longer names the root token", "root@pam!root" not in handle.read())
    check(".env.example no longer names the root token",
          "root@pam!root" not in read("WSGI_Files", ".env.example"))
    check("no template bridge is granted by default", getattr(Config, "PROXMOX_TEMPLATE_BRIDGES", None) == "")
    check("local-lvm is the default template storage",
          getattr(Config, "PROXMOX_TEMPLATE_STORAGES", None) == "local-lvm")
    env_example = read("WSGI_Files", ".env.example")
    check(".env.example documents the new bridge and storage settings",
          "PROXMOX_TEMPLATE_BRIDGES" in env_example and "PROXMOX_TEMPLATE_STORAGES" in env_example
          and "playbook" in env_example)


# ---------------------------------------------------------- token identity

def token_identity():
    kwargs, exc = connect_with(PROXMOX_TOKEN_ID="root@pam!root")
    check("root@pam!root is refused", exc and "@pam" in str(exc))
    check("...before any connection is attempted", kwargs is None and not _connections)
    kwargs, exc = connect_with(PROXMOX_TOKEN_ID="admin@pam!pond")
    check("any other @pam token is refused", exc and "@pam" in str(exc))
    kwargs, exc = connect_with(PROXMOX_TOKEN_ID=None)
    check("a missing token ID is refused", "PROXMOX_TOKEN_ID is not set" in message(exc))
    kwargs, exc = connect_with(PROXMOX_TOKEN_ID="pond@pve")
    check("a token ID without a token name is refused", "user@realm!tokenname" in message(exc))
    kwargs, exc = connect_with(PROXMOX_TOKEN_SECRET=None)
    check("a missing secret is refused", exc and "PROXMOX_TOKEN_SECRET" in str(exc))
    kwargs, exc = connect_with(PROXMOX_POOL="")
    check("an empty pool is refused", exc and "PROXMOX_POOL" in str(exc))
    kwargs, exc = connect_with()
    check("the pond@pve token is accepted and split correctly",
          kwargs and kwargs.get("user") == "pond@pve" and kwargs.get("token_name") == "launcher")

    parse = getattr(proxmox, "parse_token_id", None)

    def refused(token_id):
        try:
            parse(token_id)
        except proxmox.ProxmoxError as exc:
            return str(exc)
        return None

    check("a mixed-case realm (root@PAM!x) is still refused as @pam", "@pam" in (refused("root@PAM!x") or ""))
    check("a token ID with leading whitespace is refused", refused(" root@pam!root") is not None)
    check("a token ID with trailing whitespace is refused", refused("root@pam!root ") is not None)
    check("a token ID with a trailing newline is refused", refused("pond@pve!launcher\n") is not None)
    check("a clean pond@pve!launcher parses", parse("pond@pve!launcher") == ("pond@pve", "launcher"))


# --------------------------------------------------------------------- TLS

def tls():
    kwargs, exc = connect_with()
    check("certificate verification is on by default", kwargs and kwargs.get("verify_ssl") is True)
    kwargs, exc = connect_with(PROXMOX_CA_BUNDLE=CA_PATH)
    check("PROXMOX_CA_BUNDLE is passed through as the CA path",
          kwargs and kwargs.get("verify_ssl") == CA_PATH)
    kwargs, exc = connect_with(PROXMOX_CA_BUNDLE=CA_PATH, PROXMOX_VERIFY_SSL=False)
    check("a CA bundle overrides PROXMOX_VERIFY_SSL=0", kwargs and kwargs.get("verify_ssl") == CA_PATH)
    kwargs, exc = connect_with(PROXMOX_CA_BUNDLE=os.path.join(_ca_dir, "absent.pem"))
    check("a CA bundle that does not exist is refused rather than ignored",
          "no such file" in message(exc) and kwargs is None)
    kwargs, exc = connect_with(PROXMOX_VERIFY_SSL=False)
    check("verification can be switched off explicitly in development",
          kwargs and kwargs.get("verify_ssl") is False)
    kwargs, exc = connect_with(PROXMOX_VERIFY_SSL=False, IS_PRODUCTION=True)
    check("verification off is refused in production", "not allowed in production" in message(exc))
    check("only an explicit 0/false/no/off switches verification off",
          all(verify_flag(v) is False for v in ["0", "false", "No", "OFF", " 0 ", "False\n"]))
    check("anything else, including true/yes/empty/garbage/unset, leaves it on",
          all(verify_flag(v) is True for v in ["1", "true", "yes", "", "  ", "garbage", None]))


# ------------------------------------------------------------ startup rules

def startup():
    good = dict(IS_PRODUCTION=True, SECRET_KEY="x", PROXMOX_BACKEND="api",
                PROXMOX_TOKEN_ID="pond@pve!launcher", PROXMOX_TOKEN_SECRET="s",
                PROXMOX_CA_BUNDLE=CA_PATH, PROXMOX_VERIFY_SSL=True)
    _, exc = make_app(**dict(good, PROXMOX_TOKEN_ID="root@pam!root"))
    check("production refuses to start with the root token", isinstance(exc, RuntimeError))
    _, exc = make_app(**dict(good, PROXMOX_CA_BUNDLE=None, PROXMOX_VERIFY_SSL=False))
    check("production refuses to start with TLS verification off", isinstance(exc, RuntimeError))
    _, exc = make_app(**dict(good, PROXMOX_TOKEN_ID=None))
    check("production refuses to start with no token ID", isinstance(exc, RuntimeError))
    started, exc = make_app(**good)
    check("production starts with the pond token and a CA bundle", started is not None and exc is None)
    started, exc = make_app(IS_PRODUCTION=True, SECRET_KEY="x", PROXMOX_BACKEND="simulate",
                            PROXMOX_TOKEN_ID=None, PROXMOX_TOKEN_SECRET=None)
    check("production with the simulate backend needs no Proxmox credentials",
          started is not None and exc is None)
    started, exc = make_app(PROXMOX_TOKEN_ID=None, PROXMOX_TOKEN_SECRET=None)
    check("development with the api backend still starts (launches fail closed instead)",
          started is not None and exc is None)


# ------------------------------------------------- pool and protected VMs

def pool_and_protected():
    fake = FakeProxmox()
    clone, err = launch(fake)
    check("a launch sends pool=pond-clones on the clone",
          clone is not None and any(kw.get("pool") == "pond-clones"
                                    for _, _, kw in calls(fake, "POST", "clone")))
    check("the new clone lands in the pool", 9001 in fake.pools.get("pond-clones", set()))

    touched = []
    fake = FakeProxmox()
    clone, err = launch(fake, template=301, connect=lambda: touched.append(1) or fake)
    check("cloning a protected template (301) is refused", clone is None and "protected" in message(err))
    check("...without contacting Proxmox", not touched and not fake.calls)

    touched = []
    fake = FakeProxmox()
    err = destroy(fake, 300, connect=lambda: touched.append(1) or fake)
    check("tearing down a protected vmid (300) is refused without contacting Proxmox",
          err is not None and "protected" in message(err) and not touched and not fake.calls)

    fake = FakeProxmox()
    fake.next_vmid = 302
    clone, err = launch(fake)
    check("a nextid inside the protected range is refused before cloning",
          clone is None and "protected" in message(err) and not calls(fake, "POST", "clone"))


# --------------------------------------------------------- privilege check

def privilege_check():
    fake = FakeProxmox()
    clone, err = launch(fake)
    check("the minimal token passes and the launch succeeds", clone is not None and err is None)
    check("the self-check runs before anything else",
          fake.calls and fake.calls[0][:2] == ("GET", "access/permissions"))

    fake = FakeProxmox()
    fake.permissions = copy.deepcopy(ROOT_PERMISSIONS)
    clone, err = launch(fake)
    check("a root-equivalent token is refused", clone is None and "over-privileged" in message(err))
    check("...before anything is cloned or networked",
          not calls(fake, "POST", "clone") and not fake.vnets and set(fake.vms) == {FakeProxmox.TEMPLATE})
    check("...and the student is not shown the privilege list", "Sys.Modify" not in message(err))

    def attempt(path, *privileges, setup=None, **cfg):
        fake = FakeProxmox()
        fake.permissions = with_permission(path, *privileges)
        if setup:
            setup(fake)
        clone, err = launch(fake, **cfg)
        return clone, err, fake

    def over_privileged(path, *privileges, **kw):
        clone, err, _ = attempt(path, *privileges, **kw)
        return clone is None and "over-privileged" in message(err)

    for privilege in ["Permissions.Modify", "Sys.Modify", "Sys.Console", "Sys.PowerMgmt", "User.Modify",
                      "Datastore.Allocate", "Realm.Allocate", "Group.Allocate", "Pool.Allocate"]:
        check(f"{privilege} on the clone pool is refused as over-privileged",
              over_privileged("/pool/pond-clones", privilege))

    def add_vm(vmid, pool):
        def setup(fake):
            fake.vms[vmid] = {"name": f"vm{vmid}"}
            if pool:
                fake.pools.setdefault(pool, set()).add(vmid)
        return setup

    check("any privilege on /vms (every VM) is refused", over_privileged("/vms", "VM.Audit"))
    check("any privilege on the other project's VM 301 is refused",
          over_privileged("/vms/301", "VM.Audit", setup=add_vm(301, None)))
    check("VM.Allocate on a VM outside both pools (250) is refused",
          over_privileged("/vms/250", "VM.Allocate", setup=add_vm(250, None)))
    check("...including a vmid that does not appear in the cluster listing (304)",
          over_privileged("/vms/304", "VM.Allocate"))
    check("VM.Allocate on a template (/vms/100) is refused", over_privileged("/vms/100", "VM.Allocate"))
    clone, err, _ = attempt("/vms/9500", *sorted(proxmox._VM_PRIVS_CLONES), setup=add_vm(9500, "pond-clones"))
    check("clone-pool privileges on a VM that is in the clone pool are allowed",
          clone is not None and err is None)
    check("clone-pool privileges on a VM in the template pool are refused",
          over_privileged("/vms/9500", *sorted(proxmox._VM_PRIVS_CLONES), setup=add_vm(9500, "pond-templates")))
    check("SDN rights on another zone are refused", over_privileged("/sdn/zones/other", "SDN.Use"))
    check("SDN.Use on every local bridge is refused", over_privileged("/sdn/zones/localnetwork", "SDN.Use"))
    clone, err, _ = attempt("/sdn/zones/localnetwork/vmbr0", "SDN.Use", PROXMOX_TEMPLATE_BRIDGES="vmbr0")
    check("SDN.Use on one named, configured template bridge is allowed", clone is not None and err is None)
    check("SDN.Use on a bridge that is not in PROXMOX_TEMPLATE_BRIDGES is refused",
          over_privileged("/sdn/zones/localnetwork/vmbr0", "SDN.Use")
          and over_privileged("/sdn/zones/localnetwork/vmbr9", "SDN.Use", PROXMOX_TEMPLATE_BRIDGES="vmbr0"))
    check("space on a storage that is not configured is refused",
          over_privileged("/storage/other", "Datastore.AllocateSpace"))
    check("Sys.Audit on a node other than PROXMOX_NODE is refused", over_privileged("/nodes/other", "Sys.Audit"))
    clone, err, _ = attempt("/nodes/pve", "Sys.Audit")
    check("Sys.Audit on PROXMOX_NODE is allowed", clone is not None and err is None)
    check("disk reconfiguration on clones is refused", over_privileged("/pool/pond-clones", "VM.Config.Disk"))

    fake = FakeProxmox()
    fake.permissions_error = True
    clone, err = launch(fake)
    check("an unreadable permission reply refuses the launch",
          clone is None and "Could not read" in message(err))
    fake = FakeProxmox()
    fake.permissions = ["not", "a", "dict"]
    clone, err = launch(fake)
    check("a malformed permission reply refuses the launch",
          clone is None and "Could not read" in message(err))
    fake = FakeProxmox()
    fake.resources_error = True
    clone, err = launch(fake)
    check("an unreadable resource listing refuses the launch",
          clone is None and "Could not read" in message(err) and not calls(fake, "POST", "clone"))

    allowed = getattr(proxmox, "allowed_privileges", None)
    with app.app_context():
        cfg = app.config
        check("/pool/pond-clones-evil is not the clone pool", allowed("/pool/pond-clones-evil", cfg) == frozenset())
        check("/sdn/zones/pondzX is not the session zone", allowed("/sdn/zones/pondzX", cfg) == frozenset())
        check("/vms/0300 is recognised as protected VM 300", allowed("/vms/0300", cfg, {300: "pond-clones"}) == frozenset())
        check("/vms/301/ (trailing slash) is still protected VM 301",
              allowed("/vms/301/", cfg, {301: "pond-clones"}) == frozenset())
        try:
            odd = allowed("/vms/\u00b2", cfg, {})
        except Exception:
            odd = None
        check("/vms/<superscript two> is simply not a VM path (no crash, no privileges)", odd == frozenset())


    fake = FakeProxmox()
    _reset_cache()
    first, _ = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=300)
    fake.next_vmid = 9002
    second, _ = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=300)
    check("a passed check is cached",
          first and second and len(calls(fake, "GET", "access/permissions")) == 1)

    fake = FakeProxmox()
    fake.permissions = copy.deepcopy(ROOT_PERMISSIONS)
    _reset_cache()
    first = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=300)
    second = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=300)
    check("a failed check is never cached",
          first[1] and second[1] and len(calls(fake, "GET", "access/permissions")) == 2)

    fake = FakeProxmox()
    _reset_cache()
    first, _ = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=0)
    fake.next_vmid = 9002
    second, _ = launch(fake, keep_cache=True, PROXMOX_PRIVILEGE_CHECK_TTL=0)
    check("TTL 0 re-checks every launch",
          first and second and len(calls(fake, "GET", "access/permissions")) == 2)

    fake = FakeProxmox()
    clone, _ = launch(fake)
    fake.permissions = copy.deepcopy(ROOT_PERMISSIONS)
    err = destroy(fake, 9001)
    check("teardown is not blocked by the self-check (no orphaned VMs)",
          clone is not None and err is None and 9001 not in fake.vms)


# ------------------------------------------------------ delivered artefacts

def _playbook():
    text = read("playbooks", "pond_least_privilege.yml")
    return text, yaml.safe_load(text)[0]


# A stand-in for Ansible's templating, close enough to catch the failure that
# matters: a set_fact value that renders with leading whitespace is kept as a
# STRING by Ansible, and the first `| product(...)` then iterates characters.
_jinja = jinja2.Environment(undefined=jinja2.StrictUndefined)
_jinja.filters["dict2items"] = lambda d: [{"key": k, "value": v} for k, v in d.items()]
_jinja.filters["product"] = lambda first, *rest: list(itertools.product(first, *rest))
_jinja.filters["from_json"] = json.loads


def _resolve(value, ctx):
    if isinstance(value, str) and ("{{" in value or "{%" in value):
        return _jinja.from_string(value).render(**ctx)
    if isinstance(value, dict):
        return {k: _resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    return value


_SAMPLE_ACLS = [{"path": "/", "roleid": "PondAudit", "type": "user", "ugid": "pond@pve", "propagate": 0},
                {"path": "/sdn", "roleid": "PondSDNApply", "type": "token", "ugid": "pond@pve!launcher",
                 "propagate": 0}]


def render_playbook(token_exists=True, **var_overrides):
    """Render every set_fact of the playbook the way Ansible would, with the play
    vars (plus overrides) and sample command output. Returns (raw strings,
    parsed values, resolved vars, the loop expression's parsed value)."""
    text, play = _playbook()
    raw_vars = dict(play["vars"], **var_overrides)
    ctx = dict(raw_vars)
    for _ in range(3):
        ctx = {k: _resolve(v, ctx) for k, v in raw_vars.items()}
    ctx["acls_raw"] = {"stdout": json.dumps(_SAMPLE_ACLS)}
    ctx["tokens_raw"] = ({"rc": 0, "stdout": json.dumps([{"tokenid": "launcher", "privsep": 1}])}
                         if token_exists else {"rc": 1, "stdout": ""})
    raw, parsed = {}, {}
    for task in play["tasks"]:
        for key, value in (task.get("ansible.builtin.set_fact") or {}).items():
            rendered = _resolve(value, ctx)
            if isinstance(rendered, str):
                raw[key] = rendered
                rendered = ast.literal_eval(rendered) if rendered[:1] in "[{" else rendered
            parsed[key] = ctx[key] = rendered
    loop = None
    for task in play["tasks"]:
        if task.get("name") == "Grant the ACLs that are missing":
            loop = ast.literal_eval(_resolve(task["loop"], ctx))
    return raw, parsed, ctx, loop


def _function_body(text, name):
    start = text.index(f"def {name}(")
    end = text.find("\ndef ", start + 1)
    return text[start:end if end != -1 else len(text)]


def artefacts():
    text, play = _playbook()
    check("the least-privilege playbook exists and parses", bool(play.get("tasks")))

    # -- Jinja values must survive as lists, not strings
    raw, parsed, ctx, loop = render_playbook()
    check("every list-valued fact renders with no leading whitespace",
          all(raw[k][:1] == "[" for k in ("pond_desired_acls", "pond_desired_roles",
                                          "pond_existing_acl_keys", "pond_token_rows")))
    check("pond_desired_acls is a list of 6 rows with default vars (5 fixed + local-lvm)",
          isinstance(parsed["pond_desired_acls"], list) and len(parsed["pond_desired_acls"]) == 6
          and all(isinstance(r, dict) and {"path", "role", "propagate"} <= set(r) for r in parsed["pond_desired_acls"]))
    check("the ACL loop yields 12 (row, principal) pairs, not characters",
          isinstance(loop, list) and len(loop) == 12 and all(isinstance(pair[0], dict) for pair in loop))
    check("pond_desired_roles is a list of 6 roles without PondBridge by default",
          len(parsed["pond_desired_roles"]) == 6
          and "PondBridge" not in {r["key"] for r in parsed["pond_desired_roles"]})
    check("pond_existing_acl_keys is a list with one key per existing ACL",
          len(parsed["pond_existing_acl_keys"]) == 2)
    check("pond_token_rows is a one-item list when the token exists and [] when it does not",
          len(parsed["pond_token_rows"]) == 1
          and render_playbook(token_exists=False)[1]["pond_token_rows"] == [])
    raw2, parsed2, ctx2, loop2 = render_playbook(pond_template_bridges=["vmbr0"], pond_grant_node_audit=True)
    check("with a bridge and node audit the ACL list is 8 rows, roles 7, loop 16",
          len(parsed2["pond_desired_acls"]) == 8 and len(parsed2["pond_desired_roles"]) == 7
          and raw2["pond_desired_acls"][:1] == "[" and len(loop2) == 16)

    # -- what those rendered grants amount to, judged by the app's own allowlist
    roles = ctx2["pond_roles"]
    perms = {}
    for acl in parsed2["pond_desired_acls"]:
        perms.setdefault(acl["path"], {}).update({p: acl["propagate"] for p in roles[acl["role"]]})
    excess_privileges = getattr(proxmox, "excess_privileges", None)
    with app.app_context():
        saved = app.config.get("PROXMOX_TEMPLATE_BRIDGES")
        app.config["PROXMOX_TEMPLATE_BRIDGES"] = "vmbr0"
        try:
            check("the playbook's grants pass the app's own allowlist",
                  excess_privileges is not None and excess_privileges(perms, app.config) == [])
        finally:
            app.config["PROXMOX_TEMPLATE_BRIDGES"] = saved

    granted = {p for privs in roles.values() for p in privs}
    dangerous = getattr(proxmox, "DANGEROUS_PRIVILEGES", None)
    check("the playbook grants nothing dangerous",
          dangerous is not None and not (granted & dangerous)
          and "VM.Console" not in granted and "VM.Config.Disk" not in granted)
    check("the playbook creates a privilege-separated token", "--privsep 1" in text)
    all_paths = [r["path"] for r in parsed["pond_desired_acls"] + parsed2["pond_desired_acls"]]
    check("the playbook never grants on /vms paths (including the paths built by set_fact)",
          all_paths and not any(path.startswith("/vms") for path in all_paths) and "acl modify /vms" not in text)
    check("the playbook refuses protected vmids as templates",
          "intersect(pond_protected_vmids)" in text)
    check("the playbook refuses to create a token while an old secret file exists",
          "Refuse to create a token while an old secret file exists" in text
          and "shred -u" in text
          and text.index("Refuse to create a token while an old secret file exists")
          < text.index("Create the privilege-separated token"))
    check("the secret file is overwritten on rotation (force: true), never kept (force: false)",
          "force: true" in text and "force: false" not in text)
    header = text.split("- name:")[0]
    check("the playbook header says to run it from a directory with no ansible.cfg",
          "no ansible.cfg" in header and "cd /root && ansible-playbook" in header
          and "vault_password_file" in header and "ANSIBLE_CONFIG=/dev/null" not in text)

    docs = read("pond-sec", "docs", "README.md")
    check("the README runbook says how to run it and keeps the secret out of shell history and ps",
          "cd /root && ansible-playbook" in docs and "ANSIBLE_CONFIG=/dev/null" not in docs
          and "read -rs" in docs and "zgrep" in docs
          and "PVEAPIToken=pond@pve!launcher=<secret>" not in docs)
    check("the README probes never write (no POST to /access/users) and quote the ?type=vm URL",
          "-X POST" not in docs and "access/users -d" not in docs
          and '"https://10.1.21.151:8006/api2/json/cluster/resources?type=vm"' in docs)

    provisioner = read("provisioner.py")
    check("provisioner.py no longer disables TLS verification", "verify_ssl=False" not in provisioner)
    check("provisioner.py no longer auto-trusts host keys",
          "AutoAddPolicy" not in provisioner and "RejectPolicy" in provisioner)
    check("every provisioner.py function that writes to or acts on a caller-supplied vmid refuses "
          "the protected ones (instance_exists is a read-only status probe, always reached via a guarded caller)",
          all("_refuse_protected" in _function_body(provisioner, name)
              for name in ("create_instance", "clone_and_start", "enable_vm_firewall", "apply_network_rule",
                           "get_console_ticket", "destroy_instance", "stop_and_destroy",
                           "inject_instance_network", "_find_disk_volid")))
    check("provisioner.py refuses @pam and realm-less users after stripping whitespace",
          ".strip()" in _function_body(provisioner, "get_client")
          and "not realm" in _function_body(provisioner, "get_client"))
    importer = read("import_challenge.py")
    check("import_challenge.py no longer auto-trusts host keys or disables TLS",
          "AutoAddPolicy" not in importer and "RejectPolicy" in importer
          and "verify_ssl=False" not in importer)
    check("import_challenge.py refuses @pam and realm-less API users",
          "not realm" in _function_body(importer, "connect_api")
          and '"pam"' in _function_body(importer, "connect_api"))
    check("import_challenge.py refuses root SSH unless explicitly allowed",
          "THEPOND_PROXMOX_ALLOW_ROOT_SSH" in _function_body(importer, "connect_ssh"))
    check("group_vars no longer use root@pam", "root@pam" not in read("group_vars", "all.yml"))
    check("Ansible checks SSH host keys", "host_key_checking = False" not in read("ansible.cfg"))


section("defaults", defaults)
section("token identity", token_identity)
section("TLS", tls)
section("startup rules", startup)
section("pool and protected VMs", pool_and_protected)
section("privilege self-check", privilege_check)
section("delivered artefacts", artefacts)

passed = sum(1 for _, ok in checks if ok)
print(f"\n{passed}/{len(checks)} checks passed")
sys.exit(0 if passed == len(checks) else 1)
