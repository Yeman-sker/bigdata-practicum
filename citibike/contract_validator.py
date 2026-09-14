#!/usr/bin/env python3
"""Small, dependency-free validator for the Track C source contracts.

The validator intentionally validates source-shaped files rather than silently
normalising them.  Errors stop the check; warnings are retained as evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import (
    GBFS_FEEDS,
    GBFS_VERSION,
    HISTORICAL_TIME_FORMATS,
    MEMBER_TYPES,
    RIDEABLE_TYPES,
    SOURCE_FIELDS,
)

STATION_ID_RE = re.compile(r"^[^\s]+$")


class Report:
    def __init__(self, kind: str, path: str):
        self.kind, self.path = kind, path
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.metrics: dict[str, Any] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def result(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "status": "FAIL" if self.errors else "PASS",
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def parse_time(value: str) -> bool:
    return any(_try_datetime(value, fmt) for fmt in HISTORICAL_TIME_FORMATS)


def _try_datetime(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def is_numeric(value: Any) -> bool:
    try:
        if isinstance(value, bool):
            return False
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def is_posix(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_json_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_json_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_json(path: Path, report: Report) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            report.error("top-level JSON value must be an object")
            return None
        return value
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"cannot read JSON: {exc}")
        return None


def extract_records(data: dict[str, Any]) -> list[Any]:
    value = data.get("data", data)
    if isinstance(value, dict):
        for key in ("stations", "vehicle_types", "feeds"):
            if isinstance(value.get(key), list):
                return value[key]
        # GBFS discovery wraps feeds in one or more language objects.
        for language in value.values():
            if isinstance(language, dict) and isinstance(language.get("feeds"), list):
                return language["feeds"]
    return value if isinstance(value, list) else []


def validate_historical(path: Path) -> Report:
    report = Report("historical_trip_csv", str(path))
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            duplicates = sorted({x for x in header if header.count(x) > 1})
            missing = [x for x in SOURCE_FIELDS if x not in header]
            unknown = [x for x in header if x not in SOURCE_FIELDS]
            if duplicates:
                report.error(f"duplicate columns: {duplicates}")
            if missing:
                report.error(f"missing required columns: {missing}")
            if unknown:
                report.warn(f"unknown columns retained for review: {unknown}")
            rows = 0
            nulls = {
                "ride_id": 0, "start_station_id": 0, "end_station_id": 0,
                "start_station_name": 0, "end_station_name": 0,
            }
            parse_failures = {"started_at": 0, "ended_at": 0}
            null_timestamps = {"started_at": 0, "ended_at": 0}
            invalid_coords = 0
            unknown_rideable: set[str] = set()
            unknown_member: set[str] = set()
            rideable_values: set[str] = set()
            member_values: set[str] = set()
            ride_ids: set[str] = set()
            duplicate_ids = 0
            required_nulls = {"ride_id": 0, "rideable_type": 0, "member_casual": 0}
            coordinate_nulls = {field: 0 for field in ("start_lat", "start_lng", "end_lat", "end_lng")}
            out_of_range_coords = 0
            nonpositive_duration_count = 0
            for line, row in enumerate(reader, start=2):
                rows += 1
                for field in nulls:
                    if not (row.get(field) or "").strip():
                        nulls[field] += 1
                ride_id = (row.get("ride_id") or "").strip()
                for field in required_nulls:
                    if not (row.get(field) or "").strip():
                        required_nulls[field] += 1
                null_required = [field for field in required_nulls if not (row.get(field) or "").strip()]
                if null_required:
                    report.error(f"row {line} has null required source fields: {null_required}")
                if ride_id in ride_ids and ride_id:
                    duplicate_ids += 1
                if ride_id:
                    ride_ids.add(ride_id)
                for field in parse_failures:
                    value = (row.get(field) or "").strip()
                    if not value:
                        null_timestamps[field] += 1
                    elif not parse_time(value):
                        parse_failures[field] += 1
                started = (row.get("started_at") or "").strip()
                ended = (row.get("ended_at") or "").strip()
                if started and ended and parse_time(started) and parse_time(ended):
                    start_dt = next(datetime.strptime(started, fmt) for fmt in HISTORICAL_TIME_FORMATS if _try_datetime(started, fmt))
                    end_dt = next(datetime.strptime(ended, fmt) for fmt in HISTORICAL_TIME_FORMATS if _try_datetime(ended, fmt))
                    if (end_dt - start_dt).total_seconds() <= 0:
                        nonpositive_duration_count += 1
                for field in ("start_lat", "start_lng", "end_lat", "end_lng"):
                    value = (row.get(field) or "").strip()
                    if not value:
                        coordinate_nulls[field] += 1
                    elif not is_numeric(value):
                        invalid_coords += 1
                    else:
                        coordinate = float(value)
                        if (field.endswith("lat") and not -90 <= coordinate <= 90) or (field.endswith("lng") and not -180 <= coordinate <= 180):
                            out_of_range_coords += 1
                rideable = (row.get("rideable_type") or "").strip()
                member = (row.get("member_casual") or "").strip()
                if rideable:
                    rideable_values.add(rideable)
                if member:
                    member_values.add(member)
                if rideable and rideable not in RIDEABLE_TYPES:
                    unknown_rideable.add(rideable)
                if member and member not in MEMBER_TYPES:
                    unknown_member.add(member)
            report.metrics = {
                "rows": rows,
                "columns": header,
                "null_counts": nulls,
                "duplicate_ride_id_count": duplicate_ids,
                "timestamp_parse_failures": parse_failures,
                "timestamp_null_counts": null_timestamps,
                "invalid_coordinate_values": invalid_coords,
                "coordinate_null_counts": coordinate_nulls,
                "coordinate_out_of_range_count": out_of_range_coords,
                "nonpositive_duration_count": nonpositive_duration_count,
                "required_null_counts": required_nulls,
                "null_rates": {field: count / rows if rows else 0 for field, count in nulls.items()},
                "rideable_type_values": sorted(rideable_values),
                "member_casual_values": sorted(member_values),
                "unknown_rideable_type_values": sorted(unknown_rideable),
                "unknown_member_casual_values": sorted(unknown_member),
            }
            if unknown_rideable:
                report.warn(f"unknown rideable_type values: {sorted(unknown_rideable)}")
            if unknown_member:
                report.warn(f"unknown member_casual values: {sorted(unknown_member)}")
            if any(parse_failures.values()) or any(null_timestamps.values()):
                report.error(
                    f"timestamp parse/null failures: parse={parse_failures}, null={null_timestamps}"
                )
            if invalid_coords:
                report.warn(f"invalid coordinate values: {invalid_coords}")
            if out_of_range_coords:
                report.warn(f"out-of-range coordinate values: {out_of_range_coords}")
    except OSError as exc:
        report.error(f"cannot read CSV: {exc}")
    return report


def validate_discovery(path: Path) -> Report:
    report = Report("gbfs_discovery", str(path))
    data = load_json(path, report)
    if data is None:
        return report
    validate_gbfs_envelope(data, report, require_version=True)
    version = data.get("version")
    feeds = extract_records(data)
    names = {item.get("name") for item in feeds if isinstance(item, dict)}
    required = set(GBFS_FEEDS)
    missing = sorted(required - names)
    if missing:
        report.error(f"missing required feeds: {missing}")
    malformed_entries = [index for index, item in enumerate(feeds, start=1) if not isinstance(item, dict)]
    if malformed_entries:
        report.error(f"discovery feed records must be objects: records {malformed_entries}")
    invalid_urls = [
        item.get("name", "<unnamed>")
        for item in feeds
        if isinstance(item, dict)
        and (not isinstance(item.get("url"), str) or not _valid_http_url(item["url"]))
    ]
    if invalid_urls:
        report.error(f"required/declared feed URLs must be non-empty http(s) URLs: {invalid_urls}")
    report.metrics = {"version": version, "feed_names": sorted(x for x in names if x), "invalid_feed_urls": invalid_urls, "malformed_feed_records": malformed_entries}
    return report


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and bool(parsed.hostname)


def validate_gbfs_envelope(data: dict[str, Any], report: Report, require_version: bool = False) -> None:
    if not isinstance(data.get("data"), dict):
        report.error("GBFS data must be an object")
    if not is_posix(data.get("last_updated")):
        report.error("top-level last_updated must be a non-negative POSIX integer")
    if "ttl" in data and (not is_json_integer(data["ttl"]) or data["ttl"] < 0):
        report.error("top-level ttl must be a non-negative integer")
    if require_version and (not isinstance(data.get("version"), str) or data.get("version") != GBFS_VERSION):
        report.error(f"expected GBFS version string {GBFS_VERSION!r}, got {data.get('version')!r}")


def validate_gbfs(path: Path, kind: str) -> Report:
    report = Report(kind, str(path))
    data = load_json(path, report)
    if data is None:
        return report
    validate_gbfs_envelope(data, report)
    rows = extract_records(data)
    required = {
        "station_information": {"station_id", "name", "lat", "lon"},
        "station_status": {"station_id", "num_bikes_available", "is_installed", "is_renting", "is_returning", "last_reported"},
        "vehicle_types": {"vehicle_type_id"},
    }[kind]
    missing = sorted(required - set().union(*(set(x) for x in rows if isinstance(x, dict)))) if rows else sorted(required)
    if missing:
        report.error(f"missing required fields: {missing}")
    station_ids = []
    invalid_availability_types = 0
    negative_availability_values = 0
    bad_types = 0
    bad_timestamps = 0
    bad_coordinates = 0
    bad_capacity = 0
    bad_booleans = 0
    required_nulls = 0
    optional_missing = 0
    optional_fields = {
        "station_information": ("capacity", "region_id"),
        "station_status": ("num_bikes_disabled", "num_docks_available", "num_docks_disabled"),
        "vehicle_types": ("name", "form_factor"),
    }[kind]
    optional_missing_counts = {field: 0 for field in optional_fields}
    coordinate_nulls = {"lat": 0, "lon": 0}
    coordinate_out_of_range = 0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            report.error(f"record {index} is not an object")
            continue
        record_missing = sorted(required - set(row))
        if record_missing:
            report.error(f"record {index} missing required fields: {record_missing}")
        null_required = sorted(field for field in required if field in row and row[field] is None)
        if null_required:
            required_nulls += len(null_required)
            report.error(f"record {index} has null required fields: {null_required}")
        for field in optional_fields:
            if field not in row:
                optional_missing += 1
                optional_missing_counts[field] += 1
        if "station_id" in row:
            station_id = row["station_id"]
            if not isinstance(station_id, str) or not STATION_ID_RE.match(station_id):
                bad_types += 1
            else:
                station_ids.append(station_id)
        if kind == "station_information":
            for field in ("lat", "lon"):
                if field not in row or row[field] is None:
                    coordinate_nulls[field] += 1
                elif not is_json_number(row[field]):
                    bad_coordinates += 1
                elif field == "lat" and not -90 <= row[field] <= 90:
                    coordinate_out_of_range += 1
                elif field == "lon" and not -180 <= row[field] <= 180:
                    coordinate_out_of_range += 1
            if "capacity" in row and row["capacity"] is not None and (not is_json_integer(row["capacity"]) or row["capacity"] < 0):
                bad_capacity += 1
            if "name" in row and row["name"] is not None and (not isinstance(row["name"], str) or not row["name"].strip()):
                report.error(f"record {index} name must be non-empty STRING")
        if kind == "station_status":
            for field in ("num_bikes_available", "num_bikes_disabled", "num_docks_available", "num_docks_disabled"):
                if field in row and row[field] is not None and (not is_json_integer(row[field]) or row[field] < 0):
                    if not is_json_integer(row[field]):
                        invalid_availability_types += 1
                    elif row[field] < 0:
                        negative_availability_values += 1
            if "last_reported" in row and row["last_reported"] is not None and not is_posix(row["last_reported"]):
                bad_timestamps += 1
            for field in ("is_installed", "is_renting", "is_returning"):
                if field in row and row[field] not in (0, 1, False, True):
                    bad_booleans += 1
    if bad_types:
        report.error(f"station_id must be STRING and non-empty: {bad_types} invalid records")
    if invalid_availability_types:
        report.error(f"availability fields must be JSON integers: {invalid_availability_types} invalid values")
    if negative_availability_values:
        report.warn(f"negative availability values observed: {negative_availability_values}")
    if bad_timestamps:
        report.error(f"invalid POSIX last_reported values: {bad_timestamps}")
    if bad_coordinates:
        report.warn(f"invalid lat/lon values: {bad_coordinates}")
    if coordinate_out_of_range:
        report.warn(f"out-of-range lat/lon values: {coordinate_out_of_range}")
    if bad_capacity:
        report.warn(f"negative/non-integer capacity values: {bad_capacity}")
    if bad_booleans:
        report.error(f"service flags must be boolean or 0/1: {bad_booleans} invalid values")
    if optional_missing:
        report.warn(f"optional GBFS fields missing: {optional_missing_counts}")
    if kind == "vehicle_types":
        bad_vehicle_ids = sum(not isinstance(row.get("vehicle_type_id"), str) or not row.get("vehicle_type_id", "").strip() for row in rows if isinstance(row, dict))
        if bad_vehicle_ids:
            report.error(f"vehicle_type_id must be non-empty STRING: {bad_vehicle_ids} invalid records")
    else:
        bad_vehicle_ids = 0
    report.metrics = {
        "records": len(rows),
        "station_id_type": "STRING",
        "distinct_station_ids": len(set(station_ids)),
        "invalid_station_id_records": bad_types,
        "invalid_availability_types": invalid_availability_types,
        "negative_availability_values": negative_availability_values,
        "invalid_posix_timestamps": bad_timestamps,
        "invalid_coordinate_values": bad_coordinates,
        "invalid_capacity_values": bad_capacity,
        "invalid_service_flags": bad_booleans,
        "null_required_fields": required_nulls,
        "invalid_vehicle_type_id_records": bad_vehicle_ids,
        "optional_missing_counts": optional_missing_counts,
        "optional_missing_total": optional_missing,
        "coordinate_null_counts": coordinate_nulls,
        "coordinate_out_of_range_count": coordinate_out_of_range,
    }
    return report


def validate(kind: str, path: Path) -> Report:
    if kind == "historical":
        return validate_historical(path)
    if kind == "discovery":
        return validate_discovery(path)
    return validate_gbfs(path, kind)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate source contracts")
    parser.add_argument("kind", choices=["historical", "discovery", "station_information", "station_status", "vehicle_types"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    result = validate(args.kind, args.path).result()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
