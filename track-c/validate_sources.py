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

HISTORICAL_COLUMNS = [
    "ride_id", "rideable_type", "started_at", "ended_at",
    "start_station_name", "start_station_id", "end_station_name",
    "end_station_id", "start_lat", "start_lng", "end_lat", "end_lng",
    "member_casual",
]
RIDEABLE_TYPES = {"classic_bike", "electric_bike", "electric_scooter"}
MEMBER_TYPES = {"member", "casual"}
TIME_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
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
    return any(_try_datetime(value, fmt) for fmt in TIME_FORMATS)


def _try_datetime(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def number(value: Any, integer: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return False
        f = float(value)
        return f.is_integer() if integer else True
    except (TypeError, ValueError):
        return False


def load_json(path: Path, report: Report) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"cannot read JSON: {exc}")
        return None


def payload(data: dict[str, Any]) -> list[dict[str, Any]]:
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
            missing = [x for x in HISTORICAL_COLUMNS if x not in header]
            unknown = [x for x in header if x not in HISTORICAL_COLUMNS]
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
            ride_ids: set[str] = set()
            duplicate_ids = 0
            for line, row in enumerate(reader, start=2):
                rows += 1
                for field in nulls:
                    if not (row.get(field) or "").strip():
                        nulls[field] += 1
                ride_id = (row.get("ride_id") or "").strip()
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
                for field in ("start_lat", "start_lng", "end_lat", "end_lng"):
                    value = (row.get(field) or "").strip()
                    if value and not number(value):
                        invalid_coords += 1
                rideable = (row.get("rideable_type") or "").strip()
                member = (row.get("member_casual") or "").strip()
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
                "unknown_rideable_type": sorted(unknown_rideable),
                "unknown_member_casual": sorted(unknown_member),
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
    except OSError as exc:
        report.error(f"cannot read CSV: {exc}")
    return report


def validate_discovery(path: Path) -> Report:
    report = Report("gbfs_discovery", str(path))
    data = load_json(path, report)
    if data is None:
        return report
    version = str(data.get("version", ""))
    if version != "2.3":
        report.error(f"expected GBFS version 2.3, got {version!r}")
    feeds = payload(data)
    names = {item.get("name") for item in feeds if isinstance(item, dict)}
    required = {"station_information", "station_status", "vehicle_types"}
    missing = sorted(required - names)
    if missing:
        report.error(f"missing required feeds: {missing}")
    report.metrics = {"version": version, "feed_names": sorted(x for x in names if x)}
    return report


def validate_gbfs(path: Path, kind: str) -> Report:
    report = Report(kind, str(path))
    data = load_json(path, report)
    if data is None:
        return report
    rows = payload(data)
    required = {
        "station_information": {"station_id", "name", "lat", "lon"},
        "station_status": {"station_id", "num_bikes_available", "is_installed", "is_renting", "is_returning"},
        "vehicle_types": {"vehicle_type_id"},
    }[kind]
    missing = sorted(required - set().union(*(set(x) for x in rows if isinstance(x, dict)))) if rows else sorted(required)
    if missing:
        report.error(f"missing required fields: {missing}")
    station_ids = []
    negative_counts = 0
    bad_types = 0
    bad_timestamps = 0
    bad_coordinates = 0
    bad_capacity = 0
    bad_booleans = 0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            report.error(f"record {index} is not an object")
            continue
        record_missing = sorted(required - set(row))
        if record_missing:
            report.error(f"record {index} missing required fields: {record_missing}")
        if "station_id" in row:
            station_id = row["station_id"]
            if not isinstance(station_id, str) or not STATION_ID_RE.match(station_id):
                bad_types += 1
            else:
                station_ids.append(station_id)
        if kind == "station_information":
            for field in ("lat", "lon"):
                if field in row and not number(row[field]):
                    bad_coordinates += 1
            if "capacity" in row and row["capacity"] is not None and (not number(row["capacity"], True) or int(row["capacity"]) < 0):
                bad_capacity += 1
        if kind == "station_status":
            for field in ("num_bikes_available", "num_bikes_disabled", "num_docks_available", "num_docks_disabled"):
                if field in row and row[field] is not None and (not number(row[field], True) or int(row[field]) < 0):
                    negative_counts += 1
            if "last_reported" in row and row["last_reported"] is not None and not number(row["last_reported"], True):
                bad_timestamps += 1
            for field in ("is_installed", "is_renting", "is_returning"):
                if field in row and row[field] not in (0, 1, False, True):
                    bad_booleans += 1
    if bad_types:
        report.error(f"station_id must be STRING and non-empty: {bad_types} invalid records")
    if negative_counts:
        report.warn(f"negative/non-integer availability values: {negative_counts}")
    if bad_timestamps:
        report.error(f"invalid POSIX last_reported values: {bad_timestamps}")
    if bad_coordinates:
        report.warn(f"invalid lat/lon values: {bad_coordinates}")
    if bad_capacity:
        report.warn(f"negative/non-integer capacity values: {bad_capacity}")
    if bad_booleans:
        report.error(f"service flags must be boolean or 0/1: {bad_booleans} invalid values")
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
        "negative_or_invalid_counts": negative_counts,
        "invalid_posix_timestamps": bad_timestamps,
        "invalid_coordinate_values": bad_coordinates,
        "invalid_capacity_values": bad_capacity,
        "invalid_service_flags": bad_booleans,
        "invalid_vehicle_type_id_records": bad_vehicle_ids,
    }
    return report


def validate(kind: str, path: Path) -> Report:
    if kind == "historical":
        return validate_historical(path)
    if kind == "discovery":
        return validate_discovery(path)
    return validate_gbfs(path, kind)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Track C source contracts")
    parser.add_argument("kind", choices=["historical", "discovery", "station_information", "station_status", "vehicle_types"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    result = validate(args.kind, args.path).result()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
