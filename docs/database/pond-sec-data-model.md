# Data model

What the database holds, how the tables connect, and which constraints are
doing real work. The models in `db/*_models.py` are the source of truth. The
column reference at the end of this doc is generated from them, so it matches
the code exactly.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. The merge changes no table or column.

---

## Where it lives

- **One SQLite file,** `the_pond.db` at the repo root by default. A `POND_SETTINGS`
  file can move it, by setting `SQLALCHEMY_BINDS = {'pond': 'sqlite:////path/to/the_pond.db'}`.
- **One SQLAlchemy bind,** called `pond`. Every model sets `__bind_key__ = "pond"`.
- **Foreign keys are enforced.** SQLite leaves them off by default; `db/orm.py`
  switches them on for every connection.
- **Creating it:** `python3 -m db.init_database` creates any missing tables and
  the three roles, and never touches existing data. It can't change an existing
  table: it refuses to start if a column the models expect is missing.
- **Changing the schema** therefore needs a one-off migration script. Copy the
  shape of `db/oneoff_add_account_approval.py`: a check-only mode, refuse a
  partial state, and run it once.

---

## The shape of it

```
roles ─< users ─┬─ user_credentials            (1:1, password hash and lockout state)
                ├─< challenge_instances ─┬─< vm_instances >── vm_templates
                │        │               ├─< flag_submissions
                │        │               └─< user_solves >── challenge_flags
                │        └── challenges ─┬─< challenge_templates >── vm_templates
                │                        ├─< challenge_flags
                │                        └─< network_rules
                ├─< audit_logs
                └─< challenge_submissions ─┬─< submission_files
                                           ├─< submission_jobs ─< submission_issues
                                           └─< notification_outbox

throttle_events   (standalone rate-limit counters)
instance_jobs     (created, but unused by the app)
```

`A ─< B` means one A has many B.

---

## The tables that matter most

### Accounts

- **`roles`:** exactly three rows, created by `db.init_database`: `user`
  (level 1), `manager` (2), `sysadmin` (3). The app calls them student,
  moderator and admin (`pond-sec/app/identity.py`). `role_name` and
  `role_level` are both unique, and the initialiser refuses to continue if
  they've drifted.
- **`users`:** username (unique), display name, role, `approval_status`
  (`pending`, `approved` or `rejected`), and who approved it when. There's no
  email column and no year of study (known issue W1).
- **`user_credentials`:** one row per user, kept apart from `users` so that
  reading a user never brings a password hash along. It holds the scrypt hash,
  `must_change_password` (set by a temporary password), the failed-login count,
  and `locked_until`. Only `pond-sec/app/security.py` reads or writes it.

### Challenges

- **`challenges`:** one row per challenge. `title` is the YAML file's
  `challenge:` value, and the seeder uses it as the identity. `category` is the
  theme. `status` must be `published` for students to see or launch it.
  `execution_type` is `vm`, `container_lab` or `offline`.
- **`vm_templates`:** one row per Proxmox template, unique on
  `proxmox_template_vmid`. A template is reusable: one Red Duck template serves
  every Red Duck challenge.
- **`challenge_templates`:** the join between a challenge and the templates it
  uses, with each VM's `vm_role`, `boot_order`, `static_ip` and
  `is_user_accessible`. Unique on `(challenge_id, vm_role)`. Launches clone, in
  `boot_order`, only the rows with `is_user_accessible` true.
- **`challenge_flags`:** the flags, each stored as a SHA-256 hash of the trimmed,
  lower-cased flag, with its points. A flag belongs to a challenge
  (`challenge_id`); `template_id` is optional, for information only.
- **`network_rules`:** the multi-VM firewall allows: `from_role`, `to_role`,
  `protocol`, `port`.

### Sessions and scoring

- **`challenge_instances`:** one student's session at one challenge. `status`
  goes `provisioning` → `running` → `complete` or `abandoned`.
  `network_identifier` holds the session VNet's name, for every session except a lone Red Duck (which has none). The
  `docker_*` columns are only for `container_lab` challenges.
- **`vm_instances`:** one row per clone, with its Proxmox VMID, node and
  `status` (`reserving`, `running`, `error`, `destroyed`). A row is "live" while
  `deleted_at` is empty.
- **`flag_submissions`:** every flag attempt, right or wrong. The leaderboards'
  "flags played" counts these.
- **`user_solves`:** the award ledger. One row the first time a user captures a
  flag, with the points copied in at that moment (`awarded_points`).

