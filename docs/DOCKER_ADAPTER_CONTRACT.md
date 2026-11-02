# Docker server handoff for Redduck challenges

The Docker server and its transport are being built separately. This update
provides the Pond side only. The interface is a trusted Python object configured
through `CONTAINER_LAB_ADAPTER`, not a new public HTTP endpoint or an implementation
of Docker's API. The following is an interface sketch, not a functioning adapter:

```python
class PondDockerAdapter:
    def start(self, *, challenge_key: str, operation_key: str,
              workstation: dict) -> str:
        # Request the challenge on your server through your chosen transport.
        # Return its opaque session reference; raise on failure.
        raise NotImplementedError

    def stop(self, *, operation_key: str, session_ref: str | None) -> bool:
        # Clean up every resource associated with this operation.
        # Return True only when cleanup has been acknowledged.
        raise NotImplementedError
```

Install the real implementation as an importable module, then instantiate it in
the deployment's private `POND_SETTINGS` file, preserving the existing settings:

```python
from your_deployment_adapter import PondDockerAdapter
CONTAINER_LAB_ADAPTER = PondDockerAdapter()  # Supply your actual private config.
```

This setting takes an object, not an environment-variable string. Keep server
credentials and privileged environment values in trusted deployment/server
configuration, never in challenge manifests, student responses or VM-delivered
files. Any material delivered into a student-controlled VM is accessible to that
student; sensitive server-only values must remain on the server.

## Start

Pond first clones/starts the assigned workstation using its existing Proxmox
launcher. It then calls `start` with keyword arguments:

| Argument | Meaning |
|---|---|
| `challenge_key` | Stable manifest `docker_challenge_key`, independent of display title. |
| `operation_key` | Random `pond-...` identity committed before the external request; unique per attempt. |
| `workstation` | Dictionary with `vmid`, `node`, `instance_id`, `challenge_template_id`. |

Return a nonblank opaque string of at most 255 characters. Pond records it as
`docker_session_ref` and records status `active`. This acknowledges the operation;
Pond performs no additional challenge readiness or file verification.

The adapter/server must decide how a VMID/node maps to its delivery/network
mechanism. Pond does not supply guest IP discovery, guest login, a download
protocol, container environment variables or a netcat command. If guest startup
needs retries, the integration must handle them within bounded timeouts. The
server owns copying files into Redduck and exposing the exercise's websites and
services to the correct workstation, plus any required per-attempt isolation.

Treat `operation_key` as the idempotency identity: repeated start for the same key
must not create duplicate resource sets. Different attempts sharing a challenge
key must remain distinguishable. Persist this association server-side. Do not use
the challenge title or shared template ID as a session identity.

## Stop and uncertain requests

`stop` is called on completion, abandonment or failed start. It must be idempotent
and support lookup by `operation_key` even when `session_ref` is `None`: a start
request may have reached Docker before its response was lost.

Return literal `True` only when the server acknowledges cleanup (including an
already absent session); raise or return another value if cleanup is uncertain.
Coordinate a stop with any in-flight start for that operation so a delayed start
cannot create resources after cleanup was acknowledged. This ordering belongs in
the server/adapter contract, because the client cannot infer a lost request's fate.

Use bounded transport timeouts. Pond's current calls are synchronous; an unbounded
adapter call would occupy a web worker. Pond stores a generic error and retains
identity for operator retry; it does not expose raw adapter exceptions or server
credentials to students. Configure detailed private diagnostics in the adapter
without logging credentials.

## Stored lifecycle

`not_requested` → `requested` → `active` → `cleanup_pending` → `closed`.
A failed start also moves to `cleanup_pending`; a failed stop remains there.
The attempt's ordinary `provisioning/running/complete/abandoned` status is separate.
Keep references after closure for troubleshooting. There is no new readiness
state, background Docker reconciler or dynamic flag-generation mechanism.

Exercise this contract against the real server before rollout: separate attempts
for a shared Redduck template, lost start response, repeated stop, cleanup failure
then recovery, and delayed start racing with stop. Tests in this package simulate
the interface and do not establish real-server behavior.
