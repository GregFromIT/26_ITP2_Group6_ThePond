# Challenge uploads — step 4: validation issues

Requires steps 1–3. This model stores findings; it does not run validation or
notify uploaders. No live database has been changed.

## Apply

1. Add `db/submission_issue_models.py` and `tests/test_submission_issue_models.py`
   to the matching directories of your repository.
2. Retain the earlier imports and add to `db/__init__.py`:

   ```python
   from db.submission_issue_models import SubmissionIssue
   ```

The supplied `db/__init__.py` matches the original repository plus steps 1–4.
If yours has other changes, add only the new import. Earlier models are unchanged.
Registration alone does not create a table. Initialization will follow once all
models are ready; do not run the older Proxmox-seeding initializer just for this.

## Fields

| Field | Purpose |
|---|---|
| `issue_id` | Unique finding identifier |
| `submission_id` | Submission revision that was checked |
| `job_id` | Job that produced the finding; required |
| `file_id` | Optional related file; absent for missing-file or general metadata problems |
| `severity` | `error`, `warning` or `info` |
| `error_code` | Stable application code such as `MISSING_IMAGE` |
| `field_path` | Optional field identifier such as `vms.target.image` |
| `message` | Safe, actionable explanation for the uploader |
| `resolution_note` | Explanation of how the finding was resolved |
| `resolved_by_user_id` | Optional human resolver |
| `resolved_at` | When resolution was confirmed; null means unresolved |
| `created_at` | UTC creation time |

Composite foreign keys ensure any linked job and file belong to the issue's
submission. Missing files need no placeholder file rows. The referenced
submission, job, file and human resolver cannot be deleted while these links
exist. Records remain after resolution; nothing is automatically wiped.

`issue.submission` and `issue.resolved_by` are normal ORM relationships.
`issue.job` and `issue.file` are read-only navigation relationships to avoid
conflicting writes to the shared submission ID. Create findings by explicitly
setting `submission_id`, `job_id` and optional `file_id`; assigning a read-only
relationship does not persist those IDs. Expire/refresh loaded navigation
relationships after changing IDs.

## Example finding

```python
issue = SubmissionIssue(
    submission_id=submission.submission_id,
    job_id=validation_job.job_id,
    severity="error",
    error_code="MISSING_IMAGE",
    field_path="vms.target.image",
    message="Upload an accepted VM image for the target machine.",
)
db.session.add(issue)
```

The validator should create this only after the relevant check. No `file_id` is
supplied because the expected file does not exist yet.

## Application responsibilities

- Distinguish a completed validation with no blocking findings from one that
  has not run or has failed. No issues alone does not mean ready to publish.
- Use the applicable validation run for publication decisions; old run history
  must not incorrectly block or approve a newer run.
- Deduplicate or replace findings within a retried job transaction. Separate
  runs use separate jobs and keep their earlier findings. This table deliberately
  has no uniqueness rule on error codes: multiple fields can have the same error.
- A correction creates a new submission revision. Findings on the old revision
  keep their old file/job references; do not relink them to replacement files.
  The service can record a resolution note identifying the verified correction.
- Only mark a finding resolved after revalidation or authorized review. A matching
  filename is insufficient. Automated resolution may have no human resolver;
  service code must consistently set resolution timestamps and audit the event.
- Validate error codes and field identifiers, enforce permissions, escape messages
  when displaying them and omit credentials/flags from messages and notes.

## Verification

From the repository root:

```bash
python3 -m pytest tests/test_submission_models.py tests/test_submission_file_models.py tests/test_submission_job_models.py tests/test_submission_issue_models.py -q
```

75 tests passed (23 new issue tests) with Flask-SQLAlchemy 3.1.1 and SQLAlchemy
2.0.51, using temporary in-memory databases. Existing user defaults still emit
`datetime.utcnow()` warnings. Validation, resolution authorization, notifications
and Proxmox operations are not implemented or tested in this step.

Next: `db/notification_models.py`, the last planned new database model file.
