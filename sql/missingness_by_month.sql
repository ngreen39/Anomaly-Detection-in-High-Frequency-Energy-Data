-- Source rows with missing measurements are different from absent minute rows.
-- Unparseable dates stay in an explicit bucket rather than disappearing.
WITH source_months AS (
    SELECT COALESCE(substr(timestamp, 1, 7), 'INVALID_TIMESTAMP') AS month,
           COUNT(*) AS source_rows,
           SUM(missing_count > 0) AS missing_measurement_rows,
           SUM(missing_count) AS missing_measurement_cells
    FROM readings
    GROUP BY 1
), absent_months AS (
    SELECT substr(timestamp, 1, 7) AS month,
           COUNT(*) AS absent_timestamp_rows
    FROM absent_timestamps
    GROUP BY 1
), months AS (
    SELECT month FROM source_months UNION SELECT month FROM absent_months
)
SELECT month,
       COALESCE(source_rows, 0) AS source_rows,
       COALESCE(missing_measurement_rows, 0) AS missing_measurement_rows,
       COALESCE(missing_measurement_cells, 0) AS missing_measurement_cells,
       COALESCE(absent_timestamp_rows, 0) AS absent_timestamp_rows
FROM months
LEFT JOIN source_months USING (month)
LEFT JOIN absent_months USING (month)
ORDER BY month;
