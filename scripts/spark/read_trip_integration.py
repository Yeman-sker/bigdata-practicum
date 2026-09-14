#!/usr/bin/env python3
"""Run the Day 1 Spark read and Historical Trips contract gate."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SOURCE_FIELDS = (
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
)
COORDINATE_FIELDS = ("start_lat", "start_lng", "end_lat", "end_lng")
STATION_ID_FIELDS = ("start_station_id", "end_station_id")
REQUIRED_STRING_FIELDS = (
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "member_casual",
)
TIME_PATTERNS = (
    "yyyy-MM-dd HH:mm:ss.SSSSSS",
    "yyyy-MM-dd HH:mm:ss.SSS",
    "yyyy-MM-dd HH:mm:ss",
    "MM/dd/yyyy HH:mm:ss.SSS",
    "MM/dd/yyyy HH:mm:ss",
)
LOCAL_TIME_ZONE = "America/New_York"
SOURCE_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")
TARGET_TYPES = {field: "double" for field in COORDINATE_FIELDS}
TARGET_TYPES.update({field: "string" for field in SOURCE_FIELDS if field not in TARGET_TYPES})


class IntegrationError(RuntimeError):
    """Raised when the input cannot satisfy the Day 1 read contract."""


def parse_source_month(value: str) -> tuple[int, int]:
    match = SOURCE_MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise IntegrationError(f"source month must use YYYY-MM, got {value!r}")
    return int(match.group("year")), int(match.group("month"))


def load_expected_count(
    manifest_path: Path | None,
    expected_count: int | None,
    source_month: str | None,
) -> int | None:
    """Read Track A's count and reject contradictory command-line evidence."""

    manifest_count = None
    if manifest_path is not None:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise IntegrationError(f"cannot read manifest {manifest_path}: {error}") from error
        if not isinstance(payload, dict):
            raise IntegrationError("source manifest top-level value must be an object")
        if source_month is not None and payload.get("source_month") != source_month:
            raise IntegrationError(
                "manifest source_month does not match the requested month: "
                f"{payload.get('source_month')!r} != {source_month!r}"
            )
        value = payload.get("record_count")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise IntegrationError("source manifest record_count must be a non-negative integer")
        manifest_count = value

    if expected_count is not None and (expected_count < 0):
        raise IntegrationError("expected count must be non-negative")
    if manifest_count is not None and expected_count is not None and manifest_count != expected_count:
        raise IntegrationError(
            f"manifest record_count {manifest_count} != expected count {expected_count}"
        )
    return expected_count if expected_count is not None else manifest_count


def build_source_schema():
    """Return the raw CSV schema without importing PySpark during CI discovery."""

    from pyspark.sql.types import StringType, StructField, StructType

    return StructType([StructField(field, StringType(), True) for field in SOURCE_FIELDS])


def _cast_source_columns(frame, functions):
    expressions = []
    for field in SOURCE_FIELDS:
        data_type = TARGET_TYPES[field]
        expressions.append(functions.col(field).cast(data_type).alias(field))
    for field in COORDINATE_FIELDS:
        expressions.append(functions.col(field).cast("string").alias(f"__raw_{field}"))
    return frame.select(*expressions)


def _read_input(spark, input_path: str | None, hive_table: str | None, source_month: str | None):
    from pyspark.sql import functions as F

    if input_path is not None:
        header = (
            spark.read.option("header", "true")
            .option("inferSchema", "false")
            .csv(input_path)
        )
        if header.columns != list(SOURCE_FIELDS):
            raise IntegrationError(
                f"expected the 13-column Historical Trips header, got {header.columns!r}"
            )
        raw = (
            spark.read.option("header", "true")
            .option("mode", "PERMISSIVE")
            .option("enforceSchema", "false")
            .schema(build_source_schema())
            .csv(input_path)
        )
        return _cast_source_columns(raw, F), None, "csv"

    if hive_table is None:
        raise IntegrationError("one of --input or --hive-table is required")
    source = spark.table(hive_table)
    if source_month is not None:
        year, month = parse_source_month(source_month)
        if "year" not in source.columns or "month" not in source.columns:
            raise IntegrationError(
                "Hive input must expose year/month partitions when --source-month is used"
            )
        source = source.where((F.col("year") == year) & (F.col("month") == month))
    missing = sorted(set(SOURCE_FIELDS) - set(source.columns))
    if missing:
        raise IntegrationError(f"Hive table is missing source columns: {missing}")
    for field in STATION_ID_FIELDS:
        if source.schema[field].dataType.simpleString() != "string":
            raise IntegrationError(f"Hive column {field} must be STRING")
    return _cast_source_columns(source, F), source.count(), "hive_table"


