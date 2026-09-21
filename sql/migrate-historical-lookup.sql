-- Run in the dedicated serving database, not the Hive metastore.
-- CREATE TABLE IF NOT EXISTS does not migrate an already populated database.
-- Add a covering index without deleting/replacing published rows or releases.
-- Re-runs are safe; a conflicting existing index definition is an error.
SET @historical_lookup_columns = (
  SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',')
    FROM information_schema.STATISTICS
   WHERE TABLE_SCHEMA = DATABASE()
     AND TABLE_NAME = 'dws_station_hourly_flow_v1'
     AND INDEX_NAME = 'historical_lookup'
);
SET @historical_lookup_ddl = CASE
  WHEN @historical_lookup_columns IS NULL THEN
    'ALTER TABLE dws_station_hourly_flow_v1 ADD INDEX historical_lookup (dataset_id, service_date, hour), ALGORITHM=INPLACE, LOCK=NONE'
  WHEN @historical_lookup_columns = 'dataset_id,service_date,hour' THEN
    'SELECT ''historical_lookup already installed'' AS migration_status'
  ELSE 'SELECT * FROM historical_lookup_definition_conflict__abort_migration'
END;
PREPARE historical_lookup_migration FROM @historical_lookup_ddl;
EXECUTE historical_lookup_migration;
DEALLOCATE PREPARE historical_lookup_migration;
