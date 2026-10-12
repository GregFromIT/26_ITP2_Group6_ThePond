# Challenge uploads — step 5: notification outbox

Requires steps 1–4. This is the final planned new database model file. It records
notifications but does not deliver them, send email or provide an inbox screen.

## Apply

1. Add `db/notification_models.py` and `tests/test_notification_models.py` to the
   matching repository directories.
2. Retain earlier model imports and add to `db/__init__.py`:

   ```python
   from db.notification_models import NotificationOutbox
   ```

The included `db/__init__.py` matches the original repository plus steps 1–5.
If yours has other changes, add the import instead of overwriting that file.
Earlier model files are unchanged. No live database has been modified.
Importing models only registers metadata; table initialization is the next step.
Do not run the old initializer just for this update: it also seeds challenges
using Proxmox.

## Fields

| Fields | Purpose |
|---|---|
| `notification_id`, `submission_id`, `recipient_user_id` | Identify the record, submission and intended recipient |
| `event_type` | Application-defined event such as `needs_changes` or `published` |
| `deduplication_key` | Unique identity for the event occurrence, recipient and channel |
| `channel` | `in_app` only in this version |
| `message` | Safe user-facing summary, with details available on the submission page |
| `status` | `pending`, `delivering`, `sent` or `failed` |
| `attempt_count`, `available_at` | Delivery attempt count and next eligible attempt time |
| `lease_token`, `lease_expires_at` | Claim ownership for delivery/recovery |
| `sent_at` | Time the notification became available in the application |
| `read_at` | Time the recipient acknowledged/read it |
| `last_error` | Safe diagnostic summary for a failed attempt |
| `created_at` | UTC creation time |

The database rejects unsupported channels/statuses, negative attempt counts,
unknown recipients/submissions, duplicate keys, delivery claims without lease
details, and sent records without a sent timestamp. Referenced recipients and
submissions cannot be deleted while notification records link to them.

## Intended delivery flow

1. In the transaction that stores validation/publication results, insert the
   outbox record. Do not commit it separately from the result it describes.
2. Use a server-generated key for the particular event occurrence, recipient and
   channel. Retries reuse that key. A new validation run or new event gets a new
   key, so later legitimate notifications are not suppressed.
3. The delivery service claims an eligible pending record atomically, sets a
   unique lease token and expiry, and increments the attempt count.
4. For in-app delivery, mark it `sent` and set `sent_at`; the inbox shows only sent
   records for the authenticated recipient. Clear the lease after completion.
5. A failed attempt retains the same row. Service policy decides retry limits,
   backoff and whether to requeue it. Reclaim expired delivery leases deliberately.
6. Only the authorized recipient may mark it read. Reading sets `read_at`; it does
   not resolve submission issues or delete their history.

## Service responsibilities

Permissions, event-type validation, recipient selection, maximum attempts,
lease freshness, allowed transitions and timestamp consistency remain in the
future service. In particular, it must refuse reads of unsent notifications,
prevent cross-user inbox access and stale worker updates, and preserve delivered
history. The model's checks are not a delivery or authorization implementation.

Use authenticated identities and trusted submission records when selecting
recipients, not recipient IDs supplied directly by a browser. Notification
messages must not contain credentials, flags or full manifests. Escape text
when displaying it. No external messages were sent during this work.

Email requires a later schema/service change and configured recipient addresses;
the current database explicitly rejects `channel='email'`.

## Verification

From the repository root:

```bash
python3 -m pytest tests/test_submission_models.py tests/test_submission_file_models.py tests/test_submission_job_models.py tests/test_submission_issue_models.py tests/test_notification_models.py -q
```

96 tests passed across all five models (21 new notification tests), using
Flask-SQLAlchemy 3.1.1 and SQLAlchemy 2.0.51 with temporary in-memory databases.
Existing user defaults still emit `datetime.utcnow()` deprecation warnings.
Notification delivery, browser authorization and Proxmox operations are not
implemented or tested in this step.

Next: integrate initialization so the five registered models can create their
tables in the configured Pond database without unintended Proxmox seeding.
