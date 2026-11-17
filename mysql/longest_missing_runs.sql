-- missing_runs contains the independently computed result of missing_runs.sql.
SELECT missing_run_id, start, end, number_of_missing_readings, duration_minutes
FROM missing_runs
ORDER BY duration_minutes DESC, start
LIMIT 10;