def _timestamp_expression(field: str, functions):
    return functions.coalesce(
        *(functions.to_timestamp(functions.col(field), pattern) for pattern in TIME_PATTERNS)
    )


def _with_time_columns(frame, functions):
    started = _timestamp_expression("started_at", functions)
    ended = _timestamp_expression("ended_at", functions)
    frame = frame.withColumn("started_at_local", started).withColumn("ended_at_local", ended)
    frame = frame.withColumn("service_date", functions.to_date("started_at_local"))
    frame = frame.withColumn("start_hour", functions.hour("started_at_local"))
    frame = frame.withColumn(
        "day_of_week",
        ((functions.dayofweek("started_at_local") + 5) % 7) + 1,
    )
    return frame.withColumn("is_weekend", functions.col("day_of_week").isin(6, 7))


def _blank(functions, field: str):
    value = functions.col(field)
    return value.isNull() | (functions.length(functions.trim(value)) == 0)


def _raw_blank(functions, field: str):
    return _blank(functions, f"__raw_{field}")


def _sum_when(functions, condition, alias: str):
    return functions.sum(functions.when(condition, 1).otherwise(0)).cast("long").alias(alias)


def _as_int(value: Any) -> int:
    return int(value or 0)


def _schema_summary(frame) -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "type": field.dataType.simpleString(),
            "nullable": field.nullable,
        }
        for field in frame.schema.fields
    ]


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _rows_as_dicts(frame) -> list[dict[str, Any]]:
    return [
        {key: _json_safe(value) for key, value in row.asDict().items()}
        for row in frame.collect()
    ]


def _reconcile_counts(source_count: int | None, hive_count: int | None, spark_count: int) -> dict[str, Any]:
    counts = {
        "source": source_count,
        "hive": hive_count,
        "spark": spark_count,
    }
    known = [value for value in counts.values() if value is not None]
    if len(known) < 2:
        status = "NOT_RUN"
    elif len(set(known)) != 1:
        status = "FAIL"
    elif len(known) == 3:
        status = "PASS"
    else:
        status = "PARTIAL"
    return {"status": status, "counts": counts}


