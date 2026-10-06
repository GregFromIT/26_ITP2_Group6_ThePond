"""In-memory stand-in for the Proxmox REST API.

Shared by tests/test_isolation.py and tests/test_credentials.py. It answers the
handful of API paths app/proxmox.py uses and records every call in .calls, so a
check can assert both the end state (where each NIC ended up, which pool a clone
landed in) and the order things happened in (nothing boots before it is
isolated; the privilege self-check comes first).

cluster/resources reports each VM's pool (the template in pond-templates, each
clone in the pool named by the clone call), which the self-check needs to judge
/vms/<id> grants.

MINIMAL_PERMISSIONS is what GET /access/permissions returns for a token scoped
exactly as playbooks/pond_least_privilege.yml scopes it. ROOT_PERMISSIONS is a
token that can do everything. Tests set fake.permissions to one of these, or to
a copy with a privilege added, to exercise the self-check.
"""

import copy

_CLONES = {"VM.Allocate": 1, "VM.Audit": 1, "VM.Config.Network": 1, "VM.PowerMgmt": 1, "Pool.Audit": 1}

MINIMAL_PERMISSIONS = {
    "/": {"Sys.Audit": 0},
    "/sdn": {"SDN.Allocate": 0},
    "/sdn/zones/pondz": {"SDN.Allocate": 1, "SDN.Audit": 1, "SDN.Use": 1},
    "/pool/pond-clones": dict(_CLONES),
    "/pool/pond-templates": {"VM.Audit": 1, "VM.Clone": 1, "Pool.Audit": 1},
    "/storage/local-lvm": {"Datastore.AllocateSpace": 0},
    "/vms/100": {"VM.Audit": 1, "VM.Clone": 1, "Pool.Audit": 1},
}

_EVERY_PRIVILEGE = [
    "Datastore.Allocate", "Datastore.AllocateSpace", "Datastore.AllocateTemplate", "Datastore.Audit",
    "Group.Allocate", "Mapping.Audit", "Mapping.Modify", "Mapping.Use",
    "Permissions.Modify", "Pool.Allocate", "Pool.Audit",
    "Realm.Allocate", "Realm.AllocateUser",
    "SDN.Allocate", "SDN.Audit", "SDN.Use",
    "Sys.AccessNetwork", "Sys.Audit", "Sys.Console", "Sys.Incoming", "Sys.Modify",
    "Sys.PowerMgmt", "Sys.Syslog",
    "User.Modify",
    "VM.Allocate", "VM.Audit", "VM.Backup", "VM.Clone",
    "VM.Config.CDROM", "VM.Config.CPU", "VM.Config.Cloudinit", "VM.Config.Disk",
    "VM.Config.HWType", "VM.Config.Memory", "VM.Config.Network", "VM.Config.Options",
    "VM.Console", "VM.GuestAgent.Audit", "VM.GuestAgent.FileRead", "VM.GuestAgent.FileSystemMgmt",
    "VM.GuestAgent.FileWrite", "VM.GuestAgent.Unrestricted",
    "VM.Migrate", "VM.PowerMgmt", "VM.Snapshot", "VM.Snapshot.Rollback",
]

ROOT_PERMISSIONS = {"/": {p: 1 for p in _EVERY_PRIVILEGE}}


# ------------------------------------------------------------ fake Proxmox

class _Path:
    """proxmoxer-style chaining: api.nodes("pve").qemu(9001).config.get()."""

    def __init__(self, api, parts):
        self._api, self._parts = api, parts

    def __getattr__(self, name):
        return _Path(self._api, self._parts + [name])

    def __call__(self, *args):
        return _Path(self._api, self._parts + [str(a) for a in args])

    def get(self, **kw):
        return self._api.handle("GET", "/".join(self._parts), kw)

    def post(self, **kw):
        return self._api.handle("POST", "/".join(self._parts), kw)

    def put(self, **kw):
        return self._api.handle("PUT", "/".join(self._parts), kw)

    def delete(self, **kw):
        return self._api.handle("DELETE", "/".join(self._parts), kw)


