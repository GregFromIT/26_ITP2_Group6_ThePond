-- Reference DDL only. Existing referenced tables must already exist.
-- Generated from the SQLAlchemy models; use db.init_database for initialization.


CREATE TABLE challenge_submissions (
	submission_id INTEGER NOT NULL, 
	uploaded_by_user_id INTEGER NOT NULL, 
	title VARCHAR(100) NOT NULL, 
	challenge_type VARCHAR(50) NOT NULL, 
	schema_version INTEGER DEFAULT 1 NOT NULL, 
	manifest_json JSON DEFAULT '{}' NOT NULL, 
	status VARCHAR(30) DEFAULT 'draft' NOT NULL, 
	supersedes_submission_id INTEGER, 
	content_digest VARCHAR(64), 
	approved_by_user_id INTEGER, 
	approved_at DATETIME, 
	published_challenge_id INTEGER, 
	submitted_at DATETIME, 
	published_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (submission_id), 
	CONSTRAINT ck_submission_schema_version CHECK (schema_version >= 1), 
	CONSTRAINT ck_submission_manifest_object CHECK (json_valid(manifest_json) AND json_type(manifest_json) = 'object'), 
	CONSTRAINT ck_submission_status CHECK (status IN ('draft', 'validating', 'validation_failed', 'needs_changes', 'ready_for_review', 'approved', 'importing', 'import_failed', 'published', 'rejected')), 
	CONSTRAINT ck_submission_digest CHECK (content_digest IS NULL OR (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*')), 
	CONSTRAINT ck_submission_not_self_revision CHECK (supersedes_submission_id IS NULL OR supersedes_submission_id <> submission_id), 
	CONSTRAINT ck_submission_approval_pair CHECK ((approved_by_user_id IS NULL AND approved_at IS NULL) OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)), 
	CONSTRAINT ck_submission_finalized CHECK (status = 'draft' OR (content_digest IS NOT NULL AND submitted_at IS NOT NULL)), 
	CONSTRAINT ck_submission_requires_approval CHECK (status NOT IN ('approved', 'importing', 'import_failed', 'published') OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)), 
	CONSTRAINT ck_submission_publication_pair CHECK ((status = 'published' AND published_challenge_id IS NOT NULL AND published_at IS NOT NULL) OR (status <> 'published' AND published_challenge_id IS NULL AND published_at IS NULL)), 
	FOREIGN KEY(uploaded_by_user_id) REFERENCES users (user_id) ON DELETE RESTRICT, 
	FOREIGN KEY(supersedes_submission_id) REFERENCES challenge_submissions (submission_id) ON DELETE RESTRICT, 
	FOREIGN KEY(approved_by_user_id) REFERENCES users (user_id) ON DELETE RESTRICT, 
	UNIQUE (published_challenge_id), 
	FOREIGN KEY(published_challenge_id) REFERENCES challenges (challenge_id) ON DELETE RESTRICT
)

;

CREATE INDEX ix_submission_previous ON challenge_submissions (supersedes_submission_id);

CREATE INDEX ix_submission_status ON challenge_submissions (status, created_at);

CREATE INDEX ix_submission_uploader ON challenge_submissions (uploaded_by_user_id, created_at);


CREATE TABLE submission_files (
	file_id INTEGER NOT NULL, 
	submission_id INTEGER NOT NULL, 
	file_role VARCHAR(50) NOT NULL, 
	logical_path TEXT NOT NULL, 
	original_filename TEXT NOT NULL, 
	storage_key TEXT NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	detected_media_type VARCHAR(100), 
	validation_status VARCHAR(20) DEFAULT 'pending' NOT NULL, 
	validated_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (file_id), 
	CONSTRAINT ck_submission_file_size CHECK (size_bytes >= 0), 
	CONSTRAINT ck_submission_file_sha256 CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'), 
	CONSTRAINT ck_submission_file_validation_status CHECK (validation_status IN ('pending', 'passed', 'failed', 'review_required')), 
	CONSTRAINT uq_submission_file_path UNIQUE (submission_id, logical_path), 
	CONSTRAINT uq_submission_file_identity UNIQUE (submission_id, file_id), 
	FOREIGN KEY(submission_id) REFERENCES challenge_submissions (submission_id) ON DELETE RESTRICT, 
	UNIQUE (storage_key)
)

;