def run_integration(
    spark,
    *,
    input_path: str | None = None,
    hive_table: str | None = None,
    source_month: str | None = None,
    expected_count: int | None = None,
    sample_rows: int = 10,
    display: bool = True,
) -> dict[str, Any]:
    """Read one CSV dataset or Hive partition and return the gate evidence."""

    if sample_rows < 0:
        raise IntegrationError("sample rows must be non-negative")
    if input_path is not None and hive_table is not None:
        raise IntegrationError("--input and --hive-table are mutually exclusive")
    if source_month is not None:
        parse_source_month(source_month)

    frame, hive_count, input_mode = _read_input(spark, input_path, hive_table, source_month)
    if frame.columns[: len(SOURCE_FIELDS)] != list(SOURCE_FIELDS):
        raise IntegrationError(f"mapped columns do not match the source contract: {frame.columns!r}")
    from pyspark.sql import functions as F

    frame = _with_time_columns(frame, F)

    non_empty_start_station = ~_blank(F, "start_station_id")
    non_empty_end_station = ~_blank(F, "end_station_id")
    aggregate_expressions = [F.count("*").alias("total_rows")]
    for field in STATION_ID_FIELDS:
        aggregate_expressions.append(_sum_when(F, _blank(F, field), f"null_{field}"))
    for field in REQUIRED_STRING_FIELDS:
        aggregate_expressions.append(_sum_when(F, _blank(F, field), f"null_{field}"))
    for raw_field, parsed_field in (("started_at", "started_at_local"), ("ended_at", "ended_at_local")):
        aggregate_expressions.append(
            _sum_when(F, ~_blank(F, raw_field) & F.col(parsed_field).isNull(), f"invalid_{raw_field}")
        )
    for field in COORDINATE_FIELDS:
        coordinate = F.col(field)
        raw_value_present = ~_raw_blank(F, field)
        invalid = raw_value_present & (coordinate.isNull() | F.isnan(coordinate))
        aggregate_expressions.append(_sum_when(F, coordinate.isNull(), f"null_{field}"))
        aggregate_expressions.append(_sum_when(F, invalid, f"invalid_{field}"))
        if field.endswith("lat"):
            out_of_range = coordinate.isNotNull() & ~F.isnan(coordinate) & (
                (coordinate < -90) | (coordinate > 90)
            )
        else:
            out_of_range = coordinate.isNotNull() & ~F.isnan(coordinate) & (
                (coordinate < -180) | (coordinate > 180)
            )
        aggregate_expressions.append(_sum_when(F, out_of_range, f"out_of_range_{field}"))
    aggregate_expressions.extend(
        [
            F.date_format(F.min("started_at_local"), "yyyy-MM-dd HH:mm:ss.SSS").alias(
                "min_started_at_local"
            ),
            F.date_format(F.max("started_at_local"), "yyyy-MM-dd HH:mm:ss.SSS").alias(
                "max_started_at_local"
            ),
            F.min("start_hour").alias("min_start_hour"),
            F.max("start_hour").alias("max_start_hour"),
            F.min("day_of_week").alias("min_day_of_week"),
            F.max("day_of_week").alias("max_day_of_week"),
            _sum_when(F, F.col("service_date").isNull(), "null_service_date"),
            F.sort_array(
                F.collect_set(F.when(~_blank(F, "rideable_type"), F.col("rideable_type")))
            ).alias("rideable_type_values"),
            F.sort_array(
                F.collect_set(F.when(~_blank(F, "member_casual"), F.col("member_casual")))
            ).alias("member_casual_values"),
            F.sort_array(
                F.collect_set(F.when(non_empty_start_station, F.col("start_station_id")))
            ).alias("start_station_id_examples"),
            F.sort_array(
                F.collect_set(F.when(non_empty_end_station, F.col("end_station_id")))
            ).alias("end_station_id_examples"),
            _sum_when(
                F,
                (~_blank(F, "rideable_type"))
                & ~F.col("rideable_type").isin("classic_bike", "electric_bike"),
                "unknown_rideable_type",
            ),
            _sum_when(
                F,
                (~_blank(F, "member_casual"))
                & ~F.col("member_casual").isin("casual", "member"),
                "unknown_member_casual",
            ),
        ]
    )
    metrics = frame.agg(*aggregate_expressions).collect()[0].asDict()
    total_rows = _as_int(metrics["total_rows"])
    for key, value in list(metrics.items()):
        if key.startswith(("null_", "invalid_", "out_of_range_", "unknown_")):
            metrics[key] = _as_int(value)
    for key in (
        "rideable_type_values",
        "member_casual_values",
        "start_station_id_examples",
        "end_station_id_examples",
    ):
        metrics[key] = (metrics[key] or [])[:10]

    sample = frame.select(*SOURCE_FIELDS).limit(sample_rows)
    time_sample = frame.select(
        F.date_format("started_at_local", "yyyy-MM-dd HH:mm:ss.SSS").alias("started_at_local"),
        F.date_format("ended_at_local", "yyyy-MM-dd HH:mm:ss.SSS").alias("ended_at_local"),
        F.col("service_date").cast("string").alias("service_date"),
        F.col("start_hour"),
        F.col("day_of_week"),
        F.col("is_weekend"),
    ).limit(sample_rows)
    if display:
        print("=== Spark printSchema ===")
        frame.select(*SOURCE_FIELDS).printSchema()
        print(f"=== Spark count: {total_rows} ===")
        print("=== Spark sample records ===")
        sample.show(truncate=False)
        print("=== Derived time sample ===")
        time_sample.show(truncate=False)

    schema_summary = _schema_summary(frame.select(*SOURCE_FIELDS))
    types = {item["name"]: item["type"] for item in schema_summary}
    required_nulls = {field: metrics[f"null_{field}"] for field in REQUIRED_STRING_FIELDS}
    coordinate_quality = {
        field: {
            "type": types[field],
            "null_count": metrics[f"null_{field}"],
            "invalid_numeric_count": metrics[f"invalid_{field}"],
            "out_of_range_count": metrics[f"out_of_range_{field}"],
        }
        for field in COORDINATE_FIELDS
    }
    count_reconciliation = _reconcile_counts(expected_count, hive_count, total_rows)
    gate_checks = {
        "source_columns_complete": "PASS",
        "station_id_string": (
            "PASS"
            if all(types[field] == "string" for field in STATION_ID_FIELDS)
            else "FAIL"
        ),
        "coordinates_double": (
            "PASS" if all(types[field] == "double" for field in COORDINATE_FIELDS) else "FAIL"
        ),
        "required_fields_non_null": "PASS" if not any(required_nulls.values()) else "FAIL",
        "time_parse": (
            "PASS"
            if not metrics["invalid_started_at"] and not metrics["invalid_ended_at"]
            else "FAIL"
        ),
        "count_reconciliation": count_reconciliation["status"],
    }
    deviations = []
    for field, quality in coordinate_quality.items():
        if quality["invalid_numeric_count"] or quality["out_of_range_count"]:
            deviations.append({"field": field, **quality})
    for field, key in (
        ("rideable_type", "unknown_rideable_type"),
        ("member_casual", "unknown_member_casual"),
    ):
        if metrics[key]:
            deviations.append({"field": field, "unknown_count": metrics[key]})
    blocking_checks = {key: value for key, value in gate_checks.items() if value == "FAIL"}
    summary = {
        "status": "FAIL" if blocking_checks else "PASS",
        "input": {
            "mode": input_mode,
            "value": input_path or hive_table,
            "source_month": source_month,
            "timezone": LOCAL_TIME_ZONE,
        },
        "schema": {
            "source_columns": list(SOURCE_FIELDS),
            "summary": schema_summary,
            "station_id_types": {field: types[field] for field in STATION_ID_FIELDS},
        },
        "counts": count_reconciliation,
        "data_quality": {
            "total_rows": total_rows,
            "null_start_station_id": metrics["null_start_station_id"],
            "null_end_station_id": metrics["null_end_station_id"],
            "invalid_started_at": metrics["invalid_started_at"],
            "invalid_ended_at": metrics["invalid_ended_at"],
            "min_started_at": metrics["min_started_at_local"],
            "max_started_at": metrics["max_started_at_local"],
            "rideable_type_distinct": metrics["rideable_type_values"],
            "member_casual_distinct": metrics["member_casual_values"],
            "required_field_nulls": required_nulls,
            "station_id_examples": {
                "start_station_id": metrics["start_station_id_examples"],
                "end_station_id": metrics["end_station_id_examples"],
            },
            "coordinates": coordinate_quality,
            "unknown_enum_counts": {
                "rideable_type": metrics["unknown_rideable_type"],
                "member_casual": metrics["unknown_member_casual"],
            },
        },
        "time_validation": {
            "timezone": LOCAL_TIME_ZONE,
            "accepted_patterns": list(TIME_PATTERNS),
            "derived_fields": [
                "started_at_local",
                "ended_at_local",
                "service_date",
                "start_hour",
                "day_of_week",
                "is_weekend",
            ],
            "start_hour_range": [metrics["min_start_hour"], metrics["max_start_hour"]],
            "day_of_week_range": [metrics["min_day_of_week"], metrics["max_day_of_week"]],
            "null_service_date": metrics["null_service_date"],
        },
        "gate_checks": gate_checks,
        "contract_deviations": deviations,
        "sample_records": _rows_as_dicts(sample),
        "derived_time_sample": _rows_as_dicts(time_sample),
    }
    if display:
        print("=== Integration summary (JSON) ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", help="CSV file/directory or HDFS CSV path")
    inputs.add_argument("--hive-table", help="Hive table, for example citibike_ods.ods_trip_raw")
    parser.add_argument("--source-month", help="partition month in YYYY-MM format")
    parser.add_argument("--manifest", type=Path, help="Track A manifest used for source count")
    parser.add_argument("--expected-count", type=int, help="expected source row count")
    parser.add_argument("--sample-rows", type=int, default=10)
    parser.add_argument("--output-json", type=Path, help="optional path for the JSON summary")
    parser.add_argument("--app-name", default="Day1-Spark-Trip-Integration")
    return parser.parse_args()


def build_spark_session(app_name: str, use_hive: bool):
    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", LOCAL_TIME_ZONE)
        .config("spark.sql.warehouse.dir", "/tmp/citibike-day1/spark-warehouse")
    )
    if use_hive:
        builder = builder.enableHiveSupport()
    return builder.getOrCreate()


def main() -> int:
    args = parse_args()
    try:
        expected_count = load_expected_count(args.manifest, args.expected_count, args.source_month)
        spark = build_spark_session(args.app_name, args.hive_table is not None)
        try:
            summary = run_integration(
                spark,
                input_path=args.input,
                hive_table=args.hive_table,
                source_month=args.source_month,
                expected_count=expected_count,
                sample_rows=args.sample_rows,
            )
        finally:
            spark.stop()
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return 0 if summary["status"] == "PASS" else 1
    except (IntegrationError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
