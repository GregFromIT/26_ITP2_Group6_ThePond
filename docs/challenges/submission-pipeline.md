# Challenge submission pipeline

The web route for adding challenges: a staff member uploads a challenge
definition, the platform validates it, an admin reviews it, and a worker
publishes it. This doc covers what works now, what each part needs, and
the interfaces a site has to supply before VM challenges can come in this way.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged (which doesn't change the pipeline). For the route that works today (YAML files
plus the seeder), see the [authoring guide](authoring-guide.md).

---

## What works now

| Challenge type in the manifest | Validates? | Publishes? | Launches? | Missing piece |
|---|---|---|---|---|
| `vm` / `scored_vm` with an `approved_template` source | Only with `INGESTION_VERIFY_TEMPLATE` | Only with `INGESTION_PUBLICATION_ADAPTER` | — | Both integrations (not written) |
| `vm` / `scored_vm` with an uploaded image | Only with `INGESTION_INSPECT_IMAGE` | Only with `INGESTION_PUBLICATION_ADAPTER` | — | Both integrations (not written) |
| `container_lab` | Yes | Yes (metadata only) | Only with `CONTAINER_LAB_ADAPTER` | The Docker integration (not written) |
| `offline` | Yes | Yes | Yes: no VM, the student records an attempt and submits a flag | — |

Without the integrations, a VM submission fails validation with
`CHECK_UNAVAILABLE`, "Required verification service is not configured"
(`challenge_ingestion/validation.py`). The repo provides none of the four
integrations, and none is configured on the live system. So this
pipeline can't produce a launchable VM or Red Duck challenge (known issue C2).

Our own Red Duck challenges are **not** `container_lab` challenges. They're
ordinary VM challenges on the `Kali-attacker` template, loaded by the seeder,
talking to a Docker host that's run by hand (see the
[authoring guide](authoring-guide.md), §5). The `container_lab` type was
designed for a Docker server that the platform would start and stop per
attempt. That server was never built.

---

## Who does what

| Step | Who | Where |
|---|---|---|
| Create a submission, upload files, submit | Moderators and admins (`upload_challenges`) | **Staff → Challenge submissions** (`/admin/challenge-submissions`) |
| Validate | A worker process | `python3 -m challenge_ingestion.runtime --mode validate` |
| Review: approve, reject, or return for correction | Admins only (`review_challenges`) | The submission's page |
| Publish | An admin requests it; a worker does it | The submission's page, then `--mode publish` |
| Notify the uploader | A worker | `--mode notify`; results appear under **Notifications** |

Approving doesn't publish anything: publication is a separate request. The
approving admin must still be an active `sysadmin` when publication runs, and the
content must be unchanged since approval (`publication.py`, `_approval()`).

---

## Lifecycle

```
draft ──submit──▶ validating ──▶ ready_for_review ──approve──▶ approved ──publish──▶ importing ──▶ published
                      │                │                                                │
                      ▼                ▼                                                ▼
          needs_changes /          rejected                                       import_failed
          validation_failed            │                                                │
                      └─────── corrected revision (a new submission) ◀──────────────────┘
```

The statuses come from the `ck_submission_status` constraint on
`challenge_submissions`. A correction is a new submission linked to the old one
(`supersedes_submission_id`); nothing is edited in place. Only a validated,
approved and fully prepared revision reaches the live `challenges` tables, and
it gets there in one transaction.

---

## Setting it up

1. **Quarantine storage.** Uploaded files are stored privately until published.
   Set `UPLOAD_QUARANTINE_ROOT` to a directory outside the repo and outside
   `pond-sec/app/static`. Without it, the submissions pages return
   503 "Challenge upload storage has not been configured" (`uploads.intake()`).
2. **One settings file for the web app and the workers.** Both must see the same
   database and the same quarantine. Use a `POND_SETTINGS` Python file. The
   example is `deployment/pond-settings.example.py`:

   ```python
   UPLOAD_QUARANTINE_ROOT = '/srv/pond-private/quarantine'
   SQLALCHEMY_BINDS = {'pond': 'sqlite:////srv/pond-private/the_pond.db'}
   INGESTION_VERIFY_TEMPLATE = None
   INGESTION_INSPECT_IMAGE = None
   INGESTION_PUBLICATION_ADAPTER = None
   ```

   Flask executes this file as Python, so keep it outside the repo, writable only
   by the administrator, and never accept one from an uploader
   (`pond-sec/app/__init__.py`).

   > If you set `SQLALCHEMY_BINDS` here, it moves the database for the web app,
   > the workers and the `db.*` commands that build the web app's configuration.
   > Point it at your existing `the_pond.db`, or you'll start with an empty one.

3. **Workers.** Each run does at most one job (notifications work in batches of
   up to 100), then exits:

   ```bash
   export POND_SETTINGS=/etc/the-pond/settings.py
   python3 -m challenge_ingestion.runtime --check                       # report what's configured; does no work
   python3 -m challenge_ingestion.runtime --mode validate --worker-id v1
   python3 -m challenge_ingestion.runtime --mode publish  --worker-id p1
   python3 -m challenge_ingestion.runtime --mode notify   --worker-id n1
   ```

   Nothing runs these on a schedule. Run them by hand after each upload, or set
   up a systemd timer or cron job.

   > **DECISION:** whether to schedule the workers at all, given that only
   > offline challenges get through.

Limits are fixed in `challenge_ingestion/requirements.py` and `config.py`: up to
8 VMs per challenge; images `.qcow2` only, 20 GiB each and 40 GiB per
submission; a 256 KiB manifest; and per-user and total quarantine quotas
(`UPLOAD_USER_QUOTA_BYTES`, default 80 GiB, and `UPLOAD_TOTAL_QUOTA_BYTES`,
default 200 GiB).