CREATE TABLE submission_jobs (
	job_id INTEGER NOT NULL, 
	submission_id INTEGER NOT NULL, 
	requested_by_user_id INTEGER NOT NULL, 
	action VARCHAR(20) NOT NULL, 
	status VARCHAR(20) DEFAULT 'queued' NOT NULL, 
	idempotency_key VARCHAR(100) NOT NULL, 
	attempt_count INTEGER DEFAULT 0 NOT NULL, 
	max_attempts INTEGER DEFAULT 3 NOT NULL, 
	available_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	locked_by VARCHAR(100), 
	lease_token VARCHAR(100), 
	lease_expires_at DATETIME, 
	heartbeat_at DATETIME, 
	resource_inventory_json JSON DEFAULT '[]' NOT NULL, 
	error_code VARCHAR(80), 
	error_message TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	started_at DATETIME, 
	completed_at DATETIME, 
	PRIMARY KEY (job_id), 
	CONSTRAINT ck_submission_job_action CHECK (action IN ('validate', 'publish', 'cleanup')), 
	CONSTRAINT ck_submission_job_status CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')), 
	CONSTRAINT ck_submission_job_attempts CHECK (attempt_count >= 0), 
	CONSTRAINT ck_submission_job_max_attempts CHECK (max_attempts >= 1), 
	CONSTRAINT ck_submission_job_inventory CHECK (json_valid(resource_inventory_json) AND json_type(resource_inventory_json) = 'array'), 
	CONSTRAINT ck_submission_job_running_lease CHECK (status <> 'running' OR (locked_by IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)), 
	CONSTRAINT uq_submission_job_identity UNIQUE (submission_id, job_id), 
	FOREIGN KEY(submission_id) REFERENCES challenge_submissions (submission_id) ON DELETE RESTRICT, 
	FOREIGN KEY(requested_by_user_id) REFERENCES users (user_id) ON DELETE RESTRICT, 
	UNIQUE (idempotency_key)
)

;

CREATE INDEX ix_submission_job_queue ON submission_jobs (status, available_at);

CREATE UNIQUE INDEX uq_submission_active_job ON submission_jobs (submission_id) WHERE status IN ('queued', 'running', 'retry_wait');


CREATE TABLE submission_issues (
	issue_id INTEGER NOT NULL, 
	submission_id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	file_id INTEGER, 
	severity VARCHAR(10) NOT NULL, 
	error_code VARCHAR(80) NOT NULL, 
	field_path TEXT, 
	message TEXT NOT NULL, 
	resolution_note TEXT, 
	resolved_by_user_id INTEGER, 
	resolved_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (issue_id), 
	CONSTRAINT ck_submission_issue_severity CHECK (severity IN ('error', 'warning', 'info')), 
	CONSTRAINT fk_submission_issue_file FOREIGN KEY(submission_id, file_id) REFERENCES submission_files (submission_id, file_id) ON DELETE RESTRICT, 
	CONSTRAINT fk_submission_issue_job FOREIGN KEY(submission_id, job_id) REFERENCES submission_jobs (submission_id, job_id) ON DELETE RESTRICT, 
	FOREIGN KEY(submission_id) REFERENCES challenge_submissions (submission_id) ON DELETE RESTRICT, 
	FOREIGN KEY(resolved_by_user_id) REFERENCES users (user_id) ON DELETE RESTRICT
)

;

CREATE INDEX ix_submission_issue_lookup ON submission_issues (submission_id, job_id, severity);


CREATE TABLE notification_outbox (
	notification_id INTEGER NOT NULL, 
	submission_id INTEGER NOT NULL, 
	recipient_user_id INTEGER NOT NULL, 
	event_type VARCHAR(50) NOT NULL, 
	deduplication_key VARCHAR(150) NOT NULL, 
	channel VARCHAR(20) DEFAULT 'in_app' NOT NULL, 
	message TEXT NOT NULL, 
	status VARCHAR(20) DEFAULT 'pending' NOT NULL, 
	attempt_count INTEGER DEFAULT 0 NOT NULL, 
	available_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	lease_token VARCHAR(100), 
	lease_expires_at DATETIME, 
	sent_at DATETIME, 
	read_at DATETIME, 
	last_error TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (notification_id), 
	CONSTRAINT ck_notification_channel CHECK (channel = 'in_app'), 
	CONSTRAINT ck_notification_status CHECK (status IN ('pending', 'delivering', 'sent', 'failed')), 
	CONSTRAINT ck_notification_attempts CHECK (attempt_count >= 0), 
	CONSTRAINT ck_notification_delivery_lease CHECK (status <> 'delivering' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)), 
	CONSTRAINT ck_notification_sent_at CHECK (status <> 'sent' OR sent_at IS NOT NULL), 
	FOREIGN KEY(submission_id) REFERENCES challenge_submissions (submission_id) ON DELETE RESTRICT, 
	FOREIGN KEY(recipient_user_id) REFERENCES users (user_id) ON DELETE RESTRICT, 
	UNIQUE (deduplication_key)
)

;

CREATE INDEX ix_notification_queue ON notification_outbox (status, available_at);

CREATE INDEX ix_notification_recipient ON notification_outbox (recipient_user_id, read_at);
