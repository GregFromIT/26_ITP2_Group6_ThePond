# Challenge uploads — step 13: administrator review

Requires steps 1–12. This adds review controls but does not queue publication,
import VMs or make a challenge available to students.

## Apply

Add:

- `challenge_ingestion/review.py`
- `tests/test_submission_review.py`

Merge the updated versions of:

- `challenge_ingestion/worker.py`
- `pond-sec/app/uploads.py`
- `pond-sec/app/templates/admin/submission_detail.html`

No database migration, new model import, environment setting or dependency is
required. Restart the web app and validation worker after merging. The worker
update is required: it records the validated content digest in the existing
validation audit entry, which approval now checks.

## Review flow

1. Sign in as a system administrator and open a completed submission.
2. Review its description, student instructions, VM sources/resources, requested
   connections, scoring summary and latest validation findings.
3. For a `ready_for_review` submission, select **Approve revision**, optionally
   including a note. This sets `approved`, the authenticated administrator ID and
   approval timestamp, records an audit entry, and queues an uploader notification.
4. Alternatively select **Reject with note**. A nonblank explanation is required.
   Rejection is also available for completed `needs_changes` or `validation_failed`
   submissions. The uploader can read the note and create a corrected revision.

No approval button is available for failed validation. Running or incomplete
validation cannot be reviewed. The review controls do not execute image checks
or bypass missing verification adapters.

The current policy permits an active sysadmin to review their own submission.
It does not introduce a mandatory second administrator. Moderators and students
cannot review even if they supply an administrator ID in a request.

## Approval requirements

The service checks all of the following inside one short SQLite write transaction:

- The actor is currently active and has the database `sysadmin` role.
- The form references the current digest and latest validation job.
- No validation, publication or cleanup job is active for that submission.
- Stored metadata and file inventory still reproduce the frozen digest, and the
  submission's type/version match its manifest.
- The submission is `ready_for_review`, with a succeeded, completed validation job.
- The latest validation job has no findings, even findings manually marked resolved.
  Run validation again after a correction; do not manually suppress findings.
- Every uploaded file is marked passed with a validation timestamp.
- The latest validation audit entry records a successful result, no findings and
  this exact job/digest combination.

Older jobs' findings do not block a genuinely newer successful validation run.
The hidden digest/job fields are stale-form checks, not authorization tokens.
All state-changing web requests continue to require CSRF protection.

Approval checks persisted metadata/inventory rather than rereading multi-gigabyte
images in the HTTP request. Publication must rehash the actual stored bytes,
recheck template availability and enforce current infrastructure policy before
importing or exposing anything. Review approval alone is not a VM safety claim.

## History, notifications and repeated requests

Review author, decision, note, job and digest are stored in existing `audit_logs`.
The submission page displays review history to its authorized owner and sysadmins;
it does not expose flag hashes or the full manifest. Text is escaped. Write notes
as actionable feedback; do not include credentials or answer secrets.

Submission state, audit entry and outbox record are committed together. An insert
failure rolls all three back. Delivery still uses the step-eleven notification
service; no external messages are sent.

Repeating the same decision preserves its first author, note and timestamp and
does not duplicate notifications. Concurrent approvals result in one stored
decision. This version does not provide reversal/revocation or note editing after
a decision; corrections use a new submission revision. Approved submissions remain
frozen and await the separate publication implementation.

## Submissions validated before this update

Earlier worker audit entries lack the successful digest evidence now required for
approval. Those entries are not silently trusted or backfilled. If an old submission
is already `ready_for_review`, an administrator can reject it with a clear note
requesting revalidation, and its owner can create a correction and submit that
revision to the updated worker. Retain the original history. No direct database
editing is needed or recommended.

## Verification

```bash
python3 -m pytest tests/test_submission_review.py -q
```

19 new review tests cover role checks, inactive accounts, CSRF, actor spoofing,
stale forms, changed metadata, missing validation evidence, unpassed files,
current versus historical findings, rejection notes, atomic rollback, duplicate
decisions and concurrent approvals. They use temporary databases, real Flask
routes and fake image-inspection callbacks. No live approval, real VM scan/import
or external notification was performed. Existing user-model timestamp warnings
remain.

Next: controlled publication of approved submissions, including rechecks,
resource ownership/recovery and explicit Proxmox integration. Concrete approved-
template verification and isolated image inspection still need deployment adapters.
