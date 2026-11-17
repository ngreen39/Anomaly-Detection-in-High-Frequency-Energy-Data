-- Compute runs independently in MySQL from source audit facts, not Python run IDs.
WITH missing_minutes AS (
    SELECT DISTINCT minute_epoch
    FROM readings
    WHERE missing_count > 0 AND minute_epoch IS NOT NULL
), islands AS (
    SELECT minute_epoch,
           minute_epoch - ROW_NUMBER() OVER (ORDER BY minute_epoch) AS island
    FROM missing_minutes
), runs AS (
    SELECT MIN(minute_epoch) AS first_minute, MAX(minute_epoch) AS last_minute,
           COUNT(*) AS number_of_missing_readings
    FROM islands
    GROUP BY island
)
SELECT ROW_NUMBER() OVER (ORDER BY first_minute) AS missing_run_id,
       TIMESTAMPADD(MINUTE, first_minute, CAST('1970-01-01 00:00:00' AS DATETIME)) AS start,
       TIMESTAMPADD(MINUTE, last_minute, CAST('1970-01-01 00:00:00' AS DATETIME)) AS end,
       number_of_missing_readings,
       last_minute - first_minute + 1 AS duration_minutes
FROM runs
ORDER BY first_minute;
