-- Loaded only into a newly created, dedicated database. No DROP/TRUNCATE.
CREATE TABLE audit_metadata (
    key_name VARCHAR(80) PRIMARY KEY,
    value_text TEXT NOT NULL
);
CREATE TABLE readings (
    source_row_id BIGINT PRIMARY KEY,
    reading_timestamp DATETIME NULL,
    minute_epoch BIGINT NULL,
    missing_count TINYINT UNSIGNED NOT NULL,
    invalid_count TINYINT UNSIGNED NOT NULL,
    negative_count TINYINT UNSIGNED NOT NULL,
    infinity_count TINYINT UNSIGNED NOT NULL,
    duplicate_row BOOLEAN NOT NULL,
    duplicate_timestamp BOOLEAN NOT NULL,
    INDEX missing_minutes (missing_count, minute_epoch)
);
CREATE TABLE exceptions (
    source_row_id BIGINT NULL,
    reading_timestamp DATETIME NULL,
    field VARCHAR(64) NOT NULL,
    rule VARCHAR(40) NOT NULL,
    observed_value TEXT NOT NULL
);
CREATE TABLE absent_timestamps (
    reading_timestamp DATETIME PRIMARY KEY
);
CREATE TABLE missing_runs (
    missing_run_id BIGINT PRIMARY KEY,
    start DATETIME NOT NULL,
    end DATETIME NOT NULL,
    number_of_missing_readings BIGINT NOT NULL,
    duration_minutes BIGINT NOT NULL
);