class FakeProxmox:
    TEMPLATE = 100

    def __init__(self, zone_type="simple", dc_firewall=1, nics=None):
        self.zones = [{"zone": "pondz", "type": zone_type}]
        self.dc_firewall = dc_firewall
        self.vms = {self.TEMPLATE: dict(nics or {
            "net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0,tag=20",
            "net1": "e1000=BC:24:11:00:00:02,bridge=vmbr1,firewall=0",
        }, name="template")}
        self.firewall = {}
        self.running = set()
        self.vnets = set()
        self.next_vmid = 9001
        self.calls = []
        self.fail_on = None          # (method, path suffix) that should raise
        self.ignore_config_put = False
        self.permissions = copy.deepcopy(MINIMAL_PERMISSIONS)   # GET access/permissions
        self.permissions_error = False                            # make that call raise
        self.pools = {"pond-templates": {self.TEMPLATE}}          # pool -> its member vmids
        self.resources_error = False                              # make cluster/resources raise

    def __getattr__(self, name):
        return getattr(_Path(self, []), name)

    def handle(self, method, path, kw):
        self.calls.append((method, path, kw))
        if self.fail_on and method == self.fail_on[0] and path.endswith(self.fail_on[1]):
            raise RuntimeError(f"injected failure on {method} {path}")
        p = path.split("/")

        if path == "access/permissions":
            if self.permissions_error:
                raise RuntimeError("injected failure reading permissions")
            return self.permissions
        if path == "cluster/resources":
            if self.resources_error:
                raise RuntimeError("injected failure reading resources")
            return [{"vmid": v, "type": "qemu",
                     "pool": next((pool for pool, ids in self.pools.items() if v in ids), None)}
                    for v in sorted(self.vms)]
        if path == "cluster/sdn/zones":
            return self.zones
        if path == "cluster/firewall/options":
            return {"enable": self.dc_firewall}
        if path == "cluster/nextid":
            return str(self.next_vmid)
        if path == "cluster/sdn/vnets":
            if method == "GET":
                return [{"vnet": v} for v in sorted(self.vnets)]
            self.vnets.add(kw["vnet"])
            return None
        if path == "cluster/sdn" and method == "PUT":
            return None
        if p[:3] == ["cluster", "sdn", "vnets"] and method == "DELETE":
            self.vnets.discard(p[3])
            return None
        if p[0] == "nodes" and p[2] == "tasks":
            return {"status": "stopped", "exitstatus": "OK"}
        if p[0] == "nodes" and p[2] == "qemu":
            vmid, rest = int(p[3]), p[4:]
            if rest == ["clone"]:
                self.vms[kw["newid"]] = dict(self.vms[vmid], name=kw["name"])
                self.pools.setdefault(kw.get("pool"), set()).add(kw["newid"])
                return f"UPID:clone:{kw['newid']}"
            if rest == ["config"]:
                if method == "GET":
                    return dict(self.vms[vmid])
                if not self.ignore_config_put:
                    self.vms[vmid].update(kw)
                return None
            if rest == ["firewall", "options"]:
                if method == "GET":
                    return dict(self.firewall.get(vmid, {}))
                self.firewall[vmid] = dict(kw)
                return None
            if rest == ["status", "start"]:
                self.running.add(vmid)
                return f"UPID:start:{vmid}"
            if rest == ["status", "stop"]:
                self.running.discard(vmid)
                return f"UPID:stop:{vmid}"
            if rest == [] and method == "DELETE":
                self.vms.pop(vmid, None)
                for ids in self.pools.values():
                    ids.discard(vmid)
                self.firewall.pop(vmid, None)
                return f"UPID:delete:{vmid}"
        raise AssertionError(f"FakeProxmox has no handler for {method} {path}")