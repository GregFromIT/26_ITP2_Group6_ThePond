# Controlled publication and worker configuration

This completes the database/web orchestration for approved uploads. It does not
include a working Proxmox image importer, isolated image inspection service or
approved-template catalog. Those deployment integrations remain required before
real uploads can pass validation and publish. The test adapter is never installed
in the application. There have been no live infrastructure changes.

## Publication behavior

An active sysadmin queues an approved, unchanged revision. The worker verifies its
approval evidence and revalidates its manifest, stored bytes and source checks.
It persists a resource plan before asking the deployment adapter to prepare VMs.
After isolated readiness checks, it revalidates again and commits the challenge,
VM templates, flag hashes, network rules, publication link, audit entry and
notification together. A database failure rolls back the live records.

Repeated publication requests return the same job. Failed external preparation
retains the resource journal and sets `import_failed`. It never blindly deletes
VMs or starts a new import with a new identity. After stopping the previous worker
and reconciling pending tasks/resources, an administrator can explicitly retry the
same job, within its attempt limit. Expired leases require this same recovery.
A database lease cannot cancel an in-flight Proxmox task: the adapter must reconcile
it before continuing. The journal is not a distributed transaction with Proxmox.

The existing `vm_templates.proxmox_template_vmid` uniqueness constraint is
preserved: the same template VMID cannot be assigned to multiple live challenge
records. Reservations in other publication journals also block reuse. Do not
manually clear reservations while external tasks or owned resources may remain.

## Trusted adapter contract

Supply an object as `INGESTION_PUBLICATION_ADAPTER` in the administrator-owned
Python settings file. All three methods are required:

- `plan(manifest)`: read-only; return one dictionary per VM with exactly `role`,
  `node`, `vmid`, `template_name`, `owned`. IDs must be integers 100–999999999,
  unique in the plan and available on the cluster. `owned` is true for image
  imports, false for catalog templates. Use a deployment-reserved VMID range and
  enforce ownership at the hypervisor; do not overwrite an existing unrelated VM.
- `prepare(manifest, journal, files, storage, checkpoint, guard)`: reconcile each
  journal `operation_key` with external resources and pending tasks before doing
  work. `files` contains immutable FileRecords mapping logical paths to storage
  keys and hashes. Read images using `storage.open(record.storage_key)`; never
  construct disk paths or shell commands from uploaded filenames. Verify the
  actual imported bytes against the approved record. Use `guard()` before every
  external mutation and while polling. Persist task IDs and progress using
  `checkpoint(role, state='importing', task_id='...')`, then state `ready` only
  after preparation succeeds. Return exactly `True` for success.
- `ready(manifest, journal, guard)`: perform deployment-specific isolated boot,
  disk/resource and network policy checks. Confirm all temporary test resources
  are cleaned up before returning exactly `True`. Never boot an untrusted VM on
  a management or production network. Do not delete catalog templates.

The worker adds stable `operation_key`, `state`, `task_id` to the plan. Retry uses
the same stored plan; it does not call `plan` again. Callbacks must have bounded
execution time, poll task completion, enforce current infrastructure policy and
stop on lease loss. Exceptions/false returns stop publication. Safe generic error
codes are stored; trusted adapters should record actionable operational errors in
restricted logs without credentials, flags or uploaded secrets.

Also configure `INGESTION_VERIFY_TEMPLATE(vm)` and
`INGESTION_INSPECT_IMAGE(open_handle, dependent_vms)`. Exactly `True` means passed;
False rejects the source and unavailable checks block success. Magic bytes alone
are not an image safety check. An actual inspector must enforce QCOW2 structure,
backing-file/external-data restrictions, virtual size and the site's isolated
inspection policy. Reuse of existing clone-only provisioner code does not implement
this contract.

## Configuration and execution

Copy `deployment/pond-settings.example.py` to a private administrator-owned location
and configure callbacks, database and storage. Set `POND_SETTINGS` to its absolute
path for BOTH web and workers. This file executes trusted Python; never let an
uploader edit it. Web test-config overrides still take precedence.

From the repository root with application dependencies installed:

```bash
python -m challenge_ingestion.runtime --check
python -m challenge_ingestion.runtime --mode validate --worker-id validation-1
python -m challenge_ingestion.runtime --mode publish --worker-id publication-1
python -m challenge_ingestion.runtime --mode notify --worker-id notification-1
```

Each command processes bounded work then exits. Run them periodically under the
site's service supervisor; use separate validation/publication/notification workers
so slow imports do not delay messages. `--mode all` runs one of each in sequence.
`--check` reports callback configuration only, not infrastructure health. No
service/timer is automatically installed. Missing publication configuration is
reported and skipped in all-mode; explicit publish-mode fails.

## Recovery and retention

Inspect the administrator-only recovery panel and pending hypervisor tasks. Stop
the old worker before retrying, confirm each VM's operation ownership, and verify
which imports or readiness cleanup completed. Check the reconciliation checkbox
only after this work. The adapter must still enforce idempotence on retry. If the package itself needs
changing, use **Return for correction** with a note after the same reconciliation.
This retains the failed job and resource reservations, clears approval, and lets
the uploader create a fresh draft; it does not delete hypervisor resources.

Submissions, issues, jobs and audit records remain as history. Images are retained
for diagnosis and corrected revisions; quotas bound further intake. Automatic
retention/deletion is not implemented. Back up database and private file storage
consistently; do not wipe whole staging tables or remove files used by a running
job. Choose a retention policy before long-term operation and monitor disk space.

## Verification scope

Tests cover successful atomic publication, repeated/concurrent requests, role and
CSRF checks, stale content, corrupted stored bytes, invalid plans, conflicting VM
IDs, missing approval evidence, database rollback, readiness failures, recovery
journal reuse and stale workers. A complete test goes from a missing-file upload
through correction, validation, review, publication and in-app notification.
These use temporary SQLite databases and a fake adapter, not real VM boots.
