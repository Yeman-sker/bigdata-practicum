#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPT = ROOT / "validate_sources.py"
FIXTURES = ROOT / "fixtures"


def run(kind, filename):
    path = Path(filename)
    if not path.is_absolute():
        path = FIXTURES / path
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), kind, str(path)],
        text=True, capture_output=True, check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


class TrackCContractTests(unittest.TestCase):
    def test_valid_fixtures_pass(self):
        for kind, filename in (
            ("historical", "historical_trip_sample.csv"),
            ("discovery", "gbfs.json"),
            ("station_information", "station_information.json"),
            ("station_status", "station_status.json"),
            ("vehicle_types", "vehicle_types.json"),
        ):
            with self.subTest(kind=kind):
                code, result = run(kind, filename)
                self.assertEqual(code, 0, result)
                self.assertEqual(result["status"], "PASS")

    def test_missing_required_field_fails(self):
        code, result = run("station_information", "invalid_missing_station_id.json")
        self.assertEqual(code, 1)
        self.assertIn("station_id", result["errors"][0])

    def test_numeric_station_id_fails(self):
        code, result = run("station_information", "invalid_station_id_type.json")
        self.assertEqual(code, 1)
        self.assertEqual(result["metrics"]["invalid_station_id_records"], 1)

    def test_empty_historical_timestamp_fails(self):
        source = (FIXTURES / "historical_trip_sample.csv").read_text(encoding="utf-8")
        source = source.replace("01/15/2025 08:10:00", ",", 1)
        with tempfile.TemporaryDirectory(prefix="track-c-test-") as directory:
            temporary = Path(directory) / "invalid_timestamp.csv"
            temporary.write_text(source, encoding="utf-8")
            code, result = run("historical", temporary)
        self.assertEqual(code, 1)
        self.assertEqual(result["metrics"]["timestamp_null_counts"]["started_at"], 1)


if __name__ == "__main__":
    unittest.main()
