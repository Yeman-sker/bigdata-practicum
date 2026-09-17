#!/usr/bin/env python3
"""Build the versioned historical warehouse and serving exports with Spark.

RAW CSV is the authoritative DWD input.  Files are opened one at a time in
manifest order and Hadoop byte offsets are sorted before record numbers are
assigned, so source identity does not depend on Spark shuffle partitioning.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

if __package__:
    from .contracts import SOURCE_FIELDS, SOURCE_MONTH_PATTERN, SPARK_TIME_PATTERNS
else:
    from citibike.contracts import (
        SOURCE_FIELDS,
        SOURCE_MONTH_PATTERN,
        SPARK_TIME_PATTERNS,
    )

CONTRACT_VERSION = "1.1"
RELEASE_FORMAT = "immutable-v1"
SERVING_FORMAT = "tsv-unquoted-v1"
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
WAREHOUSE_TABLE_PATHS = {
    "dwd_trip_v1": "dwd/dwd_trip_v1",
    "dim_station_v1": "dim/dim_station_v1",
    "dws_station_hourly_flow_v1": "dws/dws_station_hourly_flow_v1",
    "dws_station_hour_profile_v1": "dws/dws_station_hour_profile_v1",
    "dws_station_od_hourly_v1": "dws/dws_station_od_hourly_v1",
}
SERVING_COLUMNS = {
    "dim_station_v1": (
        "station_id", "station_name", "lat", "lon", "capacity", "region_id",
        "is_current", "metadata_source", "metadata_updated_at", "dataset_id",
    ),
    "dws_station_hourly_flow_v1": (
        "station_id", "service_date", "hour", "inbound_rides", "outbound_rides",
        "net_flow", "total_activity", "electric_outbound", "classic_outbound",
        "member_outbound", "casual_outbound", "dataset_id",
    ),
    "dws_station_hour_profile_v1": (
        "station_id", "day_of_week", "hour", "avg_inbound", "avg_outbound",
        "avg_net_flow", "median_net_flow", "sample_days", "dataset_id",
    ),
    "dws_station_od_hourly_v1": (
        "service_date", "hour", "from_station_id", "to_station_id", "ride_count",
        "dataset_id",
    ),
}


class OfflineError(RuntimeError):
    """Raised when a batch cannot be safely built or reconciled."""


def parse_source_month(value: str) -> tuple[int, int]:
    match = SOURCE_MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise OfflineError(f"source month must use YYYY-MM, got {value!r}")
    return int(match.group("year")), int(match.group("month"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise OfflineError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def dataset_id(metadata_version: str, months: Iterable[tuple[str, str]]) -> str:
    lines = [f"{month}:{zip_hash}\n" for month, zip_hash in sorted(months)]
    payload = f"{CONTRACT_VERSION}\n{metadata_version}\n{''.join(lines)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path, expected: type, label: str) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfflineError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(value, expected):
        raise OfflineError(f"{label} must contain a {expected.__name__}")
    return value


def load_manifest(path: Path, source_month: str) -> dict[str, Any]:
    manifest = _load_json(path, dict, "manifest")
    if manifest.get("source_month") != source_month:
        raise OfflineError(
            f"manifest source_month {manifest.get('source_month')!r} != {source_month!r}"
        )
    zip_hash = manifest.get("zip_sha256")
    if not isinstance(zip_hash, str) or re.fullmatch(r"[0-9a-f]{64}", zip_hash) is None:
        raise OfflineError("manifest zip_sha256 must be a lowercase SHA-256 digest")
    entries = manifest.get("csv_files")
    if not isinstance(entries, list) or not entries:
        raise OfflineError("manifest csv_files must be a non-empty list")
    names: set[str] = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise OfflineError("manifest csv_files entries must be objects")
        name = entry.get("filename")
        count = entry.get("record_count")
        if not isinstance(name, str) or not name or PurePosixPath(name).name != name:
            raise OfflineError(f"unsafe manifest filename: {name!r}")
        if name in names:
            raise OfflineError(f"duplicate manifest filename: {name}")
        if entry.get("header") != list(SOURCE_FIELDS):
            raise OfflineError(f"{name}: manifest header does not match source contract")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise OfflineError(f"{name}: invalid manifest record_count")
        names.add(name)
        total += count
    if manifest.get("record_count") != total:
        raise OfflineError(
            f"manifest record_count {manifest.get('record_count')!r} != member total {total}"
        )
    return manifest


def load_metadata(path: Path) -> list[dict[str, Any]]:
    rows = _load_json(path, list, "metadata")
    ids: set[str] = set()
    provider_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise OfflineError(f"metadata row {index} must be an object")
        provider_id = row.get("provider_station_id")
        station_id = row.get("station_id")
        for name, value in (("provider_station_id", provider_id), ("station_id", station_id)):
            if (
                not isinstance(value, str)
                or not value
                or value.strip() != value
                or len(value) > 128
                or any(character.isspace() for character in value)
            ):
                raise OfflineError(f"metadata row {index} has invalid {name}")
        if station_id in ids:
            raise OfflineError(f"duplicate metadata station_id: {station_id}")
        if provider_id in provider_ids:
            raise OfflineError(f"duplicate metadata provider_station_id: {provider_id}")
        if row.get("is_current") is not True or row.get("metadata_source") != "GBFS":
            raise OfflineError(f"metadata station {station_id} is not a current GBFS row")
        mapping_status = row.get("mapping_status")
        mapping_reason = row.get("mapping_reason")
        if mapping_status == "SHORT_NAME":
            if mapping_reason is not None:
                raise OfflineError(f"metadata station {station_id} has an invalid mapping_reason")
        elif mapping_status == "ISOLATED":
            if (
                mapping_reason
                not in {"MISSING_SHORT_NAME", "DUPLICATE_SHORT_NAME", "MISSING_METADATA"}
                or station_id != f"gbfs:{provider_id}"
            ):
                raise OfflineError(f"metadata station {station_id} has an invalid isolated mapping")
        else:
            raise OfflineError(f"metadata station {station_id} has an invalid mapping_status")
        for name, limit in (("station_name", 512), ("region_id", 128)):
            value = row.get(name)
            if value is not None and (
                not isinstance(value, str)
                or not value
                or value.strip() != value
                or len(value) > limit
            ):
                raise OfflineError(f"metadata station {station_id} has invalid {name}")
        for name, lower, upper in (("lat", -90, 90), ("lon", -180, 180)):
            value = row.get(name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise OfflineError(f"metadata station {station_id} has invalid {name}")
        capacity = row.get("capacity")
        if capacity is not None and (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or not 0 < capacity <= 2_147_483_647
        ):
            raise OfflineError(f"metadata station {station_id} has invalid capacity")
        updated_at = _parse_utc_timestamp(
            row.get("metadata_updated_at"), f"metadata station {station_id} metadata_updated_at"
        )
        ids.add(station_id)
        provider_ids.add(provider_id)
        normalized.append({**row, "metadata_updated_at": updated_at})
    return normalized


def _join_uri(root: str, source_month: str, filename: str) -> str:
    year, month = parse_source_month(source_month)
    root = root.rstrip("/")
    return f"{root}/year={year:04d}/month={month:02d}/{filename}"


def _local_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if not parsed.scheme and not uri.startswith("/"):
        return Path(uri)
    return None


def preflight_local_files(raw_root: str, source_month: str, manifest: dict[str, Any]) -> None:
    """Check local fixture bytes before Spark starts; HDFS is checked by record counts."""

    for entry in manifest["csv_files"]:
        uri = _join_uri(raw_root, source_month, entry["filename"])
        path = _local_path(uri)
        if path is None:
            continue
        if not path.is_file():
            raise OfflineError(f"manifest member is missing: {path}")
        expected_size = entry.get("size_bytes")
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            raise OfflineError(
                f"{entry['filename']}: size {path.stat().st_size} != manifest {expected_size}"
            )


def _parse_csv_line(line: str) -> tuple[str, ...]:
    try:
        rows = list(csv.reader(io.StringIO(line), strict=True))
    except csv.Error as error:
        raise OfflineError(f"malformed CSV record: {error}") from error
    if len(rows) != 1 or len(rows[0]) != len(SOURCE_FIELDS):
        raise OfflineError(
            "RAW member is not one complete 13-column CSV record per physical line; "
            "multiline CSV needs an explicit record-aware input path"
        )
    return tuple(rows[0])


def _read_manifest_rdd(spark, raw_root: str, source_month: str, manifest: dict[str, Any]):
    from pyspark import StorageLevel

    sc = spark.sparkContext
    result = None
    for entry in manifest["csv_files"]:
        filename = entry["filename"]
        uri = _join_uri(raw_root, source_month, filename)
        records = sc.newAPIHadoopFile(
            uri,
            "org.apache.hadoop.mapreduce.lib.input.TextInputFormat",
            "org.apache.hadoop.io.LongWritable",
            "org.apache.hadoop.io.Text",
        )
        ordered = records.sortByKey(numPartitions=1).values()
        header = ordered.take(1)
        if header != [",".join(SOURCE_FIELDS)]:
            parsed_header = _parse_csv_line(header[0]) if header else ()
            if parsed_header != SOURCE_FIELDS:
                raise OfflineError(f"{filename}: actual CSV header does not match source contract")
        body = ordered.zipWithIndex().filter(lambda pair: pair[1] > 0)
        rows = body.map(
            lambda pair, name=filename: _parse_csv_line(pair[0]) + (name, int(pair[1]))
        ).persist(StorageLevel.MEMORY_AND_DISK)
        actual = rows.count()
        if actual != entry["record_count"]:
            raise OfflineError(
                f"{filename}: RAW record count {actual} != manifest {entry['record_count']}"
            )
        result = rows if result is None else result.union(rows)
    if result is None:
        raise OfflineError("manifest contains no CSV members")
    return result


def _timestamp_expr(F, column: str):
    return F.coalesce(
        *(F.try_to_timestamp(column, F.lit(pattern)) for pattern in SPARK_TIME_PATTERNS)
    )


def _safe_coordinate(F, name: str):
    parsed = F.expr(f"try_cast(`{name}` AS DOUBLE)")
    lower, upper = (-90.0, 90.0) if name.endswith("lat") else (-180.0, 180.0)
    return F.when(parsed.between(lower, upper) & ~F.isnan(parsed), parsed)


def _blank_to_null(F, name: str):
    return F.when(F.length(F.trim(F.col(name))) > 0, F.trim(F.col(name)))


def build_frames(
    spark,
    *,
    raw_root: str,
    source_month: str,
    manifest: dict[str, Any],
    metadata_rows: list[dict[str, Any]],
    metadata_version: str,
    built_at_utc: str,
) -> dict[str, Any]:
    """Build all DWD/DIM/DWS frames and quality metrics."""

    from pyspark.sql import Window
    from pyspark.sql import functions as F
    from pyspark.sql import types as T

    year, month = parse_source_month(source_month)
    batch_id = manifest["zip_sha256"]
    current_dataset_id = dataset_id(metadata_version, [(source_month, batch_id)])
    schema = T.StructType(
        [T.StructField(name, T.StringType(), True) for name in SOURCE_FIELDS]
        + [
            T.StructField("source_file", T.StringType(), False),
            T.StructField("source_row_number", T.LongType(), False),
        ]
    )
    raw = spark.createDataFrame(
        _read_manifest_rdd(spark, raw_root, source_month, manifest), schema=schema
    )
    required = ("ride_id", "rideable_type", "started_at", "ended_at", "member_casual")
    required_invalid = F.lit(False)
    for name in required:
        required_invalid = required_invalid | F.col(name).isNull() | (F.trim(F.col(name)) == "")
    if raw.where(required_invalid).limit(1).count():
        raise OfflineError("critical source string/time field is blank; source gate rejected the batch")

    transformed = raw
    transformed = transformed.withColumn("started_at_local", _timestamp_expr(F, "started_at"))
    transformed = transformed.withColumn("ended_at_local", _timestamp_expr(F, "ended_at"))
    if transformed.where(
        F.col("started_at_local").isNull() | F.col("ended_at_local").isNull()
    ).limit(1).count():
        raise OfflineError("critical timestamp parse failed; source gate rejected the batch")
    for name in ("start_station_id", "end_station_id", "start_station_name", "end_station_name"):
        transformed = transformed.withColumn(name, _blank_to_null(F, name))
    for name in ("start_lat", "start_lng", "end_lat", "end_lng"):
        transformed = transformed.withColumn(name, _safe_coordinate(F, name))
    transformed = (
        transformed.withColumn(
            "duration_seconds",
            F.floor(F.col("ended_at_local").cast("double") - F.col("started_at_local").cast("double")).cast("long"),
        )
        .withColumn("service_date", F.to_date("started_at_local"))
        .withColumn("start_hour", F.hour("started_at_local").cast("tinyint"))
        .withColumn("day_of_week", (((F.dayofweek("started_at_local") + 5) % 7) + 1).cast("tinyint"))
        .withColumn("is_weekend", F.col("day_of_week").isin(6, 7))
        .withColumn("source_year", F.lit(year))
        .withColumn("source_month", F.lit(month))
        .withColumn("ingest_batch_id", F.lit(batch_id))
    )
    identity = Window.partitionBy("ride_id").orderBy(
        "source_year", "source_month", "source_file", "source_row_number"
    )
    transformed = transformed.withColumn("__duplicate_rank", F.row_number().over(identity))
    transformed = transformed.withColumn("is_duplicate_ride", F.col("__duplicate_rank") > 1)
    transformed = transformed.withColumn(
        "is_valid_station_trip",
        (~F.col("is_duplicate_ride"))
        & F.col("start_station_id").isNotNull()
        & F.col("end_station_id").isNotNull()
        & (F.col("ended_at_local") > F.col("started_at_local")),
    )
    business = [name for name in SOURCE_FIELDS if name != "ride_id"]
    signature = F.sha2(F.to_json(F.struct(*[F.col(c) for c in business]), {"ignoreNullFields": "false"}), 256)
    transformed = transformed.withColumn("__signature", signature)
    transformed = transformed.withColumn("__winner_signature", F.first("__signature").over(identity))
    duplicate_conflicts = transformed.where(
        F.col("is_duplicate_ride") & (F.col("__signature") != F.col("__winner_signature"))
    ).count()

    dwd_columns = (
        "ride_id", "rideable_type", "started_at_local", "ended_at_local", "duration_seconds",
        "start_station_id", "start_station_name", "start_lat", "start_lng", "end_station_id",
        "end_station_name", "end_lat", "end_lng", "member_casual", "service_date",
        "start_hour", "day_of_week", "is_weekend", "ingest_batch_id", "source_file",
        "source_row_number", "is_duplicate_ride", "is_valid_station_trip", "source_year", "source_month",
    )
    dwd = transformed.select(*dwd_columns).cache()
    valid = dwd.where("is_valid_station_trip").cache()

    outbound = valid.select(
        F.col("start_station_id").alias("station_id"),
        F.to_date("started_at_local").alias("service_date"),
        F.hour("started_at_local").cast("tinyint").alias("hour"),
        "rideable_type", "member_casual",
    )
    inbound = valid.select(
        F.col("end_station_id").alias("station_id"),
        F.to_date("ended_at_local").alias("service_date"),
        F.hour("ended_at_local").cast("tinyint").alias("hour"),
    )
    active_days = outbound.select("station_id", "service_date").union(
        inbound.select("station_id", "service_date")
    ).distinct()
    hours = spark.range(24).select(F.col("id").cast("tinyint").alias("hour"))
    grid = active_days.crossJoin(hours)
    out_agg = outbound.groupBy("station_id", "service_date", "hour").agg(
        F.count("*").cast("long").alias("outbound_rides"),
        F.sum(F.when(F.col("rideable_type") == "electric_bike", 1).otherwise(0)).cast("long").alias("electric_outbound"),
        F.sum(F.when(F.col("rideable_type") == "classic_bike", 1).otherwise(0)).cast("long").alias("classic_outbound"),
        F.sum(F.when(F.col("member_casual") == "member", 1).otherwise(0)).cast("long").alias("member_outbound"),
        F.sum(F.when(F.col("member_casual") == "casual", 1).otherwise(0)).cast("long").alias("casual_outbound"),
    )
    in_agg = inbound.groupBy("station_id", "service_date", "hour").agg(
        F.count("*").cast("long").alias("inbound_rides")
    )
    flow = grid.join(in_agg, ["station_id", "service_date", "hour"], "left").join(
        out_agg, ["station_id", "service_date", "hour"], "left"
    )
    counts = (
        "inbound_rides", "outbound_rides", "electric_outbound", "classic_outbound",
        "member_outbound", "casual_outbound",
    )
    flow = flow.fillna(0, subset=list(counts))
    flow = flow.withColumn("net_flow", F.col("inbound_rides") - F.col("outbound_rides"))
    flow = flow.withColumn("total_activity", F.col("inbound_rides") + F.col("outbound_rides"))
    flow = flow.select(
        "station_id", "service_date", "hour", "inbound_rides", "outbound_rides", "net_flow",
        "total_activity", "electric_outbound", "classic_outbound", "member_outbound", "casual_outbound",
    ).cache()
    profile_source = flow.withColumn(
        "day_of_week", (((F.dayofweek("service_date") + 5) % 7) + 1).cast("tinyint")
    )
    profile = profile_source.groupBy("station_id", "day_of_week", "hour").agg(
        F.avg("inbound_rides").alias("avg_inbound"),
        F.avg("outbound_rides").alias("avg_outbound"),
        F.avg("net_flow").alias("avg_net_flow"),
        F.expr("percentile(net_flow, 0.5)").cast("double").alias("median_net_flow"),
        F.countDistinct("service_date").cast("long").alias("sample_days"),
    ).cache()
    od = valid.groupBy(
        F.to_date("started_at_local").alias("service_date"),
        F.hour("started_at_local").cast("tinyint").alias("hour"),
        F.col("start_station_id").alias("from_station_id"),
        F.col("end_station_id").alias("to_station_id"),
    ).agg(F.count("*").cast("long").alias("ride_count")).cache()

    metadata_schema = T.StructType([
        T.StructField("station_id", T.StringType(), False),
        T.StructField("station_name", T.StringType(), True),
        T.StructField("lat", T.DoubleType(), True), T.StructField("lon", T.DoubleType(), True),
        T.StructField("capacity", T.IntegerType(), True), T.StructField("region_id", T.StringType(), True),
        T.StructField("is_current", T.BooleanType(), False),
        T.StructField("metadata_source", T.StringType(), False),
        T.StructField("metadata_updated_at", T.TimestampType(), False),
    ])
    metadata_values = [
        tuple(row.get(name) for name in (
            "station_id", "station_name", "lat", "lon", "capacity", "region_id",
            "is_current", "metadata_source", "metadata_updated_at",
        )) for row in metadata_rows
    ]
    metadata = spark.createDataFrame(metadata_values, metadata_schema)
    start_candidates = dwd.where(F.col("start_station_id").isNotNull()).select(
        F.col("start_station_id").alias("station_id"), F.col("start_station_name").alias("station_name"),
        F.col("start_lat").alias("lat"), F.col("start_lng").alias("lon"),
        F.col("started_at_local").alias("event_time"), "source_file", "source_row_number",
        F.lit(0).alias("side_priority"),
    )
    end_candidates = dwd.where(F.col("end_station_id").isNotNull()).select(
        F.col("end_station_id").alias("station_id"), F.col("end_station_name").alias("station_name"),
        F.col("end_lat").alias("lat"), F.col("end_lng").alias("lon"),
        F.col("ended_at_local").alias("event_time"), "source_file", "source_row_number",
        F.lit(1).alias("side_priority"),
    )
    candidates = start_candidates.unionByName(end_candidates)
    latest = Window.partitionBy("station_id").orderBy(
        F.col("event_time").desc(), F.col("source_file"), F.col("source_row_number"), F.col("side_priority")
    ).rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)
    historical = candidates.select(
        "station_id",
        F.first("station_name", ignorenulls=True).over(latest).alias("historical_name"),
        F.first("lat", ignorenulls=True).over(latest).alias("historical_lat"),
        F.first("lon", ignorenulls=True).over(latest).alias("historical_lon"),
    ).dropDuplicates(["station_id"])
    dim = metadata.alias("m").join(historical.alias("h"), "station_id", "full")
    dim = dim.select(
        "station_id", F.coalesce("m.station_name", "h.historical_name").alias("station_name"),
        F.coalesce("m.lat", "h.historical_lat").alias("lat"),
        F.coalesce("m.lon", "h.historical_lon").alias("lon"), "m.capacity", "m.region_id",
        F.coalesce("m.is_current", F.lit(False)).alias("is_current"),
        F.when(F.col("m.station_id").isNotNull(), F.lit("GBFS")).otherwise(F.lit("HISTORICAL")).alias("metadata_source"),
        F.coalesce("m.metadata_updated_at", F.to_timestamp(F.lit(built_at_utc))).alias("metadata_updated_at"),
    ).cache()

    summary = dwd.agg(
        F.count("*").alias("dwd"),
        F.sum(F.col("is_valid_station_trip").cast("long")).alias("valid"),
        F.sum(F.col("is_duplicate_ride").cast("long")).alias("duplicate"),
        F.sum((~F.col("is_valid_station_trip")).cast("long")).alias("invalid"),
        F.sum(F.col("start_station_id").isNull().cast("long")).alias("missing_start_station"),
        F.sum(F.col("end_station_id").isNull().cast("long")).alias("missing_end_station"),
        F.sum(
            (F.col("start_lat").isNull() | F.col("start_lng").isNull()).cast("long")
        ).alias("missing_or_invalid_start_coordinate"),
        F.sum(
            (F.col("end_lat").isNull() | F.col("end_lng").isNull()).cast("long")
        ).alias("missing_or_invalid_end_coordinate"),
        F.sum((F.col("duration_seconds") <= 0).cast("long")).alias("non_positive_duration"),
    ).first().asDict()
    dws_counts = {
        "flow": flow.count(), "profile": profile.count(), "od": od.count(),
        "inbound": int(flow.agg(F.sum("inbound_rides")).first()[0] or 0),
        "outbound": int(flow.agg(F.sum("outbound_rides")).first()[0] or 0),
        "od_rides": int(od.agg(F.sum("ride_count")).first()[0] or 0),
    }
    valid_count = int(summary["valid"] or 0)
    if {dws_counts["inbound"], dws_counts["outbound"], dws_counts["od_rides"]} != {valid_count}:
        raise OfflineError(f"DWS conservation failed: valid={valid_count}, totals={dws_counts}")
    endpoint_ids = candidates.select("station_id").distinct()
    metadata_hits = endpoint_ids.join(metadata.select("station_id"), "station_id", "inner").count()
    endpoint_count = endpoint_ids.count()
    quality = {
        "source": manifest["record_count"], **{k: int(v or 0) for k, v in summary.items()},
        **dws_counts, "duplicate_conflict": duplicate_conflicts,
        "invalid_critical_timestamp": 0,
        "dim": dim.count(), "metadata_distinct_historical_ids": endpoint_count,
        "metadata_matched_historical_ids": metadata_hits,
    }
    if quality["source"] != quality["dwd"]:
        raise OfflineError(f"source/DWD count mismatch: {quality['source']} != {quality['dwd']}")
    return {
        "dataset_id": current_dataset_id, "dwd_trip_v1": dwd, "dim_station_v1": dim,
        "dws_station_hourly_flow_v1": flow, "dws_station_hour_profile_v1": profile,
        "dws_station_od_hourly_v1": od, "quality": quality,
    }


def _sanitise_for_tsv(frame, columns: tuple[str, ...]):
    from pyspark.sql import functions as F

    expressions = []
    string_names = {field.name for field in frame.schema.fields if field.dataType.simpleString() == "string"}
    timestamp_names = {
        field.name for field in frame.schema.fields if field.dataType.simpleString() == "timestamp"
    }
    boolean_names = {
        field.name for field in frame.schema.fields if field.dataType.simpleString() == "boolean"
    }
    for name in columns:
        column = F.col(name)
        if name in string_names:
            column = F.regexp_replace(column, r"[\t\r\n]", " ")
        elif name in timestamp_names:
            column = F.date_format(column, "yyyy-MM-dd HH:mm:ss.SSSSSS")
        elif name in boolean_names:
            column = column.cast("tinyint")
        expressions.append(column.alias(name))
    return frame.select(*expressions)


def _release_paths(output_root: str, dataset: str) -> tuple[str, dict[str, str]]:
    release_root = f"{output_root.rstrip('/')}/releases/{dataset}"
    paths = {
        table: f"{release_root}/{relative}"
        for table, relative in WAREHOUSE_TABLE_PATHS.items()
    }
    paths.update({
        f"export_{table}": f"{release_root}/serving_export/{table}"
        for table in SERVING_COLUMNS
    })
    return release_root, paths


def _read_hadoop_json(spark, uri: str) -> dict[str, Any]:
    from py4j.protocol import Py4JJavaError

    jvm = spark._jvm
    path = jvm.org.apache.hadoop.fs.Path(uri)
    stream = path.getFileSystem(spark._jsc.hadoopConfiguration()).open(path)
    scanner = jvm.java.util.Scanner(stream, "UTF-8").useDelimiter("\\A")
    try:
        payload = scanner.next()
    except Py4JJavaError as error:
        raise OfflineError(f"cannot read committed release descriptor {uri}: {error}") from error
    finally:
        scanner.close()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise OfflineError(f"invalid committed release descriptor {uri}: {error}") from error
    if not isinstance(value, dict):
        raise OfflineError(f"committed release descriptor {uri} must be an object")
    return value


def _write_hadoop_json(spark, uri: str, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    jvm = spark._jvm
    path = jvm.org.apache.hadoop.fs.Path(uri)
    stream = path.getFileSystem(spark._jsc.hadoopConfiguration()).create(path, False)
    try:
        stream.write(bytearray(payload))
    finally:
        stream.close()


def _validate_committed_release(
    value: dict[str, Any], expected: dict[str, Any], release_root: str, paths: dict[str, str]
) -> dict[str, Any]:
    for name in ("contract_version", "dataset_id", "metadata_version", "source_months", "ingest_batch_ids"):
        if value.get(name) != expected.get(name):
            raise OfflineError(f"committed release {name} does not match this build")
    if value.get("status") != "PASS" or value.get("release_format") != RELEASE_FORMAT:
        raise OfflineError("committed release is not a supported PASS release")
    if value.get("serving_format") != SERVING_FORMAT:
        raise OfflineError("committed release has an unsupported serving format")
    if value.get("release_root") != release_root or value.get("paths") != paths:
        raise OfflineError("committed release paths do not match its dataset directory")
    actual_counts = value.get("counts")
    expected_counts = expected.get("counts")
    if not isinstance(actual_counts, dict) or not isinstance(expected_counts, dict):
        raise OfflineError("committed release counts must be objects")
    if (
        {name: count for name, count in actual_counts.items() if name != "ods"}
        != {name: count for name, count in expected_counts.items() if name != "ods"}
    ):
        raise OfflineError("committed release counts do not match this build")
    token = value.get("tsv_null_token")
    token_pattern = rf"__CITIBIKE_NULL_{re.escape(str(expected['dataset_id']))}_\d+__"
    if not isinstance(token, str) or re.fullmatch(token_pattern, token) is None:
        raise OfflineError("committed release has an invalid TSV null token")
    return value


def _choose_null_token(formatted: dict[str, Any], dataset: str) -> str:
    from pyspark.sql import functions as F

    suffix = 0
    while True:
        token = f"__CITIBIKE_NULL_{dataset}_{suffix}__"
        collision = False
        for table, frame in formatted.items():
            matches = F.lit(False)
            for name in SERVING_COLUMNS[table]:
                matches = matches | (
                    F.col(name).isNotNull() & (F.col(name).cast("string") == token)
                )
            if frame.where(matches).limit(1).count():
                collision = True
                break
        if not collision:
            return token
        suffix += 1


def write_outputs(
    frames: dict[str, Any], output_root: str, *, evidence: dict[str, Any]
) -> dict[str, Any]:
    from py4j.protocol import Py4JJavaError
    from pyspark.sql import functions as F

    parsed_root = urlparse(output_root)
    if parsed_root.path in {"", "/"}:
        raise OfflineError("output root must be a dedicated directory, not a filesystem root")
    root = output_root.rstrip("/")
    dataset = frames["dataset_id"]
    release_root, paths = _release_paths(root, dataset)
    spark = frames["dim_station_v1"].sparkSession
    jvm = spark._jvm
    configuration = spark._jsc.hadoopConfiguration()
    final_path = jvm.org.apache.hadoop.fs.Path(release_root)
    filesystem = final_path.getFileSystem(configuration)
    scheme = str(filesystem.getUri().getScheme())
    if scheme not in {"file", "hdfs"}:
        raise OfflineError(f"immutable release commit does not support {scheme!r} filesystems")
    descriptor_uri = f"{release_root}/release.json"
    if filesystem.exists(final_path):
        return _validate_committed_release(
            _read_hadoop_json(spark, descriptor_uri), evidence, release_root, paths
        )

    attempt_root = f"{root}/.building/{dataset}-{uuid.uuid4().hex}"
    attempt_path = jvm.org.apache.hadoop.fs.Path(attempt_root)
    filesystem.mkdirs(attempt_path.getParent())
    filesystem.mkdirs(final_path.getParent())
    attempt_paths = {
        table: f"{attempt_root}/{relative}"
        for table, relative in WAREHOUSE_TABLE_PATHS.items()
    }
    versioned = {
        table: frames[table].withColumn("dataset_id", F.lit(dataset))
        for table in SERVING_COLUMNS
    }
    frames["dwd_trip_v1"].write.mode("errorifexists").partitionBy(
        "source_year", "source_month"
    ).parquet(attempt_paths["dwd_trip_v1"])
    versioned["dim_station_v1"].write.mode("errorifexists").parquet(
        attempt_paths["dim_station_v1"]
    )
    versioned["dws_station_hourly_flow_v1"].write.mode("errorifexists").partitionBy(
        "service_date"
    ).parquet(attempt_paths["dws_station_hourly_flow_v1"])
    versioned["dws_station_hour_profile_v1"].write.mode("errorifexists").parquet(
        attempt_paths["dws_station_hour_profile_v1"]
    )
    versioned["dws_station_od_hourly_v1"].write.mode("errorifexists").partitionBy(
        "service_date"
    ).parquet(attempt_paths["dws_station_od_hourly_v1"])
    previous_timezone = spark.conf.get("spark.sql.session.timeZone")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    serving_sort_keys = {
        "dim_station_v1": ("station_id",),
        "dws_station_hourly_flow_v1": ("station_id", "service_date", "hour"),
        "dws_station_hour_profile_v1": ("station_id", "day_of_week", "hour"),
        "dws_station_od_hourly_v1": (
            "service_date", "hour", "from_station_id", "to_station_id",
        ),
    }
    try:
        formatted = {
            table: _sanitise_for_tsv(versioned[table], columns)
            for table, columns in SERVING_COLUMNS.items()
        }
        null_token = _choose_null_token(formatted, dataset)
        for table, columns in SERVING_COLUMNS.items():
            fields = [
                F.coalesce(F.col(name).cast("string"), F.lit(null_token))
                for name in columns
            ]
            export_path = f"{attempt_root}/serving_export/{table}"
            (
                formatted[table]
                .orderBy(*serving_sort_keys[table])
                .select(F.concat_ws("\t", *fields).alias("value"))
                .coalesce(1)
                .write.mode("errorifexists")
                .text(export_path)
            )
    finally:
        spark.conf.set("spark.sql.session.timeZone", previous_timezone)

    committed = {
        **evidence,
        "status": "PASS",
        "release_format": RELEASE_FORMAT,
        "serving_format": SERVING_FORMAT,
        "release_root": release_root,
        "tsv_null_token": null_token,
        "paths": paths,
    }
    _write_hadoop_json(spark, f"{attempt_root}/release.json", committed)
    file_context = jvm.org.apache.hadoop.fs.FileContext.getFileContext(
        filesystem.getUri(), configuration
    )
    rename_options = spark.sparkContext._gateway.new_array(
        jvm.org.apache.hadoop.fs.Options.Rename, 1
    )
    rename_options[0] = jvm.org.apache.hadoop.fs.Options.Rename.NONE
    try:
        file_context.rename(attempt_path, final_path, rename_options)
    except Py4JJavaError as error:
        if not filesystem.exists(final_path):
            raise OfflineError(f"cannot commit immutable release {release_root}: {error}") from error
    return _validate_committed_release(
        _read_hadoop_json(spark, descriptor_uri), evidence, release_root, paths
    )


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise OfflineError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OfflineError(f"invalid UTC timestamp {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OfflineError(f"timestamp must include UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return _parse_utc_timestamp(value, "timestamp").isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _register_warehouse(spark, release_root: str, ddl_path: Path) -> None:
    tables = tuple(WAREHOUSE_TABLE_PATHS)
    existing: list[str] = []
    for table in tables:
        qualified = f"citibike_dw.{table}"
        if not spark.catalog.tableExists(qualified):
            continue
        details = spark.sql(f"DESCRIBE FORMATTED {qualified}").collect()
        properties = {
            str(row[0]).strip().lstrip("#").strip(): str(row[1]).strip()
            for row in details
            if row[0] is not None and row[1] is not None
        }
        rendered = "\n".join("\t".join(str(value) for value in row) for row in details).lower()
        if properties.get("Type", "").upper() != "EXTERNAL" or "parquet" not in rendered:
            raise OfflineError(f"refusing to replace unexpected warehouse table {qualified}")
        existing.append(qualified)

    for qualified in existing:
        spark.sql(
            f"ALTER TABLE {qualified} SET TBLPROPERTIES ('external.table.purge'='false')"
        )
    if any(character in release_root for character in "'\r\n;"):
        raise OfflineError("release root contains characters unsafe for Hive DDL")
    try:
        ddl = ddl_path.read_text(encoding="utf-8")
    except OSError as error:
        raise OfflineError(f"cannot read warehouse DDL {ddl_path}: {error}") from error
    marker = "${hiveconf:release_root}"
    if marker not in ddl:
        raise OfflineError(f"warehouse DDL {ddl_path} has no release_root placeholder")
    ddl = ddl.replace(marker, release_root)
    sql = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    for statement in sql.split(";"):
        if statement.strip():
            spark.sql(statement)
    for table in ("dwd_trip_v1", "dws_station_hourly_flow_v1", "dws_station_od_hourly_v1"):
        spark.sql(f"MSCK REPAIR TABLE citibike_dw.{table}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True, help="RAW root; local input must use file:/// URI")
    parser.add_argument("--source-month", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--hive-table", help="ODS table used only for source/ODS reconciliation")
    parser.add_argument("--built-at", help="UTC build time; deterministic fixture hook")
    parser.add_argument("--published-at", help="UTC release time; defaults to current UTC")
    parser.add_argument(
        "--register-warehouse",
        action="store_true",
        help="rebuild the external Hive aliases at the committed immutable release",
    )
    parser.add_argument(
        "--warehouse-ddl", type=Path, default=Path("hive/warehouse_v1.sql")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    committed_release: dict[str, Any] | None = None
    try:
        parsed_root = urlparse(args.raw_root)
        if not parsed_root.scheme and not args.raw_root.startswith("/"):
            raise OfflineError("local --raw-root must be an explicit file:/// URI")
        manifest = load_manifest(args.manifest, args.source_month)
        metadata_rows = load_metadata(args.metadata)
        metadata_version = sha256_file(args.metadata)
        built_at = _iso_utc(args.built_at)
        preflight_local_files(args.raw_root, args.source_month, manifest)
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

        builder = SparkSession.builder.appName("citibike-offline-v1")
        if args.hive_table or args.register_warehouse:
            builder = builder.enableHiveSupport()
        spark = builder.getOrCreate()
        spark.conf.set("spark.sql.session.timeZone", "America/New_York")
        try:
            ods_count = None
            if args.hive_table:
                year, month = parse_source_month(args.source_month)
                ods = spark.table(args.hive_table).where(
                    (F.col("year") == year) & (F.col("month") == month)
                )
                for station_id in ("start_station_id", "end_station_id"):
                    if ods.schema[station_id].dataType.simpleString() != "string":
                        raise OfflineError(f"ODS column {station_id} must be STRING")
                header_fields = [
                    name for name in SOURCE_FIELDS
                    if name not in {"start_lat", "start_lng", "end_lat", "end_lng"}
                ]
                header_row = F.lit(True)
                for name in header_fields:
                    header_row = header_row & (F.col(name) == name)
                ods_count = ods.where(~header_row).count()
                if ods_count != manifest["record_count"]:
                    raise OfflineError(
                        f"ODS count {ods_count} != source manifest {manifest['record_count']}"
                    )
            frames = build_frames(
                spark, raw_root=args.raw_root, source_month=args.source_month,
                manifest=manifest, metadata_rows=metadata_rows,
                metadata_version=metadata_version, built_at_utc=built_at,
            )
            dates = frames["dws_station_hourly_flow_v1"].agg(
                F.min("service_date").alias("minimum"), F.max("service_date").alias("maximum")
            ).first()
            published_at = _iso_utc(args.published_at)
            pending_evidence = {
                "status": "PASS", "contract_version": CONTRACT_VERSION,
                "dataset_id": frames["dataset_id"], "metadata_version": metadata_version,
                "source_months": [args.source_month], "ingest_batch_ids": [manifest["zip_sha256"]],
                "built_at_utc": built_at, "published_at_utc": published_at,
                "min_service_date": dates["minimum"].isoformat() if dates["minimum"] else None,
                "max_service_date": dates["maximum"].isoformat() if dates["maximum"] else None,
                "counts": {**frames["quality"], "ods": ods_count},
            }
            committed_release = write_outputs(
                frames, args.output_root, evidence=pending_evidence
            )
            if args.register_warehouse:
                _register_warehouse(
                    spark, committed_release["release_root"], args.warehouse_ddl
                )
            _write_evidence(args.evidence, committed_release)
            print(json.dumps(committed_release, ensure_ascii=False, indent=2))
        finally:
            spark.stop()
    except Exception as error:
        failure = {"status": "FAIL", "error_type": type(error).__name__, "error": str(error)}
        if committed_release is not None:
            failure.update({
                "dataset_id": committed_release["dataset_id"],
                "release_committed": True,
                "release_root": committed_release["release_root"],
            })
        try:
            _write_evidence(args.evidence, failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
