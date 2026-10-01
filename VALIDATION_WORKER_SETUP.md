# Challenge uploads — step 10: validation worker

Requires steps 1–9 and initialized database tables. This step connects the
validator to jobs, issues, file states, audit records and the notification outbox.
It does not implement uploading, approval, VM publication or notification delivery.

## Apply

Add `challenge_ingestion/worker.py` and `tests/test_validation_worker.py` to the
matching repository directories. No new dependencies or model imports are needed.

The worker targets the current SQLite deployment and uses its own short-lived
sessions. Run it as a separate service process; do not call it inside an
uncommitted upload transaction. The quarantine directory and configured database
must be the same ones used by the upload service.

## Processing one job

From the repository root, in the application's environment:

```bash
python3 -m challenge_ingestion.worker --storage-root /absolute/private/quarantine --worker-id validator-1
```

Replace the example path with the real server-controlled location. This invocation
processes one eligible validation job and exits; it is not a continuous daemon.
Each invocation can also finalize exhausted expired jobs before selecting work.
A deployment runner must invoke it repeatedly to drain the queue. No automation
or deployed process was started by this task.

The CLI loads the normal web application factory and its configured `pond`
database. It looks for trusted Python callable settings:

- `INGESTION_VERIFY_TEMPLATE`: the approved-template verification adapter.
- `INGESTION_INSPECT_IMAGE`: the isolated image-inspection adapter.

These are callable objects installed by trusted application bootstrap code, not
strings imported from a manifest or browser request. The standard factory does
not yet configure them. Alternatively, a trusted worker bootstrap can instantiate
`ValidationWorker(engine, storage, worker_id=..., verify_template=...,
inspect_image=...)` directly. Missing adapters fail validation and trigger bounded
retries; never install an always-true callback in production. Configure adapter
timeouts and tenant/user authorization context when integrating the real services.

## Queue preconditions

The upload service (still to be built) must authorize the requester, complete
uploads, freeze the metadata/inventory and digest, then commit:

- The submission with status `validating` and its finalized digest/timestamp.
- A `SubmissionJob` with action `validate`, status `queued`, an eligible
  `available_at`, and a server-generated idempotency key.

These should be committed in the same transaction. The worker is an internal
trusted process, not an authorization boundary or a public enqueue endpoint.
Do not accept requester IDs, callback functions or job state from browser input.

## Job handling

- Uses SQLite `BEGIN IMMEDIATE` for short claim/result transactions so competing
  workers cannot both claim one eligible job. No writer lock is held while
  inspecting files or contacting verification services.
- Increments attempts when a claim starts and renews its lease on a heartbeat
  thread. Defaults: 120-second lease and 60-second retry delay.
- Reclaims expired validation jobs with a new token while attempts remain.
  Expired final attempts become terminal failures with an issue and notification.
- Checks the current token and lease expiry before saving results. A stale
  worker cannot overwrite a newer worker's findings or state. Adapters still
  need resource limits/timeouts: lease checks cannot forcibly cancel a hung parser.
- Rechecks the metadata and file-inventory snapshot before committing. Changes
  during validation produce `CONTENT_CHANGED`, not an accepted stale result.
- Cancels queued validation work whose submission is no longer `validating`.
- Ignores `publish` and `cleanup` jobs; their workers are not implemented here.

## Results

| Result | Job | Submission | Notification |
|---|---|---|---|
| All checks pass | `succeeded` | `ready_for_review` | Queued |
| Content needs fixing | `succeeded` | `needs_changes` | Queued |
| Technical failure, attempts remain | `retry_wait` | `validating` | None yet |
| Technical failure, attempts exhausted | `failed` | `validation_failed` | Queued |

`succeeded` means the validation job completed; it does not mean the challenge
is approved or published. Findings replace only the same job's previous retry
findings. Other jobs' issue history stays intact. Audit records retain per-attempt
status and issue counts, not full sensitive error details.

Issues, file-state updates, submission/job state, audit entry and any terminal
notification are committed together. A failure rolls them all back. Notification
keys are stable per job/recipient, and notification status starts `pending`.
This step queues notifications; it does not display or send them.

Only a fully passing report marks all files passed. On failed reports, explicit
content findings mark affected files failed; unchecked or unavailable checks
remain pending. Missing-file issues have no file reference. No files are wiped.

Database/snapshot failures can leave a running claim until expiry; the next
worker invocation recovers it under the attempt limit. Watch process failures
and job states operationally. The worker does not silently treat those errors as
success. Production supervision and alerting are still deployment work.

## Verification

```bash
python3 -m pytest tests/test_validation_worker.py -q
```

16 worker integration tests passed. The earlier combined suite passed 232 tests
before two additional worker checks were added; the final targeted worker suite
passed all 16, giving 234 tested cases across these updates. Tests cover competing
workers, lease replacement/renewal, background heartbeat, retries/exhaustion,
snapshot changes, rollback on notification failure, preservation of prior issue
history, file validation states and ignoring publication jobs.

All tests use temporary SQLite databases/storage and fake verification adapters.
No real scan, template lookup, VM import, external message or deployed worker was
run. Existing user-model `datetime.utcnow()` warnings remain.

Next: notification delivery service, to make queued in-app notifications available
to the future inbox without duplicate delivery.
