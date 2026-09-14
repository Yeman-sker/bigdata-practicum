import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from citibike.contracts import SOURCE_FIELDS
from citibike.historical_source import download_archive

ROOT = Path(__file__).parents[1]
SOURCE_MODULE = "citibike.historical_source"


def source_row(ride_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "ride_id": ride_id,
        "rideable_type": "classic_bike",
        "started_at": "2025-01-01 10:00:00.000",
        "ended_at": "2025-01-01 10:10:00.000",
        "start_station_name": "Synthetic Start",
        "start_station_id": "SYN-START-01",
        "end_station_name": "Synthetic End",
        "end_station_id": "SYN-END-01",
        "start_lat": "40.0",
        "start_lng": "-74.0",
        "end_lat": "40.1",
        "end_lng": "-74.1",
        "member_casual": "member",
    }
    row.update(overrides)
    return row


class VerifySourceTest(unittest.TestCase):
    def test_download_archive_writes_atomically_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with patch(
                "citibike.historical_source.urlopen",
                return_value=io.BytesIO(b"synthetic-zip"),
            ):
                archive_path = download_archive(
                    "https://example.invalid/202501.zip",
                    output_dir,
                    "2025-01",
                )

            self.assertEqual(
                archive_path,
                output_dir / "202501-citibike-tripdata.zip",
            )
            self.assertEqual(archive_path.read_bytes(), b"synthetic-zip")
            self.assertFalse((output_dir / ".202501-citibike-tripdata.zip.part").exists())

            with self.assertRaises(FileExistsError):
                download_archive(
                    "https://example.invalid/202501.zip",
                    output_dir,
                    "2025-01",
                )

    def test_quality_metrics_and_mapping_are_written_across_csv_members(self):
        rows = [
            source_row("test-1"),
            source_row(
                "test-1",
                rideable_type="cargo_bike",
                started_at="not-a-time",
                start_station_id="",
                start_lat="91.0",
                start_lng="not-a-coordinate",
                member_casual="subscriber",
            ),
            source_row("test-3", ended_at="2025-01-01 09:59:59.000"),
        ]

        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            archive_path = staging / "source.zip"
            extract_dir = staging / "extracted"
            manifest_path = staging / "manifest.json"
            sample_path = staging / "sample.csv"

            csv_texts = []
            for member_rows in (rows[:2], rows[2:]):
                buffer = StringIO()
                writer = csv.DictWriter(buffer, fieldnames=SOURCE_FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(member_rows)
                csv_texts.append(buffer.getvalue())

            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("part-1.csv", csv_texts[0])
                archive.writestr("part-2.csv", csv_texts[1])
                archive.writestr("README.txt", "synthetic verifier test\n")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    SOURCE_MODULE,
                    "--zip",
                    str(archive_path),
                    "--extract-dir",
                    str(extract_dir),
                    "--manifest",
                    str(manifest_path),
                    "--sample-output",
                    str(sample_path),
                    "--sample-rows",
                    "2",
                    "--source-url",
                    "https://example.invalid/source.zip",
                    "--source-month",
                    "2025-01",
                    "--downloaded-at",
                    "2026-09-11T00:00:00Z",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            quality = manifest["observed"]["quality_metrics"]
            self.assertEqual(manifest["record_count"], 3)
            self.assertEqual(manifest["csv_file_count"], 2)
            self.assertEqual(quality["ride_id"]["duplicate_row_count"], 1)
            self.assertEqual(quality["ride_id"]["duplicate_value_count"], 1)
            self.assertEqual(quality["station_id_nulls"]["start_station_id"]["missing_count"], 1)
            self.assertEqual(manifest["observed"]["invalid_time_count"]["started_at"], 1)
            self.assertEqual(quality["duration"]["non_positive_count"], 1)
            self.assertEqual(quality["duration"]["uncomputable_count"], 1)
            self.assertEqual(quality["coordinates"]["start_lat"]["out_of_range_count"], 1)
            self.assertEqual(quality["coordinates"]["start_lng"]["invalid_count"], 1)
            self.assertEqual(quality["coordinates"]["anomaly_row_count"], 1)
            self.assertEqual(quality["unknown_enum_counts"]["rideable_type"], 1)
            self.assertEqual(quality["unknown_enum_counts"]["member_casual"], 1)
            self.assertEqual(manifest["contract_mapping"]["started_at"], "started_at_local")
            self.assertEqual(manifest["contract_mapping"]["ended_at"], "ended_at_local")
            self.assertEqual(len(sample_path.read_text(encoding="utf-8").splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
