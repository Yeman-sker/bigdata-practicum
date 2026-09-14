import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "citibike"))

from land_historical_trips import (  # noqa: E402
    LandingError,
    SOURCE_FIELDS,
    land_historical_trips,
)


class FakeHdfs:
    def __init__(self, sizes: dict[str, int], mismatch_ods_checksum: bool = False):
        self.sizes = sizes
        self.mismatch_ods_checksum = mismatch_ods_checksum
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if "-stat" in command:
            path = command[-1]
            return subprocess.CompletedProcess(command, 0, f"{self.sizes[path]}\n", "")
        if "-checksum" in command:
            path = command[-1]
            checksum = f"checksum-{Path(path).name}"
            if self.mismatch_ods_checksum and "/warehouse/" in path:
                checksum += "-mismatch"
            return subprocess.CompletedProcess(
                command,
                0,
                f"MD5-of-0MD5-of-512CRC32C {checksum} {path}\n",
                "",
            )
        if "-count" in command:
            prefix = "/warehouse" if "/warehouse" in command[-1] else "/raw"
            matching_sizes = [size for path, size in self.sizes.items() if prefix in path]
            return subprocess.CompletedProcess(
                command,
                0,
                f"1 {len(matching_sizes)} {sum(matching_sizes)} {command[-1]}\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def source_row(ride_id: str) -> dict[str, str]:
    return {
        "ride_id": ride_id,
        "rideable_type": "classic_bike",
        "started_at": "2025-01-01 10:00:00.000",
        "ended_at": "2025-01-01 10:10:00.000",
        "start_station_name": "Synthetic Start",
        "start_station_id": "5484.09",
        "end_station_name": "Synthetic End",
        "end_station_id": "4199.12",
        "start_lat": "40.0",
        "start_lng": "-74.0",
        "end_lat": "40.1",
        "end_lng": "-74.1",
        "member_casual": "member",
    }


class HistoricalTripsLandingTest(unittest.TestCase):
    def test_uploads_every_csv_and_copies_bytes_to_ods_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            files = []
            for name, rows in (
                ("part-1.csv", [source_row("ride-1")]),
                ("part-2.csv", [source_row("ride-2")]),
            ):
                path = staging / name
                write_csv(path, rows)
                files.append(path)

            manifest = staging / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "source_month": "2025-01",
                        "record_count": 2,
                        "csv_files": [
                            {"filename": path.name, "size_bytes": path.stat().st_size}
                            for path in files
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raw = "/raw/citibike/trips/year=2025/month=01"
            ods = "/warehouse/ods/ods_trip_raw/year=2025/month=01"
            sizes = {
                f"{root}/{path.name}": path.stat().st_size
                for root in (raw, ods)
                for path in files
            }
            fake_hdfs = FakeHdfs(sizes)

            summary = land_historical_trips(
                staging,
                "2025-01",
                manifest_path=manifest,
                runner=fake_hdfs,
            )

            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["source_record_count"], 2)
            self.assertEqual(summary["file_count"], 2)
            self.assertEqual(summary["raw_path"], raw)
            self.assertEqual(summary["ods_path"], ods)
            self.assertEqual(
                sum("-put" in command for command in fake_hdfs.calls),
                2,
            )
            self.assertEqual(
                sum("-cp" in command for command in fake_hdfs.calls),
                2,
            )
            self.assertEqual(
                sum("-checksum" in command for command in fake_hdfs.calls),
                4,
            )
            for item, path in zip(summary["files"], files):
                self.assertEqual(item["raw_size_bytes"], path.stat().st_size)
                self.assertEqual(item["ods_size_bytes"], path.stat().st_size)
                self.assertEqual(item["raw_checksum"], item["ods_checksum"])

    def test_checksum_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            path = staging / "part.csv"
            write_csv(path, [source_row("ride-1")])
            raw = "/raw/citibike/trips/year=2025/month=01"
            ods = "/warehouse/ods/ods_trip_raw/year=2025/month=01"
            fake_hdfs = FakeHdfs(
                {
                    f"{raw}/{path.name}": path.stat().st_size,
                    f"{ods}/{path.name}": path.stat().st_size,
                },
                mismatch_ods_checksum=True,
            )

            with self.assertRaisesRegex(LandingError, "checksum mismatch"):
                land_historical_trips(staging, "2025-01", runner=fake_hdfs)

    def test_bad_header_fails_before_any_hdfs_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            (staging / "bad.csv").write_text("wrong,header\nvalue\n", encoding="utf-8")
            fake_hdfs = FakeHdfs({})

            with self.assertRaisesRegex(LandingError, "13-column"):
                land_historical_trips(staging, "2025-01", runner=fake_hdfs)

            self.assertEqual(fake_hdfs.calls, [])

    def test_manifest_mismatch_fails_before_any_hdfs_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            path = staging / "part.csv"
            write_csv(path, [source_row("ride-1")])
            manifest = staging / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "source_month": "2025-01",
                        "record_count": 1,
                        "csv_files": [{"filename": path.name, "size_bytes": 999}],
                    }
                ),
                encoding="utf-8",
            )
            fake_hdfs = FakeHdfs({})

            with self.assertRaisesRegex(LandingError, "local size"):
                land_historical_trips(
                    staging,
                    "2025-01",
                    manifest_path=manifest,
                    runner=fake_hdfs,
                )

            self.assertEqual(fake_hdfs.calls, [])

    def test_raw_only_does_not_copy_to_ods(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            path = staging / "part.csv"
            write_csv(path, [source_row("ride-1")])
            raw = "/raw/citibike/trips/year=2025/month=01"
            fake_hdfs = FakeHdfs({f"{raw}/{path.name}": path.stat().st_size})

            summary = land_historical_trips(
                staging,
                "2025-01",
                raw_only=True,
                runner=fake_hdfs,
            )

            self.assertIsNone(summary["ods_path"])
            self.assertFalse(any("-cp" in command for command in fake_hdfs.calls))


if __name__ == "__main__":
    unittest.main()
