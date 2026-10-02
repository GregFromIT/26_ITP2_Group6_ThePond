-- Synthetic pre-change schema and data generated from ThePondNewVers.zip models.
BEGIN TRANSACTION;
CREATE TABLE audit_logs (
	audit_id INTEGER NOT NULL,
	actor_user_id INTEGER,
	action VARCHAR(50) NOT NULL,
	target_type VARCHAR(50) NOT NULL,
	target_id INTEGER,
	details_json JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (audit_id),
	FOREIGN KEY(actor_user_id) REFERENCES users (user_id) ON DELETE SET NULL
);
CREATE TABLE challenge_flags (
	flag_id INTEGER NOT NULL,
	template_id INTEGER NOT NULL,
	flag_name VARCHAR(100) NOT NULL,
	flag_hash VARCHAR(255) NOT NULL,
	points INTEGER NOT NULL,
	sequence_number INTEGER,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (flag_id),
	FOREIGN KEY(template_id) REFERENCES vm_templates (template_id) ON DELETE CASCADE
);
INSERT INTO "challenge_flags" VALUES(1,1,'Objective','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',100,NULL,1,'2026-10-02 04:43:58.410598');
CREATE TABLE challenge_instances (
	instance_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	challenge_id INTEGER NOT NULL,
	instance_mode VARCHAR(20) NOT NULL,
	scoring_enabled BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	requested_at DATETIME NOT NULL,
	started_at DATETIME,
	expires_at DATETIME,
	stopped_at DATETIME,
	completed_at DATETIME,
	network_identifier VARCHAR(100),
	last_activity_at DATETIME,
	error_message TEXT,
	PRIMARY KEY (instance_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE,
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id)
);
INSERT INTO "challenge_instances" VALUES(1,1,1,'normal',1,'complete','2026-10-02 04:43:58.412357',NULL,NULL,NULL,NULL,NULL,NULL,NULL);
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
);
CREATE TABLE challenges (
	challenge_id INTEGER NOT NULL,
	title VARCHAR(100) NOT NULL,
	description TEXT NOT NULL,
	instructions TEXT NOT NULL,
	category VARCHAR(50),
	difficulty VARCHAR(20),
	status VARCHAR(20) NOT NULL,
	time_limit_minutes INTEGER,
	created_by_user_id INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (challenge_id),
	FOREIGN KEY(created_by_user_id) REFERENCES users (user_id) ON DELETE SET NULL
);
INSERT INTO "challenges" VALUES(1,'Old challenge','Old','Old',NULL,NULL,'published',NULL,1,'2026-10-02 04:43:58.406066','2026-10-02 04:43:58.406071');
CREATE TABLE flag_submissions (
	submission_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	instance_id INTEGER NOT NULL,
	vm_instance_id INTEGER,
	matched_flag_id INTEGER,
	was_correct BOOLEAN NOT NULL,
	was_already_solved BOOLEAN NOT NULL,
	scoring_enabled BOOLEAN NOT NULL,
	submitted_at DATETIME NOT NULL,
	PRIMARY KEY (submission_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE,
	FOREIGN KEY(instance_id) REFERENCES challenge_instances (instance_id) ON DELETE CASCADE,
	FOREIGN KEY(vm_instance_id) REFERENCES vm_instances (vm_instance_id) ON DELETE SET NULL,
	FOREIGN KEY(matched_flag_id) REFERENCES challenge_flags (flag_id) ON DELETE SET NULL
);
INSERT INTO "flag_submissions" VALUES(1,1,1,NULL,1,1,0,1,'2026-10-02 04:43:58.416746');
CREATE TABLE instance_jobs (
	job_id INTEGER NOT NULL,
	instance_id INTEGER NOT NULL,
	requested_by_user_id INTEGER NOT NULL,
	action VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempt_count INTEGER NOT NULL,
	locked_by VARCHAR(100),
	created_at DATETIME NOT NULL,
	started_at DATETIME,
	completed_at DATETIME,
	error_message TEXT,
	PRIMARY KEY (job_id),
	FOREIGN KEY(instance_id) REFERENCES challenge_instances (instance_id) ON DELETE CASCADE,
	FOREIGN KEY(requested_by_user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE TABLE network_rules (
	rule_id INTEGER NOT NULL,
	challenge_id INTEGER NOT NULL,
	from_role VARCHAR(50) NOT NULL,
	to_role VARCHAR(50) NOT NULL,
	protocol VARCHAR(10) NOT NULL,
	port INTEGER NOT NULL,
	PRIMARY KEY (rule_id),
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id) ON DELETE CASCADE
);
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
);
CREATE TABLE roles (
	role_id INTEGER NOT NULL,
	role_name VARCHAR(20) NOT NULL,
	role_level INTEGER NOT NULL,
	description VARCHAR(255),
	PRIMARY KEY (role_id),
	UNIQUE (role_name),
	UNIQUE (role_level)
);
INSERT INTO "roles" VALUES(1,'user',1,NULL);
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
);
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
);
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
);
CREATE TABLE throttle_events (
	event_id INTEGER NOT NULL,
	bucket VARCHAR(150) NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (event_id)
);
CREATE TABLE user_credentials (
	user_id INTEGER NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	password_changed_at DATETIME NOT NULL,
	must_change_password BOOLEAN NOT NULL,
	failed_login_count INTEGER NOT NULL,
	locked_until DATETIME,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
INSERT INTO "user_credentials" VALUES(1,'test-only-existing-hash','2026-10-02 04:43:58.407099',0,0,NULL);
CREATE TABLE user_solves (
	solve_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	flag_id INTEGER NOT NULL,
	instance_id INTEGER NOT NULL,
	awarded_points INTEGER NOT NULL,
	solved_at DATETIME NOT NULL,
	PRIMARY KEY (solve_id),
	CONSTRAINT uq_user_flag_solve UNIQUE (user_id, flag_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE,
	FOREIGN KEY(flag_id) REFERENCES challenge_flags (flag_id) ON DELETE CASCADE,
	FOREIGN KEY(instance_id) REFERENCES challenge_instances (instance_id) ON DELETE CASCADE
);
INSERT INTO "user_solves" VALUES(1,1,1,1,100,'2026-10-02 04:43:58.415484');
CREATE TABLE users (
	user_id INTEGER NOT NULL,
	username VARCHAR(50) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	role_id INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	last_login_at DATETIME,
	approval_status VARCHAR(20) DEFAULT 'pending' NOT NULL,
	approved_at DATETIME,
	approved_by INTEGER,
	PRIMARY KEY (user_id),
	CONSTRAINT ck_users_approval_status CHECK (approval_status IN ('pending', 'approved', 'rejected')),
	UNIQUE (username),
	FOREIGN KEY(role_id) REFERENCES roles (role_id),
	FOREIGN KEY(approved_by) REFERENCES users (user_id)
);
INSERT INTO "users" VALUES(1,'migration-student','Fixture',1,1,'2026-10-02 04:43:58.404064',NULL,'approved',NULL,NULL);
CREATE TABLE vm_instances (
	vm_instance_id INTEGER NOT NULL,
	instance_id INTEGER NOT NULL,
	template_id INTEGER NOT NULL,
	proxmox_vmid INTEGER NOT NULL,
	proxmox_node VARCHAR(100) NOT NULL,
	hostname VARCHAR(100),
	ip_address VARCHAR(45),
	mac_address VARCHAR(17),
	status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	started_at DATETIME,
	stopped_at DATETIME,
	deleted_at DATETIME,
	PRIMARY KEY (vm_instance_id),
	FOREIGN KEY(instance_id) REFERENCES challenge_instances (instance_id) ON DELETE CASCADE,
	FOREIGN KEY(template_id) REFERENCES vm_templates (template_id)
);
INSERT INTO "vm_instances" VALUES(1,1,1,9000,'test',NULL,NULL,NULL,'cloning','2026-10-02 04:43:58.414421',NULL,NULL,NULL);
CREATE TABLE vm_templates (
	template_id INTEGER NOT NULL,
	challenge_id INTEGER NOT NULL,
	template_name VARCHAR(100) NOT NULL,
	proxmox_template_vmid INTEGER NOT NULL,
	proxmox_node VARCHAR(100) NOT NULL,
	snapshot_name VARCHAR(100),
	vm_role VARCHAR(50) NOT NULL,
	cpu_cores INTEGER NOT NULL,
	memory_mb INTEGER NOT NULL,
	disk_gb INTEGER,
	static_ip VARCHAR(45),
	boot_order INTEGER NOT NULL,
	hostname_prefix VARCHAR(50),
	network_name VARCHAR(100),
	is_user_accessible BOOLEAN NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (template_id),
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id) ON DELETE CASCADE,
	UNIQUE (proxmox_template_vmid)
);
INSERT INTO "vm_templates" VALUES(1,1,'target',1200,'test',NULL,'target',1,1024,NULL,NULL,1,NULL,NULL,1,1,'2026-10-02 04:43:58.409015');
CREATE INDEX ix_throttle_events_occurred_at ON throttle_events (occurred_at);
CREATE INDEX ix_throttle_events_bucket ON throttle_events (bucket);
CREATE INDEX ix_users_approval_status ON users (approval_status);
CREATE INDEX ix_submission_status ON challenge_submissions (status, created_at);
CREATE INDEX ix_submission_uploader ON challenge_submissions (uploaded_by_user_id, created_at);
CREATE INDEX ix_submission_previous ON challenge_submissions (supersedes_submission_id);
CREATE UNIQUE INDEX ix_vminstance_active_vmid ON vm_instances (proxmox_vmid) WHERE deleted_at IS NULL;
CREATE INDEX ix_vm_instances_proxmox_vmid ON vm_instances (proxmox_vmid);
CREATE UNIQUE INDEX uq_submission_active_job ON submission_jobs (submission_id) WHERE status IN ('queued', 'running', 'retry_wait');
CREATE INDEX ix_submission_job_queue ON submission_jobs (status, available_at);
CREATE INDEX ix_notification_queue ON notification_outbox (status, available_at);
CREATE INDEX ix_notification_recipient ON notification_outbox (recipient_user_id, read_at);
CREATE INDEX ix_submission_issue_lookup ON submission_issues (submission_id, job_id, severity);
COMMIT;
