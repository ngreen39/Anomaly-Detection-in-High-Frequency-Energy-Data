SELECT COUNT(*) AS source_rows,
       COALESCE(SUM(duplicate_row), 0) AS duplicate_rows,
       COALESCE(SUM(duplicate_timestamp), 0) AS duplicate_timestamps,
       COALESCE(SUM(reading_timestamp IS NULL), 0) AS invalid_timestamps,
       COALESCE(SUM(reading_timestamp IS NOT NULL AND minute_epoch IS NULL), 0) AS off_minute_timestamps,
       COALESCE(SUM(missing_count > 0), 0) AS missing_measurement_rows,
       COALESCE(SUM(missing_count = 7), 0) AS all_measurements_missing_rows,
       COALESCE(SUM(missing_count), 0) AS missing_measurement_cells,
       COALESCE(SUM(invalid_count), 0) AS invalid_numeric_cells,
       COALESCE(SUM(negative_count), 0) AS negative_cells,
       COALESCE(SUM(infinity_count), 0) AS infinite_cells,
       (SELECT COUNT(*) FROM absent_timestamps) AS absent_timestamp_rows,
       (SELECT COUNT(*) FROM missing_runs) AS missing_run_count,
       (SELECT COALESCE(SUM(number_of_missing_readings), 0) FROM missing_runs)
           AS missing_minutes_in_runs,
       (SELECT COALESCE(MAX(duration_minutes), 0) FROM missing_runs)
           AS longest_missing_run_minutes
FROM readings;
