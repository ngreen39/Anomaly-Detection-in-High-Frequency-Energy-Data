-- missing_runs is materialized from missing_runs.sql, independently of Python.
SELECT missing_run_id, start, end, number_of_missing_readings, duration_minutes
FROM missing_runs
ORDER BY duration_minutes DESC, start
LIMIT 10;
