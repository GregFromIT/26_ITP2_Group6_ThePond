# Challenge uploads — step 3: background job records

Requires steps 1 and 2. This adds a model, not the worker itself.

## Apply

1. Add `db/submission_job_models.py` and `tests/test_submission_job_models.py`
   to the matching directories of your repository.
2. Retain the earlier model imports and add to `db/__init__.py`:

   ```python
   from db.submission_job_models import SubmissionJob
   ```

The included `db/__init__.py` matches the supplied repository plus steps 1–3.
If you have other local changes, add the import instead of overwriting it.
The earlier submission and file models are unchanged.

No live database has been changed. Importing the model registers metadata but
does not create its table. Initialization will be integrated once all models
are ready. The old initializer also performs Proxmox seeding; do not run it
solely to apply this model.

## What the table records

| Fields | Purpose |
|---|---|
| `job_id`, `submission_id`, `requested_by_user_id` | Identify the job, submission and authenticated requester |
| `action` | `validate`, `publish` or `cleanup` |
| `status` | `queued`, `running`, `retry_wait`, `succeeded`, `failed` or `cancelled` |
| `idempotency_key` | Unique server-controlled identifier for one logical request |
| `attempt_count`, `max_attempts`, `available_at` | Retry count, permitted attempts and next eligible run time |
| `locked_by`, `lease_token`, `lease_expires_at`, `heartbeat_at` | Worker ownership and recovery information |
| `resource_inventory_json` | Reserved/created VM resources, ownership information and external task IDs |
| `error_code`, `error_message` | Safe failure details; exclude credentials and flags |
| `created_at`, `started_at`, `completed_at` | UTC lifecycle timestamps |

The partial unique index permits only one active job per submission across all
actions. Queued, running and retry-wait jobs count as active. Terminal jobs stay
in the database, allowing a new job without erasing history. An expired lease
does not remove this constraint: the worker must reclaim or finish the old job.

Repeated requests must reuse the same idempotency key. Retries of one job update
that record, incrementing attempts and retaining resource recovery information.
A genuinely new validation run gets a new job/key. The application must authorize
requests and check the submission's workflow state before queuing work.

The `(submission_id, job_id)` unique constraint supports the issue table's
forthcoming composite foreign key, preventing issues from linking to jobs
belonging to another submission. This is why jobs were implemented before issues.

## Responsibilities reserved for the worker

- Atomically claim eligible work and issue a new lease token per claim.
- Check current lease ownership when updating jobs; stale workers must not
  overwrite a newer worker's results.
- Enforce attempt limits, timestamps, backoff and allowed state transitions.
- Reconcile external Proxmox state before retrying an uncertain operation.
  A database lock cannot undo external work or by itself guarantee exactly-once
  execution. The unique request key only prevents duplicate database records.
- Record resource reservations and ownership before external work, then record
  external task IDs and outcomes. Clean up only resources this import owns.
- Replace the whole `resource_inventory_json` list when changing it; nested
  in-place JSON edits are not tracked automatically.
- Run validation first and queue publication after validation and approval.
  The active-job index deliberately prevents queuing both at once.

This model does not enforce lease freshness, resource inventory contents or the
submission's approval state. Those require worker/application logic. SQLite is
the current target; partial index configuration must be reviewed before changing
database engines. It is separate from existing `instance_jobs`, which manage
student runtime instances.

## Verification

From the repository root:

```bash
python3 -m pytest tests/test_submission_models.py tests/test_submission_file_models.py tests/test_submission_job_models.py -q
```

52 tests passed (21 new job tests), using temporary in-memory databases with
Flask-SQLAlchemy 3.1.1 and SQLAlchemy 2.0.51. Existing user model defaults emit
`datetime.utcnow()` deprecation warnings. Worker concurrency and Proxmox
operations are not implemented or tested by this step.

Next: `db/submission_issue_models.py`.
