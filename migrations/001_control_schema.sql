-- Documentation/migration copy of the schema created by ingest_iceberg.py.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID PRIMARY KEY,
    dataset_id VARCHAR NOT NULL,
    source_uri VARCHAR NOT NULL,
    source_sha256 VARCHAR,
    source_size_bytes UBIGINT,
    source_row_count UBIGINT,
    source_schema_json JSON,
    target_catalog VARCHAR NOT NULL,
    target_namespace VARCHAR NOT NULL,
    target_table VARCHAR NOT NULL,
    iceberg_snapshot_id UBIGINT,
    pipeline_git_commit VARCHAR,
    started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    error_message VARCHAR
);

