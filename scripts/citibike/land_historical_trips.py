#!/usr/bin/env python3
"""Land verified Citi Bike Historical Trips CSV files in HDFS.

The command keeps the provider bytes intact.  It uploads every CSV found in a
local extracted staging directory to the contract RAW partition and then
copies those files to the external ODS location.  The HDFS copy is deliberate:
it keeps the RAW path immutable when the Hive table is dropped or rebuilt.

No source ZIP or CSV is copied into the repository by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable


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
DEFAULT_RAW_ROOT = "/raw/citibike/trips"
DEFAULT_ODS_ROOT = "/warehouse/ods/ods_trip_raw"
SOURCE_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")


class LandingError(RuntimeError):
    """Raised when a source or HDFS landing precondition is not met."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def parse_source_month(value: str) -> tuple[int, int]:
    """Return the numeric year and month from a strict ``YYYY-MM`` value."""

    match = SOURCE_MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise LandingError(f"source month must use YYYY-MM, got {value!r}")
    return int(match.group("year")), int(match.group("month"))


def normalise_hdfs_root(value: str) -> str:
    """Validate and normalise an absolute HDFS path root."""

    path = PurePosixPath(value)
    if not value.startswith("/") or value.startswith("//"):
        raise LandingError(f"HDFS root must be an absolute path, got {value!r}")
    if path == PurePosixPath("/") or ".." in path.parts:
        raise LandingError(f"HDFS root must not be / or contain '..', got {value!r}")
    return "/" + "/".join(part for part in path.parts if part != "/")


def partition_path(root: str, source_month: str) -> str:
    """Build the contract path for one source month."""

    year, month = parse_source_month(source_month)
    return f"{normalise_hdfs_root(root)}/year={year:04d}/month={month:02d}"


def discover_csv_files(staging_dir: Path) -> list[Path]:
    """Find all extracted CSV members and reject ambiguous target names."""

    if not staging_dir.is_dir():
        raise LandingError(f"staging directory does not exist: {staging_dir}")
    files = sorted(
        path
        for path in staging_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".csv"
    )
    if not files:
        raise LandingError(f"staging directory contains no CSV files: {staging_dir}")

    names = [path.name for path in files]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise LandingError(
            "CSV basenames must be unique because one month shares one Hive "
            f"partition: {duplicates}"
        )
    return files


def validate_source_header(path: Path) -> None:
    """Check the source header without changing or scanning source rows."""

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), None)
    except OSError as error:
        raise LandingError(f"cannot read CSV header from {path}: {error}") from error
    if header != list(SOURCE_FIELDS):
        raise LandingError(
            f"{path.name}: expected the 13-column Historical Trips header, got {header!r}"
        )


def load_manifest(manifest_path: Path, source_month: str, files: list[Path]) -> int | None:
    """Cross-check local members against the Track A source manifest."""

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LandingError(f"cannot read source manifest {manifest_path}: {error}") from error
    if not isinstance(payload, dict):
        raise LandingError("source manifest top-level value must be an object")
    if payload.get("source_month") != source_month:
        raise LandingError(
            "manifest source_month does not match the requested month: "
            f"{payload.get('source_month')!r} != {source_month!r}"
        )

    entries = payload.get("csv_files")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise LandingError("source manifest csv_files must be a list of objects")
    manifest_files: dict[str, dict[str, Any]] = {}
    for entry in entries:
        filename = entry.get("filename")
        size_bytes = entry.get("size_bytes")
        if not isinstance(filename, str) or not filename:
            raise LandingError("source manifest contains a CSV entry without filename")
        if filename in manifest_files:
            raise LandingError(f"source manifest contains duplicate filename: {filename}")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise LandingError(f"source manifest has invalid size for {filename!r}")
        manifest_files[filename] = entry

    local_files = {path.name: path for path in files}
    if set(manifest_files) != set(local_files):
        missing = sorted(set(manifest_files) - set(local_files))
        extra = sorted(set(local_files) - set(manifest_files))
        raise LandingError(f"manifest/local CSV mismatch: missing={missing}, extra={extra}")
    for filename, path in local_files.items():
        expected_size = manifest_files[filename]["size_bytes"]
        if path.stat().st_size != expected_size:
            raise LandingError(
                f"{filename}: local size {path.stat().st_size} != manifest size {expected_size}"
            )

    record_count = payload.get("record_count")
    if record_count is None:
        return None
    if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0:
        raise LandingError("source manifest record_count must be a non-negative integer")
    return record_count


def run_hdfs(
    hdfs_bin: str,
    arguments: list[str],
    runner: Runner,
) -> str:
    """Run one HDFS command and surface actionable stderr on failure."""

    command = [hdfs_bin, "dfs", *arguments]
    result = runner(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "command failed").strip()
        raise LandingError(f"{shlex.join(command)}: {details}")
    return (result.stdout or "").strip()


def remote_size(hdfs_bin: str, path: str, runner: Runner) -> int:
    """Read and return a remote file size in bytes."""

    output = run_hdfs(hdfs_bin, ["-stat", "%b", path], runner)
    try:
        return int(output)
    except ValueError as error:
        raise LandingError(f"HDFS stat returned a non-integer size for {path}: {output!r}") from error


