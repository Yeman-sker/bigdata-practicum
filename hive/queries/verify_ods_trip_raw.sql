-- Run with, for example:
-- hive --hiveconf hive.exec.local.scratchdir=/tmp/citibike-day1/hive-local \
--      --hiveconf source_year=2025 --hiveconf source_month=1 \
--      -f hive/queries/verify_ods_trip_raw.sql

USE citibike_ods;

-- The DESCRIBE output is the type-contract evidence. In particular, both
-- station IDs must be STRING and source timestamps must remain STRING in ODS.
DESCRIBE ods_trip_raw;

SELECT
    `year`,
    `month`,
    COUNT(*) AS ride_count
FROM ods_trip_raw
WHERE `year` = CAST('${hiveconf:source_year}' AS INT)
  AND `month` = CAST('${hiveconf:source_month}' AS INT)
GROUP BY `year`, `month`;

SELECT COUNT(*) AS partition_ride_count
FROM ods_trip_raw
WHERE `year` = CAST('${hiveconf:source_year}' AS INT)
  AND `month` = CAST('${hiveconf:source_month}' AS INT);

SELECT
    ride_id,
    start_station_id,
    end_station_id,
    started_at
FROM ods_trip_raw
WHERE `year` = CAST('${hiveconf:source_year}' AS INT)
  AND `month` = CAST('${hiveconf:source_month}' AS INT)
LIMIT 10;

-- Header rows must not become data rows. A zero result is expected.
SELECT COUNT(*) AS csv_header_rows
FROM ods_trip_raw
WHERE `year` = CAST('${hiveconf:source_year}' AS INT)
  AND `month` = CAST('${hiveconf:source_month}' AS INT)
  AND ride_id = 'ride_id'
  AND rideable_type = 'rideable_type'
  AND started_at = 'started_at';

-- The Track A manifest reports zero duplicate ride IDs for 2025-01. This
-- query is a lightweight reconciliation check, not a DWD quality transform.
SELECT COUNT(*) AS duplicate_ride_id_groups
FROM (
    SELECT ride_id
    FROM ods_trip_raw
    WHERE `year` = CAST('${hiveconf:source_year}' AS INT)
      AND `month` = CAST('${hiveconf:source_month}' AS INT)
    GROUP BY ride_id
    HAVING COUNT(*) > 1
) duplicates;