### Everything else

- **`audit_logs`:** append-only security events (`action`, `details_json`).
  Nothing in the app edits or deletes them. Staff actions are stored against the
  target account, not the staff member (known issue W6).
- **`throttle_events`:** one row per rate-limited attempt, keyed by `bucket`
  (action plus source). In the database so several workers share one count.
  Nothing prunes it.
- **The submission tables** (`challenge_submissions`, `submission_files`,
  `submission_jobs`, `submission_issues`, `notification_outbox`): the
  [submission pipeline](../challenges/submission-pipeline.md)'s staging area and
  history. They hold heavy `CHECK` constraints, so an invalid status or a missing
  approval can't be written.

---

## Constraints doing real work

| Constraint | What it stops |
|---|---|
| `user_solves` unique on `(user_id, flag_id)` | Claiming the same flag twice, even from two tabs at the same instant: the second insert fails instead of paying out |
| `vm_instances` partial unique index on `proxmox_vmid` where `deleted_at IS NULL` | Two live sessions getting the same VMID. It's the lock `provisioner.claim_vmid()` relies on |
| `challenge_templates` unique on `(challenge_id, vm_role)` | Two VMs with the same role in one challenge |
| `vm_templates.proxmox_template_vmid` unique | Two template rows for the same Proxmox template |
| `ck_challenge_docker_key` on `challenges` | A `container_lab` challenge without a Docker key, or a key on any other type |
| `ck_users_approval_status` on `users` | Any approval state other than pending, approved or rejected |
| `submission_jobs` partial unique index on `submission_id` for queued or running jobs | Two active jobs on one submission |

