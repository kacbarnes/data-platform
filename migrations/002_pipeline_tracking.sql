-- Generic CLI run, version, lineage, and quality tracking.
-- The CLI applies these statements with IF NOT EXISTS for local bootstrapping;
-- this file is the immutable reviewed migration record.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY,
    command VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    contract_sha256 VARCHAR NOT NULL,
    source_uri VARCHAR,
    target_catalog VARCHAR NOT NULL,
    target_namespace VARCHAR NOT NULL,
    target_table VARCHAR NOT NULL,
    pipeline_git_commit VARCHAR,
    started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    error_message VARCHAR,
    metrics_json JSON
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    run_id UUID NOT NULL,
    dataset_id VARCHAR NOT NULL,
    catalog_name VARCHAR NOT NULL,
    namespace_name VARCHAR NOT NULL,
    table_name VARCHAR NOT NULL,
    snapshot_id UBIGINT NOT NULL,
    row_count UBIGINT,
    source_sha256 VARCHAR,
    schema_json JSON,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    run_id UUID NOT NULL,
    output_dataset_id VARCHAR NOT NULL,
    output_snapshot_id UBIGINT NOT NULL,
    input_dataset_id VARCHAR NOT NULL,
    input_snapshot_id UBIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS quality_results (
    run_id UUID NOT NULL,
    dataset_id VARCHAR NOT NULL,
    check_name VARCHAR NOT NULL,
    passed BOOLEAN NOT NULL,
    observed_json JSON,
    expected_json JSON,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