---

## The manifest (schema version 1)

A JSON file. The full schema is `_MANIFEST_SCHEMA` in
`challenge_ingestion/requirements.py`. Examples are in
`examples/challenge_uploads/`.

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | `1` |
| `challenge_type` | yes | `vm`, `scored_vm`, `container_lab` or `offline` |
| `title`, `description`, `instructions`, `category` | yes | Text |
| `difficulty` | yes | `beginner`, `intermediate` or `advanced` |
| `time_limit_minutes` | no | 1–1440. Stored, but not enforced by the launch code |
| `vms` | yes | At least one for `vm`/`scored_vm`; must be empty for the others. Each VM: `role`, `name`, `source`, `cpu_cores`, `memory_mb`, `disk_gb`, `boot_order`, `is_user_accessible` |
| `vms[].source` | | `{"kind": "approved_template", "reference": "…"}` or `{"kind": "image", "path": "images/….qcow2"}` |
| `network_rules` | yes | `from_role`, `to_role`, `protocol` (`tcp`/`udp`), `port`. Empty except for VM types |
| `flags` | yes | Each: `name`, `flag_hash` (SHA-256 of the trimmed, lower-cased flag), `points`. VM types also need `vm_role`. `scored_vm` needs at least one flag; `vm` must have none |
| `docker_challenge_key`, `workstation_template_id` | `container_lab` only | The Docker server's name for the challenge, and the database ID of the workstation template |

Compute a `flag_hash` exactly as the app does:

```bash
python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].strip().lower().encode()).hexdigest())" 'CTF{your_flag}'
```

---

## Interfaces a site must supply

Each is a trusted Python object or function, set in the `POND_SETTINGS` file.
None of them is a setting you can put in an environment variable.

### For VM submissions

| Setting | Signature | Must |
|---|---|---|
| `INGESTION_VERIFY_TEMPLATE` | `verify_template(vm) -> bool` | Return exactly `True` only for an approved template that's compatible with the VM spec. `False` rejects the submission; anything else counts as unavailable |
| `INGESTION_INSPECT_IMAGE` | `inspect_image(handle, dependent_vms) -> bool` | Return exactly `True` only after an isolated, time-limited inspection of the image (format, backing files, encryption, size, policy). Must not boot images or parse them on the web host |
| `INGESTION_PUBLICATION_ADAPTER` | object with `plan(manifest)`, `prepare(manifest, journal, files, storage, checkpoint, guard)`, `ready(manifest, journal, guard)` | `plan` is read-only and returns one dict per VM role. `prepare` imports and builds only the resources it owns, keyed by an operation key so it can be retried safely. `ready` checks boot, network and resources in isolation. `prepare` and `ready` return exactly `True` on success |

Sources: the docstrings of `challenge_ingestion/validation.py`
(`validate_submission()`) and `challenge_ingestion/publication.py`.

### For `container_lab` challenges

`CONTAINER_LAB_ADAPTER`, an object with two methods (`pond-sec/app/container_lab.py`):

```python
class PondDockerAdapter:
    def start(self, *, challenge_key: str, operation_key: str, workstation: dict) -> str:
        """Start this attempt's challenge; return an opaque session reference (1–255 chars)."""
    def stop(self, *, operation_key: str, session_ref: str | None) -> bool:
        """Clean up everything for this attempt; return True only once cleanup is acknowledged."""
```

- `workstation` is `{"vmid", "node", "instance_id", "challenge_template_id"}` for
  the Red Duck clone that was just started.
- That clone is on `pondnc`, not a session network, behind the same firewall as
  any lone Red Duck: it can reach only `POND_DOCKER_HOST_IP`, over TCP
  (`themes.launch()`, `_workstation_egress()`; network reference §5.2). A Docker
  server that starts a container per attempt must serve it on that address, or
  the setting must change.
- `start` and `stop` must both be **idempotent by `operation_key`**. `stop` must
  work even when `session_ref` is `None`, because a start request can reach the
  server and then lose its reply.
- Use bounded timeouts. The calls run inside a web request.
- Keep the server's own credentials in the adapter, never in a manifest or
  anything copied into a student's VM.

Pond records the Docker side of each attempt in `challenge_instances.docker_status`:
`not_requested` → `requested` → `active` → `cleanup_pending` → `closed`. A failed
stop stays at `cleanup_pending`. Retry it for a closed attempt with:

```bash
python3 -m db.cleanup_docker_session --instance-id <id>
```

A `container_lab` manifest's `workstation_template_id` is the database ID of the
Red Duck template. Register it, and get the ID, with:

```bash
python3 -m db.register_template --name Kali-attacker --vmid 10001 --node pve
```

This records metadata only. It doesn't create or inspect the VM. Repeating it
returns the same ID.

---

## Related commands

| Command | Use it for |
|---|---|
| `python3 -m db.migrate_container_labs --database the_pond.db --check` (then `--apply --backup <path> --confirm-existing-vm`) | Only for a database created **before** the 2 Oct 2026 container-lab change. It adds `execution_type`, `challenge_templates` and the related columns. A database made by `db.init_database` today already has them |
| `python3 -m challenge_ingestion.runtime --check` | Shows which integrations are configured |

The step-by-step build notes for this pipeline (steps 1–14, written while it was
built) are kept in [`docs/archive/challenge-upload-steps/`](../archive/challenge-upload-steps/)
for history. They describe applying each change one at a time, which no longer
applies.
