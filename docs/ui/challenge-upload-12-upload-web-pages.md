# Challenge uploads — step 12: upload, status and inbox pages

Requires steps 1–11, their dependencies, and initialized database tables.
This connects authenticated web pages to intake, validation jobs and notifications.
It does not approve or publish challenges. Real template verification and isolated
image inspection adapters remain required for validation to pass.

## Apply the update

Add the new files:

- `challenge_ingestion/intake.py`
- `pond-sec/app/uploads.py`
- `pond-sec/app/templates/admin/submissions.html`
- `pond-sec/app/templates/admin/submission_upload.html`
- `pond-sec/app/templates/admin/submission_detail.html`
- `pond-sec/app/templates/admin/upload_error.html`
- `pond-sec/app/templates/notifications.html`
- `pond-sec/app/static/js/uploads.js`
- `tests/test_upload_routes.py`

Merge the included updates to existing files:

- `pond-sec/app/__init__.py`: register request limits before CSRF and register the upload blueprint.
- `pond-sec/app/config.py`: dedicated upload/storage settings.
- `pond-sec/app/roles.py`: upload and review capabilities.
- `pond-sec/app/templates/base.html`: notification navigation.
- `pond-sec/app/templates/admin/console.html`: submission navigation.

The supplied versions of existing files are based on the original ZIP plus these
steps. Preserve unrelated local edits when merging. No new database model import,
schema change or dependency is introduced. Keep the step-seven ingestion dependency
installed; the web app now imports its schema validator.

## Configure the web server

Before restarting the application, set:

```bash
export UPLOAD_QUARANTINE_ROOT=/absolute/private/quarantine
```

Use the same directory for the validation worker. Its parent must exist; the
application creates the final directory with private permissions. The service user
must own it. Do not use the public static directory. Intake requires Linux/POSIX
file-lock and storage support. Ordinary web pages can still import on other hosts,
but uploads need a supported deployment.

Optional server-controlled limits:

| Setting | Default |
|---|---|
| `UPLOAD_MAX_IMAGE_BYTES` | 20 GiB; cannot exceed the package policy ceiling |
| `UPLOAD_USER_QUOTA_BYTES` | 80 GiB of recorded files across an uploader's revisions |
| `UPLOAD_TOTAL_QUOTA_BYTES` | 200 GiB of recorded files across the database |

The package's 40 GiB per-submission limit also applies. Existing revision copies
count towards quotas. A free-space check preserves a 512 MiB margin. Each user
may have at most 20 drafts. These are initial operator defaults, not cluster
capacity measurements. Changing environment settings requires an app restart.

The normal 64 KB request limit remains in place for other routes. Definition-file
requests are bounded to the manifest limit plus small multipart overhead. Images
use raw `application/octet-stream` requests with `Content-Length` and a CSRF header;
the browser sends the file directly and displays progress. Multipart image uploads
are rejected before form parsing. Reverse-proxy/body-buffering/timeouts and OS disk
quotas must be configured separately for large transfers.

## User flow

1. Sign in as a moderator (`manager` in the database) or sysadmin.
2. Open **Staff → Challenge submissions → New submission**.
3. Choose a challenge definition JSON file matching the supplied examples.
4. Upload each listed QCOW2 image. Approved-template definitions need no images.
5. Optionally replace the definition or remove/replace draft files.
6. Select **Submit for validation**. The server freezes the manifest/inventory
   digest and creates one validation job in the same transaction. Repeated clicks
   do not create duplicate jobs for that submission.
7. Run the validation worker from step ten and notification delivery from step
   eleven (or use your deployment runner). The web request does not start them.
8. Refresh the submission page for status, findings and run history. Notifications
   appear in **Notifications** once delivered; the recipient can mark them read.
9. If changes are needed, choose **Create correction**. A new editable draft copies
   existing files into separate objects and links to the retained original.

Missing files may be submitted so the validator can record actionable findings.
Structural manifest errors are rejected before creating the draft. Revising a
technical failure can also be used once administrators have restored verification
services. Approved, published or currently validating submissions are not editable.

## Access and data protection

- Moderators access their own submissions; sysadmins can view and manage all.
  Students cannot access submission routes. Inactive accounts are rejected.
- The uploader and notification actor IDs come from the logged-in session, not
  browser parameters. Submission links are independently authorized.
- All mutations use existing CSRF protection. Raw uploads use `X-CSRF-Token`.
- No full manifest, flag hash or private storage key is exposed by the status API.
- Templates escape titles, filenames, issue text and notification messages.
- Read acknowledgements cannot mark another recipient's notification read and do
  not resolve issues or affect challenge approval.
- There are no direct image-download routes and no routes that grant approval or
  publication in this update.

The `review_challenges` permission currently permits sysadmin access to all
submissions; actual review actions will be added separately.

## Quota and failure behaviour

A private filesystem lock serializes intake changes across processes using the
same quarantine directory. It is held during streaming/copying, but no database
writer transaction is held during those operations. Concurrent intake attempts
receive a busy response and can retry. This is deliberately a single-host,
one-upload-at-a-time design, not distributed or resumable storage.

Quota totals are checked while this lock is held and file records are committed
before it is released. All other intake writers must use the same service/root;
direct database/storage writes bypass this coordination. OS quotas and orphan
reconciliation are still necessary for crashes or unrelated disk usage.

Failed or incomplete streams are removed. If file-row insertion fails, the new
object is removed. A lost HTTP response may follow a successful save; refresh
before retrying. Draft deletion commits record removal before unlinking the object,
so a subsequent filesystem failure can leave an orphan for operator reconciliation.
Process crashes can also leave unreferenced objects; no automatic sweeper is added.
Old immutable revisions are never automatically wiped.

## Verification

```bash
python3 -m pytest tests/test_upload_routes.py -q
```

18 new integration tests passed. They exercise the real Flask factory, HTML
rendering, authenticated routes, CSRF, ownership, inactive accounts, byte/quota
limits, lock contention, cleanup after insert failure, independent correction
copies and upload → worker → notification flow with fake inspection.

JavaScript syntax was checked. Full browser interaction, real multi-gigabyte
transfers, proxy behaviour, actual VM inspection/booting and deployment remain
unverified. The legacy `pond-sec/tests/test_flow.py` references older app modules
and was not used as the test harness for these updates. Existing timestamp
deprecation warnings remain in the user model.

No deployed database, web server, worker or external messaging service was changed
or started by this step. Restart your application after merging and configuring.

Next: administrator review/approval controls, followed by controlled publication
and the concrete verification/import adapters required for real challenges.