Things that are **not** constraints, and are enforced only in code:
- one running session per user (`themes.launch()`; it's racy, known issue L6);
- always keeping at least one admin (`admin.set_role()` refuses to demote the last one).

---

## Timestamps

Stored as naive UTC (no time zone). The web app's own code gets "now" from
`db.orm.utcnow_naive()`. Most model defaults, `provisioner.py` and
`challenge_ingestion/` still write time-zone-aware values. SQLite gives both
back as naive, so treat every stored time as UTC, and compare stored values only
with naive UTC times (`utcnow_naive()`), or Python raises `TypeError`.
Pages format them with the `|stamp` filter (`pond-sec/app/__init__.py`).

## Backups

The whole platform state is this one file. See "Backups" in the
[admin guide](../user-guides/pond-sec-admin-guide.md) for a safe way to copy it
while the app is running.

---

## Column reference

Generated from the models (`04998a1` with the branch merged; the merge changed no model columns). "Required" means `NOT NULL`.

### `audit_logs`

| Column | Type | Rules |
|---|---|---|
| `audit_id` | integer | primary key |
| `actor_user_id` | integer | → `users.user_id` (on delete set null) |
| `action` | varchar(50) | required |
| `target_type` | varchar(50) | required |
| `target_id` | integer |  |
| `details_json` | json |  |
| `created_at` | datetime | required |

### `challenge_flags`

| Column | Type | Rules |
|---|---|---|
| `challenge_id` | integer | required, → `challenges.challenge_id` (on delete cascade) |
| `flag_id` | integer | primary key |
| `template_id` | integer | → `vm_templates.template_id` (on delete set null) |
| `flag_name` | varchar(100) | required |
| `flag_hash` | varchar(255) | required |
| `points` | integer | required |
| `sequence_number` | integer |  |
| `is_active` | boolean | required |
| `created_at` | datetime | required |

### `challenge_instances`

| Column | Type | Rules |
|---|---|---|
| `docker_challenge_key` | varchar(160) |  |
| `docker_operation_key` | varchar(100) | unique |
| `docker_session_ref` | varchar(255) |  |
| `docker_status` | varchar(30) | required |
| `docker_error` | text |  |
| `instance_id` | integer | primary key |
| `user_id` | integer | required, → `users.user_id` (on delete cascade) |
| `challenge_id` | integer | required, → `challenges.challenge_id` |
| `instance_mode` | varchar(20) | required |
| `scoring_enabled` | boolean | required |
| `status` | varchar(20) | required |
| `requested_at` | datetime | required |
| `started_at` | datetime |  |
| `expires_at` | datetime |  |
| `stopped_at` | datetime |  |
| `completed_at` | datetime |  |
| `network_identifier` | varchar(100) |  |
| `last_activity_at` | datetime |  |
| `error_message` | text |  |

### `challenge_submissions`

| Column | Type | Rules |
|---|---|---|
| `submission_id` | integer | primary key |
| `uploaded_by_user_id` | integer | required, → `users.user_id` (on delete restrict) |
| `title` | varchar(100) | required |
| `challenge_type` | varchar(50) | required |
| `schema_version` | integer | required |
| `manifest_json` | json | required |
| `status` | varchar(30) | required |
| `supersedes_submission_id` | integer | → `challenge_submissions.submission_id` (on delete restrict) |
| `content_digest` | varchar(64) |  |
| `approved_by_user_id` | integer | → `users.user_id` (on delete restrict) |
| `approved_at` | datetime |  |
| `published_challenge_id` | integer | unique, → `challenges.challenge_id` (on delete restrict) |
| `submitted_at` | datetime |  |
| `published_at` | datetime |  |
| `created_at` | datetime | required |
| `updated_at` | datetime | required |

- Check `ck_submission_schema_version`: `schema_version >= 1`
- Check `ck_submission_approval_pair`: `(approved_by_user_id IS NULL AND approved_at IS NULL) OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)`
- Check `ck_submission_status`: `status IN ('draft', 'validating', 'validation_failed', 'needs_changes', 'ready_for_review', 'approved', 'importing', 'import_failed', 'published', 'rejected')`
- Check `ck_submission_publication_pair`: `(status = 'published' AND published_challenge_id IS NOT NULL AND published_at IS NOT NULL) OR (status <> 'published' AND published_challenge_id IS NULL AND published_at IS NULL)`
- Check `ck_submission_not_self_revision`: `supersedes_submission_id IS NULL OR supersedes_submission_id <> submission_id`
- Check `ck_submission_manifest_object`: `json_valid(manifest_json) AND json_type(manifest_json) = 'object'`
- Check `ck_submission_finalized`: `status = 'draft' OR (content_digest IS NOT NULL AND submitted_at IS NOT NULL)`
- Check `ck_submission_requires_approval`: `status NOT IN ('approved', 'importing', 'import_failed', 'published') OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)`
- Check `ck_submission_digest`: `content_digest IS NULL OR (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*')`

### `challenge_templates`

| Column | Type | Rules |
|---|---|---|
| `challenge_template_id` | integer | primary key |
| `challenge_id` | integer | required, → `challenges.challenge_id` (on delete cascade) |
| `template_id` | integer | required, → `vm_templates.template_id` (on delete restrict) |
| `vm_role` | varchar(50) | required |
| `boot_order` | integer | required |
| `static_ip` | varchar(45) |  |
| `hostname_prefix` | varchar(50) |  |
| `network_name` | varchar(100) |  |
| `is_user_accessible` | boolean | required |

- Unique together: `(challenge_id, vm_role)`

### `challenges`

| Column | Type | Rules |
|---|---|---|
| `execution_type` | varchar(20) | required |
| `docker_challenge_key` | varchar(160) |  |
| `challenge_id` | integer | primary key |
| `title` | varchar(100) | required |
| `description` | text | required |
| `instructions` | text | required |
| `category` | varchar(50) |  |
| `difficulty` | varchar(20) |  |
| `status` | varchar(20) | required |
| `time_limit_minutes` | integer |  |
| `created_by_user_id` | integer | → `users.user_id` (on delete set null) |
| `created_at` | datetime | required |
| `updated_at` | datetime | required |

- Check `ck_challenge_docker_key`: `(execution_type = 'container_lab' AND docker_challenge_key IS NOT NULL AND length(trim(docker_challenge_key)) > 0) OR (execution_type <> 'container_lab' AND docker_challenge_key IS NULL)`
- Check `ck_challenge_execution_type`: `execution_type IN ('vm', 'container_lab', 'offline')`

### `flag_submissions`

| Column | Type | Rules |
|---|---|---|
| `submission_id` | integer | primary key |
| `user_id` | integer | required, → `users.user_id` (on delete cascade) |
| `instance_id` | integer | required, → `challenge_instances.instance_id` (on delete cascade) |
| `vm_instance_id` | integer | → `vm_instances.vm_instance_id` (on delete set null) |
| `matched_flag_id` | integer | → `challenge_flags.flag_id` (on delete set null) |
| `was_correct` | boolean | required |
| `was_already_solved` | boolean | required |
| `scoring_enabled` | boolean | required |
| `submitted_at` | datetime | required |

### `instance_jobs`

| Column | Type | Rules |
|---|---|---|
| `job_id` | integer | primary key |
| `instance_id` | integer | required, → `challenge_instances.instance_id` (on delete cascade) |
| `requested_by_user_id` | integer | required, → `users.user_id` (on delete cascade) |
| `action` | varchar(20) | required |
| `status` | varchar(20) | required |
| `attempt_count` | integer | required |
| `locked_by` | varchar(100) |  |
| `created_at` | datetime | required |
| `started_at` | datetime |  |
| `completed_at` | datetime |  |
| `error_message` | text |  |

### `network_rules`

| Column | Type | Rules |
|---|---|---|
| `rule_id` | integer | primary key |
| `challenge_id` | integer | required, → `challenges.challenge_id` (on delete cascade) |
| `from_role` | varchar(50) | required |
| `to_role` | varchar(50) | required |
| `protocol` | varchar(10) | required |
| `port` | integer | required |

### `notification_outbox`

| Column | Type | Rules |
|---|---|---|
| `notification_id` | integer | primary key |
| `submission_id` | integer | required, → `challenge_submissions.submission_id` (on delete restrict) |
| `recipient_user_id` | integer | required, → `users.user_id` (on delete restrict) |
| `event_type` | varchar(50) | required |
| `deduplication_key` | varchar(150) | required, unique |
| `channel` | varchar(20) | required |
| `message` | text | required |
| `status` | varchar(20) | required |
| `attempt_count` | integer | required |
| `available_at` | datetime | required |
| `lease_token` | varchar(100) |  |
| `lease_expires_at` | datetime |  |
| `sent_at` | datetime |  |
| `read_at` | datetime |  |
| `last_error` | text |  |
| `created_at` | datetime | required |

- Check `ck_notification_status`: `status IN ('pending', 'delivering', 'sent', 'failed')`
- Check `ck_notification_delivery_lease`: `status <> 'delivering' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)`
- Check `ck_notification_channel`: `channel = 'in_app'`
- Check `ck_notification_sent_at`: `status <> 'sent' OR sent_at IS NOT NULL`
- Check `ck_notification_attempts`: `attempt_count >= 0`

### `roles`

| Column | Type | Rules |
|---|---|---|
| `role_id` | integer | primary key |
| `role_name` | varchar(20) | required, unique |
| `role_level` | integer | required, unique |
| `description` | varchar(255) |  |

### `submission_files`

| Column | Type | Rules |
|---|---|---|
| `file_id` | integer | primary key |
| `submission_id` | integer | required, → `challenge_submissions.submission_id` (on delete restrict) |
| `file_role` | varchar(50) | required |
| `logical_path` | text | required |
| `original_filename` | text | required |
| `storage_key` | text | required, unique |
| `size_bytes` | integer | required |
| `sha256` | varchar(64) | required |
| `detected_media_type` | varchar(100) |  |
| `validation_status` | varchar(20) | required |
| `validated_at` | datetime |  |
| `created_at` | datetime | required |

- Check `ck_submission_file_size`: `size_bytes >= 0`
- Check `ck_submission_file_validation_status`: `validation_status IN ('pending', 'passed', 'failed', 'review_required')`
- Unique together: `(submission_id, logical_path)`
- Check `ck_submission_file_sha256`: `length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'`
- Unique together: `(submission_id, file_id)`

### `submission_issues`

| Column | Type | Rules |
|---|---|---|
| `issue_id` | integer | primary key |
| `submission_id` | integer | required, → `challenge_submissions.submission_id` (on delete restrict), → `submission_files.submission_id` (on delete restrict), → `submission_jobs.submission_id` (on delete restrict) |
| `job_id` | integer | required, → `submission_jobs.job_id` (on delete restrict) |
| `file_id` | integer | → `submission_files.file_id` (on delete restrict) |
| `severity` | varchar(10) | required |
| `error_code` | varchar(80) | required |
| `field_path` | text |  |
| `message` | text | required |
| `resolution_note` | text |  |
| `resolved_by_user_id` | integer | → `users.user_id` (on delete restrict) |
| `resolved_at` | datetime |  |
| `created_at` | datetime | required |

- Check `ck_submission_issue_severity`: `severity IN ('error', 'warning', 'info')`

### `submission_jobs`

| Column | Type | Rules |
|---|---|---|
| `job_id` | integer | primary key |
| `submission_id` | integer | required, → `challenge_submissions.submission_id` (on delete restrict) |
| `requested_by_user_id` | integer | required, → `users.user_id` (on delete restrict) |
| `action` | varchar(20) | required |
| `status` | varchar(20) | required |
| `idempotency_key` | varchar(100) | required, unique |
| `attempt_count` | integer | required |
| `max_attempts` | integer | required |
| `available_at` | datetime | required |
| `locked_by` | varchar(100) |  |
| `lease_token` | varchar(100) |  |
| `lease_expires_at` | datetime |  |
| `heartbeat_at` | datetime |  |
| `resource_inventory_json` | json | required |
| `error_code` | varchar(80) |  |
| `error_message` | text |  |
| `created_at` | datetime | required |
| `started_at` | datetime |  |
| `completed_at` | datetime |  |

- Check `ck_submission_job_attempts`: `attempt_count >= 0`
- Check `ck_submission_job_action`: `action IN ('validate', 'publish', 'cleanup')`
- Check `ck_submission_job_running_lease`: `status <> 'running' OR (locked_by IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)`
- Check `ck_submission_job_status`: `status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')`
- Check `ck_submission_job_inventory`: `json_valid(resource_inventory_json) AND json_type(resource_inventory_json) = 'array'`
- Check `ck_submission_job_max_attempts`: `max_attempts >= 1`
- Unique together: `(submission_id, job_id)`
- Unique index `uq_submission_active_job` on `(submission_id)` where `status IN ('queued', 'running', 'retry_wait')`

### `throttle_events`

| Column | Type | Rules |
|---|---|---|
| `event_id` | integer | primary key |
| `bucket` | varchar(150) | required |
| `occurred_at` | datetime | required |

### `user_credentials`

| Column | Type | Rules |
|---|---|---|
| `user_id` | integer | primary key, → `users.user_id` (on delete cascade) |
| `password_hash` | varchar(255) | required |
| `password_changed_at` | datetime | required |
| `must_change_password` | boolean | required |
| `failed_login_count` | integer | required |
| `locked_until` | datetime |  |

### `user_solves`

| Column | Type | Rules |
|---|---|---|
| `solve_id` | integer | primary key |
| `user_id` | integer | required, → `users.user_id` (on delete cascade) |
| `flag_id` | integer | required, → `challenge_flags.flag_id` (on delete cascade) |
| `instance_id` | integer | required, → `challenge_instances.instance_id` (on delete cascade) |
| `awarded_points` | integer | required |
| `solved_at` | datetime | required |

- Unique together: `(user_id, flag_id)`

### `users`

| Column | Type | Rules |
|---|---|---|
| `user_id` | integer | primary key |
| `username` | varchar(50) | required, unique |
| `display_name` | varchar(100) | required |
| `role_id` | integer | required, → `roles.role_id` |
| `is_active` | boolean | required |
| `created_at` | datetime | required |
| `last_login_at` | datetime |  |
| `approval_status` | varchar(20) | required |
| `approved_at` | datetime |  |
| `approved_by` | integer | → `users.user_id` |

- Check `ck_users_approval_status`: `approval_status IN ('pending', 'approved', 'rejected')`

### `vm_instances`

| Column | Type | Rules |
|---|---|---|
| `challenge_template_id` | integer | → `challenge_templates.challenge_template_id` (on delete set null) |
| `vm_instance_id` | integer | primary key |
| `instance_id` | integer | required, → `challenge_instances.instance_id` (on delete cascade) |
| `template_id` | integer | required, → `vm_templates.template_id` |
| `proxmox_vmid` | integer | required |
| `proxmox_node` | varchar(100) | required |
| `hostname` | varchar(100) |  |
| `ip_address` | varchar(45) |  |
| `mac_address` | varchar(17) |  |
| `status` | varchar(20) | required |
| `created_at` | datetime | required |
| `started_at` | datetime |  |
| `stopped_at` | datetime |  |
| `deleted_at` | datetime |  |

- Unique index `ix_vminstance_active_vmid` on `(proxmox_vmid)` where `vm_instances.deleted_at IS NULL`

### `vm_templates`

| Column | Type | Rules |
|---|---|---|
| `template_id` | integer | primary key |
| `template_name` | varchar(100) | required |
| `proxmox_template_vmid` | integer | required, unique |
| `proxmox_node` | varchar(100) | required |
| `snapshot_name` | varchar(100) |  |
| `cpu_cores` | integer | required |
| `memory_mb` | integer | required |
| `disk_gb` | integer |  |
| `is_active` | boolean | required |
| `created_at` | datetime | required |

