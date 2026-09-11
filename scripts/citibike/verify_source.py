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
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


SOURCE_FIELDS = [
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
]
TIME_FIELDS = ("started_at", "ended_at")
STATION_ID_FIELDS = ("start_station_id", "end_station_id")
DECIMAL_ID = re.compile(r"^[+-]?\d+\.\d+$")
TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)


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
    parser.add_argument("--sample-rows", type=int, default=100)
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


def read_csv_stats(path: Path, sample_writer: csv.writer | None, sample_state: dict[str, int]) -> dict[str, Any]:
    missing = {field: 0 for field in SOURCE_FIELDS}
    enum_values = {"rideable_type": set(), "member_casual": set()}
    time_formats = {field: set() for field in TIME_FIELDS}
    invalid_times = {field: 0 for field in TIME_FIELDS}
    station_id_types = {field: {"string_examples": [], "decimal_looking_count": 0} for field in STATION_ID_FIELDS}
    record_count = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        if header != SOURCE_FIELDS:
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
            for field in enum_values:
                value = (row.get(field) or "").strip()
                if value:
                    enum_values[field].add(value)
            for field in TIME_FIELDS:
                value = (row.get(field) or "").strip()
                if not value:
                    continue
                parsed = False
                for fmt in TIME_FORMATS:
                    try:
                        dt.datetime.strptime(value, fmt)
                        time_formats[field].add(fmt)
                        parsed = True
                        break
                    except ValueError:
                        continue
                if not parsed:
                    invalid_times[field] += 1
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

    try:
        csv_stats = [read_csv_stats(path, sample_writer, sample_state) for path in csv_paths]
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
            "run scripts/citibike/verify_source.py with the command in the accompanying README."
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
        },
        "contract_mapping": {field: field for field in SOURCE_FIELDS},
        "notes": [
            "ZIP contains multiple CSV files; all members were enumerated before extraction.",
            "Station identifiers are read as strings; decimal-looking values were counted explicitly.",
            "Original ZIP and extracted full CSVs are staging artifacts outside the Git repository.",
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
