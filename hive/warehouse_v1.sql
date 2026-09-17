-- Day 2 v1.1 contract DDL. External Parquet outputs are produced by #28.
CREATE DATABASE IF NOT EXISTS citibike_dw;
USE citibike_dw;
CREATE EXTERNAL TABLE IF NOT EXISTS dwd_trip_v1 (
  ride_id STRING, rideable_type STRING,
  started_at_local TIMESTAMP, ended_at_local TIMESTAMP, duration_seconds BIGINT,
  start_station_id STRING, start_station_name STRING, start_lat DOUBLE, start_lng DOUBLE,
  end_station_id STRING, end_station_name STRING, end_lat DOUBLE, end_lng DOUBLE,
  member_casual STRING, service_date DATE, start_hour TINYINT, day_of_week TINYINT,
  is_weekend BOOLEAN, ingest_batch_id STRING, source_file STRING, source_row_number BIGINT,
  is_duplicate_ride BOOLEAN, is_valid_station_trip BOOLEAN
) PARTITIONED BY (source_year INT, source_month INT)
STORED AS PARQUET LOCATION '/warehouse/dwd/dwd_trip_v1'
TBLPROPERTIES ('external.table.purge'='false');
CREATE EXTERNAL TABLE IF NOT EXISTS dim_station_v1 (
  station_id STRING, station_name STRING, lat DOUBLE, lon DOUBLE, capacity INT,
  region_id STRING, is_current BOOLEAN, metadata_source STRING, metadata_updated_at TIMESTAMP,
  dataset_id STRING
) STORED AS PARQUET LOCATION '/warehouse/dim/dim_station_v1'
TBLPROPERTIES ('external.table.purge'='false');
CREATE EXTERNAL TABLE IF NOT EXISTS dws_station_hourly_flow_v1 (
  station_id STRING, hour TINYINT, inbound_rides BIGINT, outbound_rides BIGINT,
  net_flow BIGINT, total_activity BIGINT, electric_outbound BIGINT, classic_outbound BIGINT,
  member_outbound BIGINT, casual_outbound BIGINT, dataset_id STRING
) PARTITIONED BY (service_date DATE)
STORED AS PARQUET LOCATION '/warehouse/dws/dws_station_hourly_flow_v1'
TBLPROPERTIES ('external.table.purge'='false');
CREATE EXTERNAL TABLE IF NOT EXISTS dws_station_hour_profile_v1 (
  station_id STRING, day_of_week TINYINT, hour TINYINT, avg_inbound DOUBLE,
  avg_outbound DOUBLE, avg_net_flow DOUBLE, median_net_flow DOUBLE, sample_days BIGINT,
  dataset_id STRING
) STORED AS PARQUET LOCATION '/warehouse/dws/dws_station_hour_profile_v1'
TBLPROPERTIES ('external.table.purge'='false');
CREATE EXTERNAL TABLE IF NOT EXISTS dws_station_od_hourly_v1 (
  hour TINYINT, from_station_id STRING, to_station_id STRING, ride_count BIGINT,
  dataset_id STRING
) PARTITIONED BY (service_date DATE)
STORED AS PARQUET LOCATION '/warehouse/dws/dws_station_od_hourly_v1'
TBLPROPERTIES ('external.table.purge'='false');
-- Register completed partitions only after the producing job passes its checks.
-- Hive does not enforce these logical primary keys / nullability; #28 verifies them before publication.
