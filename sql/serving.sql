-- Day 2 v1.1. Run in the dedicated citibike database, never the Hive metastore DB.
-- UTC DATETIME columns use a +00:00 connection time zone.
SET time_zone = '+00:00';
CREATE TABLE IF NOT EXISTS historical_release (
  singleton TINYINT NOT NULL PRIMARY KEY CHECK (singleton = 1),
  dataset_id CHAR(64) NOT NULL,
  source_months JSON NOT NULL,
  min_service_date DATE NULL, max_service_date DATE NULL,
  published_at_utc DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dim_station_v1 (
  station_id VARCHAR(128) NOT NULL PRIMARY KEY,
  station_name VARCHAR(512) NULL, lat DOUBLE NULL, lon DOUBLE NULL,
  capacity INT NULL, region_id VARCHAR(128) NULL,
  is_current BOOLEAN NOT NULL, metadata_source VARCHAR(16) NOT NULL,
  metadata_updated_at DATETIME(6) NOT NULL, dataset_id CHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_hourly_flow_v1 (
  station_id VARCHAR(128) NOT NULL, service_date DATE NOT NULL,
  hour TINYINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
  inbound_rides BIGINT NOT NULL, outbound_rides BIGINT NOT NULL,
  net_flow BIGINT NOT NULL, total_activity BIGINT NOT NULL,
  electric_outbound BIGINT NOT NULL, classic_outbound BIGINT NOT NULL,
  member_outbound BIGINT NOT NULL, casual_outbound BIGINT NOT NULL,
  dataset_id CHAR(64) NOT NULL,
  PRIMARY KEY (station_id, service_date, hour), KEY availability (service_date, hour),
  KEY historical_lookup (dataset_id, service_date, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_hour_profile_v1 (
  station_id VARCHAR(128) NOT NULL, day_of_week TINYINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
  hour TINYINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
  avg_inbound DOUBLE NOT NULL, avg_outbound DOUBLE NOT NULL, avg_net_flow DOUBLE NOT NULL,
  median_net_flow DOUBLE NOT NULL, sample_days BIGINT NOT NULL CHECK (sample_days > 0),
  dataset_id CHAR(64) NOT NULL, PRIMARY KEY (station_id, day_of_week, hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_od_hourly_v1 (
  service_date DATE NOT NULL, hour TINYINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
  from_station_id VARCHAR(128) NOT NULL, to_station_id VARCHAR(128) NOT NULL,
  ride_count BIGINT NOT NULL CHECK (ride_count > 0), dataset_id CHAR(64) NOT NULL,
  PRIMARY KEY (service_date, hour, from_station_id, to_station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS live_release (
  singleton TINYINT NOT NULL PRIMARY KEY CHECK (singleton = 1),
  snapshot_id CHAR(64) NOT NULL, metadata_version CHAR(64) NOT NULL,
  baseline_dataset_id CHAR(64) NULL,
  snapshot_at_utc DATETIME(6) NOT NULL, ingested_at_utc DATETIME(6) NOT NULL,
  as_of_utc DATETIME(6) NOT NULL, published_at_utc DATETIME(6) NOT NULL,
  data_origin VARCHAR(16) NOT NULL, clock_mode VARCHAR(16) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS ads_station_current_risk (
  station_id VARCHAR(128) NOT NULL PRIMARY KEY, snapshot_id CHAR(64) NOT NULL,
  station_name VARCHAR(512) NULL, lat DOUBLE NULL, lon DOUBLE NULL, capacity INT NULL,
  num_bikes_available INT NULL, num_docks_available INT NULL,
  is_installed BOOLEAN NULL, is_renting BOOLEAN NULL, is_returning BOOLEAN NULL,
  current_status VARCHAR(32) NOT NULL, forecast_status VARCHAR(32) NOT NULL,
  current_reason VARCHAR(32) NULL, forecast_reason VARCHAR(32) NULL,
  fill_ratio DOUBLE NULL,
  expected_inbound_1h DOUBLE NULL, expected_outbound_1h DOUBLE NULL,
  expected_net_flow_1h DOUBLE NULL, projected_bikes_1h DOUBLE NULL, sample_days BIGINT NULL,
  snapshot_at_utc DATETIME(6) NOT NULL, last_reported_at_utc DATETIME(6) NULL,
  expires_at_utc DATETIME(6) NOT NULL, forecast_for_utc DATETIME(6) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS ads_rebalance_suggestion (
  suggestion_id VARCHAR(80) NOT NULL PRIMARY KEY, snapshot_id CHAR(64) NOT NULL,
  from_station_id VARCHAR(128) NOT NULL, to_station_id VARCHAR(128) NOT NULL,
  move_bikes INT NOT NULL CHECK (move_bikes > 0),
  from_surplus INT NOT NULL, to_deficit INT NOT NULL,
  distance_meters INT NOT NULL, priority INT NOT NULL UNIQUE CHECK (priority > 0),
  generated_at_utc DATETIME(6) NOT NULL, expires_at_utc DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dim_station_v1_load (
  station_id VARCHAR(128) NOT NULL, station_name VARCHAR(512) NULL,
  lat DOUBLE NULL, lon DOUBLE NULL, capacity INT NULL, region_id VARCHAR(128) NULL,
  is_current BOOLEAN NOT NULL, metadata_source VARCHAR(16) NOT NULL,
  metadata_updated_at DATETIME(6) NOT NULL, dataset_id CHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_hourly_flow_v1_load (
  station_id VARCHAR(128) NOT NULL, service_date DATE NOT NULL, hour TINYINT NOT NULL,
  inbound_rides BIGINT NOT NULL, outbound_rides BIGINT NOT NULL,
  net_flow BIGINT NOT NULL, total_activity BIGINT NOT NULL,
  electric_outbound BIGINT NOT NULL, classic_outbound BIGINT NOT NULL,
  member_outbound BIGINT NOT NULL, casual_outbound BIGINT NOT NULL,
  dataset_id CHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_hour_profile_v1_load (
  station_id VARCHAR(128) NOT NULL, day_of_week TINYINT NOT NULL, hour TINYINT NOT NULL,
  avg_inbound DOUBLE NOT NULL, avg_outbound DOUBLE NOT NULL, avg_net_flow DOUBLE NOT NULL,
  median_net_flow DOUBLE NOT NULL, sample_days BIGINT NOT NULL, dataset_id CHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dws_station_od_hourly_v1_load (
  service_date DATE NOT NULL, hour TINYINT NOT NULL,
  from_station_id VARCHAR(128) NOT NULL, to_station_id VARCHAR(128) NOT NULL,
  ride_count BIGINT NOT NULL, dataset_id CHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
