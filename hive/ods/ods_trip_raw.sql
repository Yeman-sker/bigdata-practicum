-- Day 1 / Track D: source-faithful Historical Trips ODS.
--
-- The landing script copies source bytes from RAW to
-- /warehouse/ods/ods_trip_raw/year=YYYY/month=MM before this file is run.
-- This table is EXTERNAL so dropping/recreating metadata cannot delete RAW.
-- Track A's verified 2025-01 CSVs contain no quoted delimiter fields, so the
-- native delimited SerDe can preserve the 13 positions and expose DOUBLE
-- coordinate columns. For a future source with quoted delimiters, ingest it
-- through a STRING source table and cast into a typed ODS table explicitly.

CREATE DATABASE IF NOT EXISTS citibike_ods
COMMENT 'Citi Bike source-faithful operational data store';

USE citibike_ods;

CREATE EXTERNAL TABLE IF NOT EXISTS ods_trip_raw (
    ride_id STRING,
    rideable_type STRING,
    started_at STRING,
    ended_at STRING,
    start_station_name STRING,
    start_station_id STRING,
    end_station_name STRING,
    end_station_id STRING,
    start_lat DOUBLE,
    start_lng DOUBLE,
    end_lat DOUBLE,
    end_lng DOUBLE,
    member_casual STRING
)
PARTITIONED BY (
    `year` INT,
    `month` INT
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '/warehouse/ods/ods_trip_raw'
TBLPROPERTIES (
    'external.table.purge' = 'false',
    'skip.header.line.count' = '1',
    'source.schema' = 'Citi Bike Historical Trips 13-column CSV',
    'ods.contract' = 'source-faithful; DWD parsing is downstream'
);

-- Discover every year=YYYY/month=MM directory, including all CSV files in
-- a month partition. This is intentionally not a one-file-per-month load.
MSCK REPAIR TABLE ods_trip_raw;