def validate_hdfs_count(output: str, expected_files: int, expected_bytes: int, label: str) -> None:
    """Reject stale or missing files reported by ``hdfs dfs -count``."""

    fields = output.split()
    if len(fields) < 3:
        raise LandingError(f"HDFS {label} count output is malformed: {output!r}")
    try:
        actual_files = int(fields[1])
        actual_bytes = int(fields[2])
    except ValueError as error:
        raise LandingError(f"HDFS {label} count output is malformed: {output!r}") from error
    if actual_files != expected_files or actual_bytes != expected_bytes:
        raise LandingError(
            f"HDFS {label} count mismatch: files={actual_files}, bytes={actual_bytes}; "
            f"expected files={expected_files}, bytes={expected_bytes}"
        )


def build_summary(
    source_month: str,
    raw_partition: str,
    ods_partition: str,
    files: list[Path],
    record_count: int | None,
    raw_only: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Create stable machine-readable output for handoff and tests."""

    return {
        "status": "DRY_RUN" if dry_run else "PASS",
        "source_month": source_month,
        "source_record_count": record_count,
        "raw_path": raw_partition,
        "ods_path": None if raw_only else ods_partition,
        "file_count": len(files),
        "files": [
            {
                "filename": path.name,
                "local_size_bytes": path.stat().st_size,
                "raw_size_bytes": None,
                "ods_size_bytes": None,
            }
            for path in files
        ],
        "raw_hdfs_count": None,
        "ods_hdfs_count": None,
    }


def land_historical_trips(
    staging_dir: Path,
    source_month: str,
    manifest_path: Path | None = None,
    hdfs_bin: str = "hdfs",
    raw_root: str = DEFAULT_RAW_ROOT,
    ods_root: str = DEFAULT_ODS_ROOT,
    raw_only: bool = False,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Validate, upload, and verify one month's Historical Trips files."""

    parse_source_month(source_month)
    raw_partition = partition_path(raw_root, source_month)
    ods_partition = partition_path(ods_root, source_month)
    files = discover_csv_files(staging_dir)
    for path in files:
        validate_source_header(path)
    record_count = load_manifest(manifest_path, source_month, files) if manifest_path else None

    summary = build_summary(
        source_month,
        raw_partition,
        ods_partition,
        files,
        record_count,
        raw_only,
        dry_run,
    )
    if dry_run:
        return summary

    expected_bytes = sum(path.stat().st_size for path in files)
    run_hdfs(hdfs_bin, ["-mkdir", "-p", raw_partition], runner)
    for index, path in enumerate(files):
        run_hdfs(hdfs_bin, ["-put", "-f", str(path), f"{raw_partition}/"], runner)
        raw_path = f"{raw_partition}/{path.name}"
        raw_size = remote_size(hdfs_bin, raw_path, runner)
        if raw_size != path.stat().st_size:
            raise LandingError(
                f"{path.name}: HDFS RAW size {raw_size} != local size {path.stat().st_size}"
            )
        summary["files"][index]["raw_size_bytes"] = raw_size

    summary["raw_hdfs_count"] = run_hdfs(hdfs_bin, ["-count", raw_partition], runner)
    validate_hdfs_count(summary["raw_hdfs_count"], len(files), expected_bytes, "RAW")

    if not raw_only:
        run_hdfs(hdfs_bin, ["-mkdir", "-p", ods_partition], runner)
        for index, path in enumerate(files):
            raw_path = f"{raw_partition}/{path.name}"
            run_hdfs(hdfs_bin, ["-cp", "-f", raw_path, f"{ods_partition}/"], runner)
            ods_path = f"{ods_partition}/{path.name}"
            ods_size = remote_size(hdfs_bin, ods_path, runner)
            if ods_size != path.stat().st_size:
                raise LandingError(
                    f"{path.name}: HDFS ODS size {ods_size} != local size {path.stat().st_size}"
                )
            summary["files"][index]["ods_size_bytes"] = ods_size
        summary["ods_hdfs_count"] = run_hdfs(hdfs_bin, ["-count", ods_partition], runner)
        validate_hdfs_count(summary["ods_hdfs_count"], len(files), expected_bytes, "ODS")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", required=True, type=Path)
    parser.add_argument("--source-month", required=True, help="source month in YYYY-MM format")
    parser.add_argument("--manifest", type=Path, help="Track A source manifest to cross-check")
    parser.add_argument("--hdfs-bin", default="hdfs")
    parser.add_argument("--raw-root", default=DEFAULT_RAW_ROOT)
    parser.add_argument("--ods-root", default=DEFAULT_ODS_ROOT)
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="upload and verify RAW only; skip the byte-for-byte ODS copy",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = land_historical_trips(
            staging_dir=args.staging_dir,
            source_month=args.source_month,
            manifest_path=args.manifest,
            hdfs_bin=args.hdfs_bin,
            raw_root=args.raw_root,
            ods_root=args.ods_root,
            raw_only=args.raw_only,
            dry_run=args.dry_run,
        )
    except (LandingError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
