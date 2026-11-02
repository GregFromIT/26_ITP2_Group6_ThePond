-- REFERENCE ONLY: affected tables, generated from SQLAlchemy models.
-- Not a full database schema or a migration script.
-- Use db.migrate_container_labs for existing databases.
-- Foreign keys also reference unchanged tables not reproduced here.

CREATE TABLE challenges (
	execution_type VARCHAR(20) DEFAULT 'vm' NOT NULL,
	docker_challenge_key VARCHAR(160),
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
	CONSTRAINT ck_challenge_execution_type CHECK (execution_type IN ('vm', 'container_lab', 'offline')),
	CONSTRAINT ck_challenge_docker_key CHECK ((execution_type = 'container_lab' AND docker_challenge_key IS NOT NULL AND length(trim(docker_challenge_key)) > 0) OR (execution_type <> 'container_lab' AND docker_challenge_key IS NULL)),
	FOREIGN KEY(created_by_user_id) REFERENCES users (user_id) ON DELETE SET NULL
);

CREATE TABLE vm_templates (
	template_id INTEGER NOT NULL,
	template_name VARCHAR(100) NOT NULL,
	proxmox_template_vmid INTEGER NOT NULL,
	proxmox_node VARCHAR(100) NOT NULL,
	snapshot_name VARCHAR(100),
	cpu_cores INTEGER NOT NULL,
	memory_mb INTEGER NOT NULL,
	disk_gb INTEGER,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (template_id),
	UNIQUE (proxmox_template_vmid)
);

CREATE TABLE challenge_templates (
	challenge_template_id INTEGER NOT NULL,
	challenge_id INTEGER NOT NULL,
	template_id INTEGER NOT NULL,
	vm_role VARCHAR(50) NOT NULL,
	boot_order INTEGER NOT NULL,
	static_ip VARCHAR(45),
	hostname_prefix VARCHAR(50),
	network_name VARCHAR(100),
	is_user_accessible BOOLEAN NOT NULL,
	PRIMARY KEY (challenge_template_id),
	CONSTRAINT uq_challenge_template_role UNIQUE (challenge_id, vm_role),
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id) ON DELETE CASCADE,
	FOREIGN KEY(template_id) REFERENCES vm_templates (template_id) ON DELETE RESTRICT
);

CREATE TABLE challenge_flags (
	challenge_id INTEGER NOT NULL,
	flag_id INTEGER NOT NULL,
	template_id INTEGER,
	flag_name VARCHAR(100) NOT NULL,
	flag_hash VARCHAR(255) NOT NULL,
	points INTEGER NOT NULL,
	sequence_number INTEGER,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (flag_id),
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id) ON DELETE CASCADE,
	FOREIGN KEY(template_id) REFERENCES vm_templates (template_id) ON DELETE SET NULL
);

CREATE INDEX ix_challenge_flags_challenge_id ON challenge_flags (challenge_id);

CREATE TABLE challenge_instances (
	docker_challenge_key VARCHAR(160),
	docker_operation_key VARCHAR(100),
	docker_session_ref VARCHAR(255),
	docker_status VARCHAR(30) DEFAULT 'not_requested' NOT NULL,
	docker_error TEXT,
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
	UNIQUE (docker_operation_key),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE,
	FOREIGN KEY(challenge_id) REFERENCES challenges (challenge_id)
);

CREATE TABLE vm_instances (
	challenge_template_id INTEGER,
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
	FOREIGN KEY(challenge_template_id) REFERENCES challenge_templates (challenge_template_id) ON DELETE SET NULL,
	FOREIGN KEY(instance_id) REFERENCES challenge_instances (instance_id) ON DELETE CASCADE,
	FOREIGN KEY(template_id) REFERENCES vm_templates (template_id)
);

CREATE INDEX ix_vm_instances_proxmox_vmid ON vm_instances (proxmox_vmid);

CREATE UNIQUE INDEX ix_vminstance_active_vmid ON vm_instances (proxmox_vmid) WHERE deleted_at IS NULL;
