# noVNC console — getting it running

Status as of this session: implemented but **not yet verified against a real
Proxmox host** (developed offline, no network path to `10.1.21.151:8006`).
This is the checklist to actually prove it works once you're back on-site.

## 1. Install deps

`requirements.txt` now includes `flask-sock` and `websocket-client` on top
of what was already there:

```
pip install -r requirements.txt
```

## 2. Environment

```
export THEPOND_PROXMOX_TOKEN_SECRET=<real proxmox api token secret>
```

Required — `provisioner.get_client()` raises immediately without it.
Node/host/user/token-id come from `group_vars/all.yml` (currently
`10.1.21.151`, `root@pam`, token id `root`).

Optional — override the sqlite path (defaults to `db/thepond.db`):

```
export THEPOND_DB_PATH=/path/to/thepond.db
```

## 3. Run on port 5001 (separate from the main app)

`debugger-app.py` now reads `PORT` (defaults to 5000 if unset, same as
before):

```
PORT=5001 python debugger-app.py
```

Watch the startup output — before the server binds, it lists LXC
containers and QEMU VMs on the configured node as a live connectivity
check. If that hangs/times out, the app never reaches `app.run()` and
port 5001 never opens. That's your first signal: no console testing is
possible until this step succeeds.

## 4. Get a real session_id

`session_id` in `/console/<session_id>` is the instance's **vmid**, not a
separate session table (there is no `sessions.db` / `session_id` column —
confirmed against `db/schema.sql`). Either:

- create an instance through the normal flow (`/create` on the main app,
  or `cli.py`) and use its vmid, or
- confirm VMID 1000 already has a row in `instances` via
  `InstanceStore().get_by_vmid(1000)`.

If there's no matching row, `/console/<id>/ws` closes immediately with
code 1008 ("no such session") — that's expected, not a bug.

## 5. Open the console

```
http://<host>:5001/console/<vmid>
```

Check in order:

1. **Page loads** — `console.html` renders, noVNC's `RFB` module import
   resolves from `/static/novnc/core/rfb.js` (confirms the submodule is
   checked out — `git submodule status` should show `v1.5.0`, not `-` for
   uninitialized).
2. **WebSocket upgrade** — browser dev tools Network tab, `ws` entry for
   `/console/<id>/ws` should show status 101.
3. **Upstream auth** — watch the Flask process's stdout/stderr. The relay
   calls Proxmox's `vncwebsocket` endpoint with an `Authorization:
   PVEAPIToken=...` header (same style as the REST client). If Proxmox
   rejects it, look for `"does not look like a valid user name"` in the
   error — that specific message means the websocket upgrade path doesn't
   accept token-header auth the way the REST API does, and the fix is to
   switch that one call to ticket/cookie auth instead (grab a
   `PVEAuthCookie` via a normal login call and pass it as a `Cookie:`
   header, rather than the API token header). Report back if you see this.
4. **Canvas renders actual output** — the noVNC canvas should show the
   VM's real screen, not just a black/blank box.
5. **Cleanup on tab close** — closing the browser tab should end the
   `ws.receive()` loop server-side, which closes `upstream` in the
   `finally` block, which ends the reader thread when `upstream.recv()`
   next raises. Confirm by checking the Proxmox side (or server logs) that
   the vncproxy connection actually drops — not just that the browser tab
   closed.

## Known gaps / things not yet exercised

- The auth-header-vs-cookie question in step 5.3 above — untested,
  flagged as the most likely real failure point.
- No test yet for what happens if the VM is stopped/doesn't exist when
  `get_console_ticket` is called (proxmoxer will raise; today that's an
  unhandled exception in the websocket handler, not a graceful ws close).
