# Challenge uploads — step 11: in-app notification service

Requires the notification model from step 5 and initialized tables. Validation
worker notifications from step 10 already use the outbox consumed here.

## Apply

Add `challenge_ingestion/notifications.py` and
`tests/test_notification_service.py` to the matching repository directories.
No additional dependencies, model imports or schema changes are needed.

This provides delivery and inbox functions, not a browser inbox page or email
integration. No external messages were sent. No deployed database was changed.

## Deliver a batch

In the application's Python environment, from the repository root:

```bash
python3 -m challenge_ingestion.notifications --limit 100
```

The command loads the normal web app configuration and processes up to 100
eligible records, then exits. A deployment runner must invoke it repeatedly if
ongoing delivery is desired. It does not start a daemon or install a scheduled
task. Batch sizes must be 1–1000.

For in-app delivery, delivery means committing `status='sent'` and `sent_at` so
the record can appear in the recipient's inbox. There is no network send and no
need to claim then wait for an external provider. A short SQLite write transaction
selects and updates the batch atomically; concurrent invocations cannot deliver
the same row twice. Existing live delivery leases are respected, while expired
leases are recoverable.

Pending records wait until `available_at`. Already sent and failed records are
not selected. Inactive recipients or exhausted attempt limits produce a failed
record with a safe diagnostic message. The default maximum is three attempts;
it is configurable by trusted bootstrap code, not recipient input. Failed rows
require operator review and explicit requeue after the cause is corrected; no
requeue interface is implemented here.

If the database transaction fails, the whole batch rolls back and the process
reports the failure. A future run can try again. Such rolled-back attempts do
not increment stored counts, so process/database failures need deployment
monitoring rather than relying only on row attempt limits.

## Inbox API for the upcoming web routes

Instantiate `NotificationService(db.engines['pond'])` within the Flask app context.
It uses independent database sessions and should not be called while holding an
uncommitted upload/worker write transaction.

| Method | Purpose |
|---|---|
| `inbox(actor_user_id=..., limit=50, before_id=None, unread_only=False)` | Return safe fields for that recipient's delivered notifications |
| `unread_count(actor_user_id=...)` | Count that recipient's delivered unread notifications |
| `mark_read(notification_id, actor_user_id=...)` | Acknowledge an owned delivered notification without changing submission state |
| `deliver_pending(limit=100)` | Internal delivery operation; do not expose it as a recipient endpoint |

`actor_user_id` MUST come from the authenticated server-side user identity
(in this repo, `g.user['user_id']`), never from a form, URL user parameter or
unverified token. The service checks that the account is active and filters by
recipient, but it cannot authenticate an integer supplied by a caller. Future
routes must require login and CSRF protection for acknowledgement requests.
No administrator override is provided by these inbox methods.

`InboxItem` exposes only notification ID, submission ID, event type, message,
sent time and read time. Lease tokens, deduplication keys and internal errors are
not returned. Render messages as escaped text, and independently authorize access
to any linked submission page.

Inbox pages are ordered by descending notification ID, with a maximum page size
of 100. Use the last item's ID as the next page's `before_id`. Refresh from page
one to discover delayed deliveries with older IDs. Unsent records never appear.

`mark_read` returns False for missing, foreign or unsent records without exposing
which case applied. Repeating it for an owned delivered record returns True and
preserves the first read timestamp. Reading a notice does not resolve issues,
delete history or approve a challenge.

## Verification

```bash
python3 -m pytest tests/test_notification_service.py -q
```

19 new integration tests passed. They cover concurrent delivery, recipient
isolation, pagination, idempotent read acknowledgement, inactive accounts, expired
leases, attempt limits, future availability and transactional rollback. All data
was held in temporary SQLite databases. No browser routes or UI are included or
tested yet. Existing user-model timestamp deprecation warnings remain.

Next: connect these services to authenticated upload/status/inbox routes and
their web forms, including authorized submission creation and job enqueueing.
