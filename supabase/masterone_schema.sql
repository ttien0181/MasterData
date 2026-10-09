-- MasterOne empty schema for Supabase PostgreSQL. Run once in SQL Editor.

-- Generated from SQLAlchemy models for a trusted backend PostgreSQL connection.

-- Application users are separate from Supabase auth.users.

-- No data, passwords, sessions or CSV files are imported.

-- RLS is enabled without Data API policies; anon/authenticated/service_role grants are revoked.

BEGIN;

SET LOCAL search_path = public;

CREATE TABLE auth_login_attempts (
	id VARCHAR(36) NOT NULL,
	email_normalized VARCHAR(320) NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	created_at_epoch INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id VARCHAR(36) NOT NULL,
	email VARCHAR(320) NOT NULL,
	email_normalized VARCHAR(320) NOT NULL,
	password_hash VARCHAR(512) NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (email_normalized)
);

CREATE TABLE auth_sessions (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	expires_at_epoch INTEGER NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (token_hash)
);

CREATE TABLE mdm_resolution_runs (
	id VARCHAR(36) NOT NULL,
	owner_user_id VARCHAR(36) NOT NULL,
	status VARCHAR(24) NOT NULL,
	phase VARCHAR(40) NOT NULL,
	policy_version VARCHAR(40) NOT NULL,
	record_count INTEGER NOT NULL,
	file_count INTEGER NOT NULL,
	candidate_count INTEGER NOT NULL,
	insufficient_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

CREATE TABLE mdm_resolution_explanations (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	signature_hash VARCHAR(64) NOT NULL,
	primary_group VARCHAR(48) NOT NULL,
	subgroup VARCHAR(48) NOT NULL,
	status VARCHAR(24) NOT NULL,
	explanation JSON,
	model_version VARCHAR(100),
	usage JSON,
	error_code VARCHAR(80),
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_mdm_resolution_explanation_signature UNIQUE (run_id, signature_hash),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_exports (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	parent_export_id VARCHAR(36),
	actor_user_id VARCHAR(36) NOT NULL,
	csv_bytes BYTEA NOT NULL,
	row_count INTEGER NOT NULL,
	merged_group_count INTEGER NOT NULL,
	pending_review_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(parent_export_id) REFERENCES mdm_resolution_exports (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id)
);

CREATE TABLE mdm_resolution_files (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	filename VARCHAR(255) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	row_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_mdm_resolution_file_hash UNIQUE (run_id, sha256),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_proposals (
	id VARCHAR(64) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	member_record_ids JSON NOT NULL,
	golden_fields JSON NOT NULL,
	status VARCHAR(32) NOT NULL,
	origin VARCHAR(16) NOT NULL,
	revision INTEGER NOT NULL,
	decision_reason VARCHAR(80),
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_review_cases (
	id VARCHAR(64) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	pair_ids JSON NOT NULL,
	member_record_ids JSON NOT NULL,
	primary_group VARCHAR(48) NOT NULL,
	subgroup VARCHAR(48) NOT NULL,
	signature_hash VARCHAR(64) NOT NULL,
	triage_reason VARCHAR(80) NOT NULL,
	status VARCHAR(24) NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_tasks (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	status VARCHAR(24) NOT NULL,
	phase VARCHAR(40) NOT NULL,
	processed_count INTEGER NOT NULL,
	total_count INTEGER NOT NULL,
	error_code VARCHAR(80),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_previews (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	actor_user_id VARCHAR(36) NOT NULL,
	policy_version VARCHAR(40) NOT NULL,
	pair_set_hash VARCHAR(64) NOT NULL,
	proposal_revisions JSON NOT NULL,
	choices JSON NOT NULL,
	acknowledge_unreviewed BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	execute_request_id VARCHAR(80),
	export_id VARCHAR(36),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(actor_user_id) REFERENCES users (id),
	FOREIGN KEY(export_id) REFERENCES mdm_resolution_exports (id)
);

CREATE TABLE mdm_resolution_records (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	external_id VARCHAR(200) NOT NULL,
	source_system VARCHAR(32) NOT NULL,
	raw_fields JSON NOT NULL,
	normalized_fields JSON NOT NULL,
	quality_flags JSON NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_mdm_resolution_record_external UNIQUE (run_id, external_id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(file_id) REFERENCES mdm_resolution_files (id) ON DELETE CASCADE
);

CREATE TABLE mdm_resolution_merge_events (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	preview_id VARCHAR(36) NOT NULL,
	export_id VARCHAR(36) NOT NULL,
	actor_user_id VARCHAR(36) NOT NULL,
	proposal_id VARCHAR(64) NOT NULL,
	member_record_ids JSON NOT NULL,
	pair_ids JSON NOT NULL,
	golden_fields JSON NOT NULL,
	guard_results JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(preview_id) REFERENCES mdm_resolution_previews (id),
	FOREIGN KEY(export_id) REFERENCES mdm_resolution_exports (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id),
	FOREIGN KEY(proposal_id) REFERENCES mdm_resolution_proposals (id)
);

CREATE TABLE mdm_resolution_pairs (
	id VARCHAR(64) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	left_record_id VARCHAR(36) NOT NULL,
	right_record_id VARCHAR(36) NOT NULL,
	blocking_reasons JSON NOT NULL,
	field_evidence JSON NOT NULL,
	score DOUBLE PRECISION NOT NULL,
	pair_decision VARCHAR(24) NOT NULL,
	decision_reason VARCHAR(80) NOT NULL,
	triage_status VARCHAR(32) NOT NULL,
	triage_reason VARCHAR(80) NOT NULL,
	primary_group VARCHAR(32),
	subgroup VARCHAR(80),
	signature VARCHAR(128),
	normalization_tags JSON NOT NULL,
	policy_id VARCHAR(80),
	policy_version VARCHAR(40),
	user_decision VARCHAR(24) NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_mdm_resolution_pair_records UNIQUE (run_id, left_record_id, right_record_id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(left_record_id) REFERENCES mdm_resolution_records (id),
	FOREIGN KEY(right_record_id) REFERENCES mdm_resolution_records (id)
);

CREATE TABLE mdm_resolution_split_events (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	before_export_id VARCHAR(36) NOT NULL,
	after_export_id VARCHAR(36) NOT NULL,
	actor_user_id VARCHAR(36) NOT NULL,
	request_id VARCHAR(80) NOT NULL,
	proposal_id VARCHAR(64) NOT NULL,
	separated_record_id VARCHAR(36) NOT NULL,
	retained_record_ids JSON NOT NULL,
	negative_pair_ids JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_mdm_resolution_split_request UNIQUE (run_id, request_id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(before_export_id) REFERENCES mdm_resolution_exports (id),
	FOREIGN KEY(after_export_id) REFERENCES mdm_resolution_exports (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id),
	FOREIGN KEY(proposal_id) REFERENCES mdm_resolution_proposals (id),
	FOREIGN KEY(separated_record_id) REFERENCES mdm_resolution_records (id)
);

CREATE TABLE mdm_resolution_audit_events (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	actor_user_id VARCHAR(36) NOT NULL,
	pair_id VARCHAR(64),
	proposal_id VARCHAR(64),
	action VARCHAR(40) NOT NULL,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES mdm_resolution_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(actor_user_id) REFERENCES users (id),
	FOREIGN KEY(pair_id) REFERENCES mdm_resolution_pairs (id),
	FOREIGN KEY(proposal_id) REFERENCES mdm_resolution_proposals (id)
);

CREATE INDEX ix_auth_attempt_email_ip_time ON auth_login_attempts (email_normalized, ip_address, created_at_epoch);

CREATE INDEX ix_auth_attempt_ip_time ON auth_login_attempts (ip_address, created_at_epoch);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE INDEX ix_mdm_resolution_runs_owner_user_id ON mdm_resolution_runs (owner_user_id);

CREATE INDEX ix_mdm_resolution_explanations_run_id ON mdm_resolution_explanations (run_id);

CREATE INDEX ix_mdm_resolution_exports_run_id ON mdm_resolution_exports (run_id);

CREATE INDEX ix_mdm_resolution_files_run_id ON mdm_resolution_files (run_id);

CREATE INDEX ix_mdm_resolution_proposals_run_id ON mdm_resolution_proposals (run_id);

CREATE INDEX ix_mdm_resolution_proposals_run_status ON mdm_resolution_proposals (run_id, status);

CREATE INDEX ix_mdm_resolution_review_cases_run_category ON mdm_resolution_review_cases (run_id, primary_group);

CREATE INDEX ix_mdm_resolution_review_cases_run_id ON mdm_resolution_review_cases (run_id);

CREATE INDEX ix_mdm_resolution_review_cases_signature ON mdm_resolution_review_cases (run_id, signature_hash);

CREATE INDEX ix_mdm_resolution_tasks_status_created ON mdm_resolution_tasks (status, created_at);

CREATE INDEX ix_mdm_resolution_previews_run_id ON mdm_resolution_previews (run_id);

CREATE INDEX ix_mdm_resolution_records_file_id ON mdm_resolution_records (file_id);

CREATE INDEX ix_mdm_resolution_records_run_id ON mdm_resolution_records (run_id);

CREATE INDEX ix_mdm_resolution_records_run_source ON mdm_resolution_records (run_id, source_system);

CREATE INDEX ix_mdm_resolution_merge_events_run_id ON mdm_resolution_merge_events (run_id);

CREATE INDEX ix_mdm_resolution_pairs_run_id ON mdm_resolution_pairs (run_id);

CREATE INDEX ix_mdm_resolution_pairs_run_triage ON mdm_resolution_pairs (run_id, triage_status);

CREATE INDEX ix_mdm_resolution_split_events_run_id ON mdm_resolution_split_events (run_id);

CREATE INDEX ix_mdm_resolution_audit_events_run_id ON mdm_resolution_audit_events (run_id);

CREATE INDEX ix_mdm_resolution_audit_run_created ON mdm_resolution_audit_events (run_id, created_at);

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN status SET DEFAULT 'UPLOADED';

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN phase SET DEFAULT 'upload';

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN policy_version SET DEFAULT 'person-v1';

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN record_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN file_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN candidate_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_runs ALTER COLUMN insufficient_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_explanations ALTER COLUMN status SET DEFAULT 'PENDING';

ALTER TABLE public.mdm_resolution_proposals ALTER COLUMN status SET DEFAULT 'AUTO_READY';

ALTER TABLE public.mdm_resolution_proposals ALTER COLUMN revision SET DEFAULT 1;

ALTER TABLE public.mdm_resolution_proposals ALTER COLUMN origin SET DEFAULT 'AUTO';

ALTER TABLE public.mdm_resolution_review_cases ALTER COLUMN status SET DEFAULT 'OPEN';

ALTER TABLE public.mdm_resolution_review_cases ALTER COLUMN revision SET DEFAULT 1;

ALTER TABLE public.mdm_resolution_review_cases ALTER COLUMN signature_hash SET DEFAULT '';

ALTER TABLE public.mdm_resolution_tasks ALTER COLUMN status SET DEFAULT 'QUEUED';

ALTER TABLE public.mdm_resolution_tasks ALTER COLUMN phase SET DEFAULT 'queued';

ALTER TABLE public.mdm_resolution_tasks ALTER COLUMN processed_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_tasks ALTER COLUMN total_count SET DEFAULT 0;

ALTER TABLE public.mdm_resolution_pairs ALTER COLUMN user_decision SET DEFAULT 'UNDECIDED';

ALTER TABLE public.mdm_resolution_pairs ALTER COLUMN revision SET DEFAULT 1;

ALTER TABLE public.mdm_resolution_pairs ALTER COLUMN normalization_tags SET DEFAULT '[]';

ALTER TABLE public.mdm_resolution_previews ALTER COLUMN status SET DEFAULT 'OPEN';

ALTER TABLE public.mdm_resolution_previews ALTER COLUMN acknowledge_unreviewed SET DEFAULT FALSE;

CREATE TABLE public.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);

INSERT INTO public.alembic_version (version_num) VALUES ('0013');

ALTER TABLE public.auth_login_attempts ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.auth_login_attempts FROM anon, authenticated, service_role;

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.users FROM anon, authenticated, service_role;

ALTER TABLE public.auth_sessions ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.auth_sessions FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_runs ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_runs FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_explanations ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_explanations FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_exports ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_exports FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_files ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_files FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_proposals ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_proposals FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_review_cases ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_review_cases FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_tasks ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_tasks FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_previews ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_previews FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_records ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_records FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_merge_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_merge_events FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_pairs ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_pairs FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_split_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_split_events FROM anon, authenticated, service_role;

ALTER TABLE public.mdm_resolution_audit_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.mdm_resolution_audit_events FROM anon, authenticated, service_role;

ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM anon, authenticated, service_role;

COMMIT;
