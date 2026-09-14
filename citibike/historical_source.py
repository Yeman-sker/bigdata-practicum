#!/usr/bin/env python3
"""Verify one Citi Bike Historical Trips ZIP and write a source manifest.

The verifier intentionally uses only the Python standard library so that the
same command can be rerun on a clean WSL installation and on a Hadoop edge
node.  It never copies the source ZIP into the repository.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import (
    COORDINATE_FIELDS,
    HISTORICAL_TIME_FORMATS,
    LATITUDE_FIELDS,
    LONGITUDE_FIELDS,
    SOURCE_ENUMS,
    SOURCE_FIELDS,
    STATION_ID_FIELDS,
    TIME_FIELDS,
)

DECIMAL_ID = re.compile(r"^[+-]?\d+\.\d+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="zip_path", required=True, type=Path)
    parser.add_argument("--extract-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-page-url", default="https://citibikenyc.com/system-data")
    parser.add_argument("--source-month", required=True, help="YYYY-MM")
    parser.add_argument("--source-name", default="Citi Bike Historical Trips")
    parser.add_argument("--downloaded-at", help="UTC ISO-8601 timestamp")
    parser.add_argument("--sample-output", type=Path)
    parser.add_argument("--sample-rows", type=int, default=20)
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_path(name: str) -> Path:
    """Reject ZIP members that could escape the staging directory."""
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"unsafe ZIP member path: {name!r}")
    return Path(*pure.parts)


def parse_timestamp(value: str) -> tuple[dt.datetime | None, str | None]:
    """Parse a source timestamp and retain the format that matched it."""
    for fmt in HISTORICAL_TIME_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt), fmt
        except ValueError:
            continue
    return None, None


def new_quality_state() -> dict[str, Any]:
    """Create cross-file quality counters without putting implementation state in JSON."""
    return {
        "seen_ride_ids": set(),
        "duplicate_ride_ids": set(),
        "duplicate_ride_id_count": 0,
        "duration_non_positive_count": 0,
        "duration_uncomputable_count": 0,
        "coordinate_quality": {
            field: {"invalid_count": 0, "out_of_range_count": 0} for field in COORDINATE_FIELDS
        },
        "coordinate_anomaly_row_count": 0,
        "unknown_enum_counts": {field: 0 for field in SOURCE_ENUMS},
    }


def read_csv_stats(
    path: Path,
    sample_writer: csv.writer | None,
    sample_state: dict[str, int],
    quality_state: dict[str, Any],
) -> dict[str, Any]:
    missing = {field: 0 for field in SOURCE_FIELDS}
    enum_values = {"rideable_type": set(), "member_casual": set()}
    time_formats = {field: set() for field in TIME_FIELDS}
    invalid_times = {field: 0 for field in TIME_FIELDS}
    station_id_types = {field: {"string_examples": [], "decimal_looking_count": 0} for field in STATION_ID_FIELDS}
    record_count = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        if header != list(SOURCE_FIELDS):
            raise ValueError(f"{path.name}: unexpected header: {header!r}")
        for row in reader:
            record_count += 1
            if sample_writer is not None and sample_state["rows"] < sample_state["limit"]:
                sample_writer.writerow([row.get(field, "") or "" for field in SOURCE_FIELDS])
                sample_state["rows"] += 1
            for field in SOURCE_FIELDS:
                value = (row.get(field) or "").strip()
                if not value:
                    missing[field] += 1

            ride_id = (row.get("ride_id") or "").strip()
            if ride_id:
                if ride_id in quality_state["seen_ride_ids"]:
                    quality_state["duplicate_ride_id_count"] += 1
                    quality_state["duplicate_ride_ids"].add(ride_id)
                else:
                    quality_state["seen_ride_ids"].add(ride_id)

            for field in enum_values:
                value = (row.get(field) or "").strip()
                if value:
                    enum_values[field].add(value)
                    if value not in SOURCE_ENUMS[field]:
                        quality_state["unknown_enum_counts"][field] += 1

            parsed_times: dict[str, dt.datetime | None] = {}
            for field in TIME_FIELDS:
                value = (row.get(field) or "").strip()
                parsed, fmt = parse_timestamp(value) if value else (None, None)
                parsed_times[field] = parsed
                if fmt is not None:
                    time_formats[field].add(fmt)
                elif value:
                    invalid_times[field] += 1

            if parsed_times["started_at"] is not None and parsed_times["ended_at"] is not None:
                duration_seconds = (
                    parsed_times["ended_at"] - parsed_times["started_at"]
                ).total_seconds()
                if duration_seconds <= 0:
                    quality_state["duration_non_positive_count"] += 1
            else:
                quality_state["duration_uncomputable_count"] += 1

            row_coordinate_anomaly = False
            for field in COORDINATE_FIELDS:
                value = (row.get(field) or "").strip()
                if not value:
                    continue
                try:
                    coordinate = float(value)
                except ValueError:
                    quality_state["coordinate_quality"][field]["invalid_count"] += 1
                    row_coordinate_anomaly = True
                    continue
                if not math.isfinite(coordinate):
                    quality_state["coordinate_quality"][field]["invalid_count"] += 1
                    row_coordinate_anomaly = True
                    continue
                if field in LATITUDE_FIELDS and not -90 <= coordinate <= 90:
                    quality_state["coordinate_quality"][field]["out_of_range_count"] += 1
                    row_coordinate_anomaly = True
                elif field in LONGITUDE_FIELDS and not -180 <= coordinate <= 180:
                    quality_state["coordinate_quality"][field]["out_of_range_count"] += 1
                    row_coordinate_anomaly = True
            if row_coordinate_anomaly:
                quality_state["coordinate_anomaly_row_count"] += 1

            for field in STATION_ID_FIELDS:
                value = (row.get(field) or "").strip()
                if not value:
                    continue
                examples = station_id_types[field]["string_examples"]
                if value not in examples and len(examples) < 5:
                    examples.append(value)
                if DECIMAL_ID.fullmatch(value):
                    station_id_types[field]["decimal_looking_count"] += 1

    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "header": SOURCE_FIELDS,
        "record_count": record_count,
        "missing_values": missing,
        "enum_values": {key: sorted(values) for key, values in enum_values.items()},
        "time_formats": {key: sorted(values) for key, values in time_formats.items()},
        "invalid_time_count": invalid_times,
        "station_id_observations": station_id_types,
    }


def ratio(count: int, total: int) -> float:
    """Return a stable, readable proportion for manifest quality metrics."""
    return round(count / total, 6) if total else 0.0


def main() -> int:
    args = parse_args()
    if not args.zip_path.is_file():
        raise FileNotFoundError(args.zip_path)
    args.extract_dir.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.zip_path) as archive:
        members = archive.infolist()
        member_names = [member.filename for member in members]
        for name in member_names:
            safe_member_path(name)
        archive.extractall(args.extract_dir)

    csv_members = [name for name in member_names if name.lower().endswith(".csv")]
    csv_paths = [args.extract_dir / safe_member_path(name) for name in csv_members]
    if not csv_paths:
        raise ValueError("ZIP contains no CSV files")

    sample_handle = None
    sample_writer = None
    sample_state = {"rows": 0, "limit": max(0, args.sample_rows)}
    if args.sample_output:
        args.sample_output.parent.mkdir(parents=True, exist_ok=True)
        sample_handle = args.sample_output.open("w", encoding="utf-8", newline="")
        sample_writer = csv.writer(sample_handle, lineterminator="\n")
        sample_writer.writerow(SOURCE_FIELDS)

    quality_state = new_quality_state()
    try:
        csv_stats = [
            read_csv_stats(path, sample_writer, sample_state, quality_state) for path in csv_paths
        ]
    finally:
        if sample_handle is not None:
            sample_handle.close()

    total_records = sum(item["record_count"] for item in csv_stats)
    all_missing = {
        field: sum(item["missing_values"][field] for item in csv_stats) for field in SOURCE_FIELDS
    }
    all_enums = {
        field: sorted({value for item in csv_stats for value in item["enum_values"][field]})
        for field in ("rideable_type", "member_casual")
    }
    all_time_formats = {
        field: sorted({value for item in csv_stats for value in item["time_formats"][field]})
        for field in TIME_FIELDS
    }
    all_station_ids = {
        field: {
            "read_as": "STRING",
            "missing_count": all_missing[field],
            "missing_ratio": ratio(all_missing[field], total_records),
            "decimal_looking_count": sum(
                item["station_id_observations"][field]["decimal_looking_count"] for item in csv_stats
            ),
            "examples": sorted(
                {
                    value
                    for item in csv_stats
                    for value in item["station_id_observations"][field]["string_examples"]
                }
            )[:10],
        }
        for field in STATION_ID_FIELDS
    }

    manifest = {
        "source_name": args.source_name,
        "source_page_url": args.source_page_url,
        "source_url": args.source_url,
        "source_month": args.source_month,
        "downloaded_at": args.downloaded_at or utc_now(),
        "staging_path": str(args.zip_path.parent.parent),
        "zip_filename": args.zip_path.name,
        "zip_size_bytes": args.zip_path.stat().st_size,
        "zip_sha256": sha256_file(args.zip_path),
        "zip_members": member_names,
        "csv_files": csv_stats,
        "csv_file_count": len(csv_stats),
        "record_count": total_records,
        "count_method": (
            "Python csv.DictReader streaming count; each physical CSV row is read and counted; "
            "run python3 -m citibike.historical_source with the command in "
            "docs/source/citibike-202501.md."
        ),
        "schema_version_or_observed_format": "2025 Citi Bike Historical Trips 13-column CSV",
        "observed": {
            "header": SOURCE_FIELDS,
            "missing_values": all_missing,
            "rideable_type_values": all_enums["rideable_type"],
            "member_casual_values": all_enums["member_casual"],
            "time_formats": all_time_formats,
            "invalid_time_count": {
                field: sum(item["invalid_time_count"][field] for item in csv_stats) for field in TIME_FIELDS
            },
            "station_id_observations": all_station_ids,
            "quality_metrics": {
                "ride_id": {
                    "missing_count": all_missing["ride_id"],
                    "missing_ratio": ratio(all_missing["ride_id"], total_records),
                    "duplicate_row_count": quality_state["duplicate_ride_id_count"],
                    "duplicate_value_count": len(quality_state["duplicate_ride_ids"]),
                },
                "station_id_nulls": {
                    field: {
                        "missing_count": all_missing[field],
                        "missing_ratio": ratio(all_missing[field], total_records),
                    }
                    for field in STATION_ID_FIELDS
                },
                "duration": {
                    "non_positive_count": quality_state["duration_non_positive_count"],
                    "uncomputable_count": quality_state["duration_uncomputable_count"],
                },
                "coordinates": {
                    field: {
                        "missing_count": all_missing[field],
                        "missing_ratio": ratio(all_missing[field], total_records),
                        **quality_state["coordinate_quality"][field],
                    }
                    for field in COORDINATE_FIELDS
                }
                | {"anomaly_row_count": quality_state["coordinate_anomaly_row_count"]},
                "unknown_enum_counts": quality_state["unknown_enum_counts"],
            },
        },
        "contract_mapping": {
            "ride_id": "ride_id",
            "rideable_type": "rideable_type",
            "started_at": "started_at_local",
            "ended_at": "ended_at_local",
            "start_station_name": "start_station_name",
            "start_station_id": "start_station_id",
            "end_station_name": "end_station_name",
            "end_station_id": "end_station_id",
            "start_lat": "start_lat",
            "start_lng": "start_lng",
            "end_lat": "end_lat",
            "end_lng": "end_lng",
            "member_casual": "member_casual",
        },
        "contract_mapping_notes": {
            "started_at": "Naive source wall-clock time; parse as America/New_York and store as started_at_local.",
            "ended_at": "Naive source wall-clock time; parse as America/New_York and store as ended_at_local.",
            "station_ids": "Read and stored as STRING identifiers; never coerce to numeric types.",
        },
        "downstream_derivations": {
            "duration_seconds": "ended_at_local - started_at_local",
            "service_date": "date(started_at_local) in America/New_York",
            "start_hour": "hour(started_at_local) in America/New_York",
            "day_of_week": "weekday(started_at_local) in America/New_York",
            "is_weekend": "day_of_week in {6, 7}",
            "is_valid_station_trip": "both station IDs are non-empty and both timestamps parse",
            "source_year": "year(source_month)",
            "source_month": "month(source_month)",
            "ingest_batch_id": "assigned by the downstream ingestion job",
        },
        "notes": [
            "ZIP contains multiple CSV files; all members were enumerated before extraction.",
            "Station identifiers are read as strings; decimal-looking values were counted explicitly.",
            "Original ZIP and extracted full CSVs are staging artifacts outside the Git repository.",
            "The tracked fixture is synthetic and is not copied from the official source rows.",
            "Quality metrics count anomalies instead of silently dropping records.",
        ],
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(args.manifest), "record_count": total_records, "csv_files": len(csv_stats)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
