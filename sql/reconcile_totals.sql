SELECT COUNT(*) AS source_rows,
       SUM(duplicate_row) AS duplicate_rows,
       SUM(duplicate_timestamp) AS duplicate_timestamps,
       SUM(timestamp IS NULL) AS invalid_timestamps,
       SUM(timestamp IS NOT NULL AND minute_epoch IS NULL) AS off_minute_timestamps,
       SUM(missing_count > 0) AS missing_measurement_rows,
       SUM(missing_count = 7) AS all_measurements_missing_rows,
       SUM(missing_count) AS missing_measurement_cells,
       SUM(invalid_count) AS invalid_numeric_cells,
       SUM(negative_count) AS negative_cells,
       SUM(infinity_count) AS infinite_cells,
       (SELECT COUNT(*) FROM absent_timestamps) AS absent_timestamp_rows,
       (SELECT COUNT(*) FROM missing_runs) AS missing_run_count,
       (SELECT COALESCE(SUM(number_of_missing_readings), 0) FROM missing_runs)
           AS missing_minutes_in_runs,
       (SELECT COALESCE(MAX(duration_minutes), 0) FROM missing_runs)
           AS longest_missing_run_minutes
FROM readings;
