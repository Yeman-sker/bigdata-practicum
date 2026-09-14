#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures" / "contracts"


def run(kind, filename):
    path = Path(filename)
    if not path.is_absolute():
        path = FIXTURES / path
    completed = subprocess.run(
        [sys.executable, "-m", "citibike.contract_validator", kind, str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


class SourceContractTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_timestamp.csv"
            temporary.write_text(source, encoding="utf-8")
            code, result = run("historical", temporary)
        self.assertEqual(code, 1)
        self.assertEqual(result["metrics"]["timestamp_null_counts"]["started_at"], 1)

    def test_historical_not_null_fields_fail(self):
        source = (FIXTURES / "historical_trip_sample.csv").read_text(encoding="utf-8")
        source = source.replace("sample-001,electric_bike", ",electric_bike", 1)
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_required.csv"
            temporary.write_text(source, encoding="utf-8")
            code, result = run("historical", temporary)
        self.assertEqual(code, 1)
        self.assertIn("ride_id", " ".join(result["errors"]))

    def test_historical_offset_timestamp_fails(self):
        source = (FIXTURES / "historical_trip_sample.csv").read_text(encoding="utf-8")
        source = source.replace("01/15/2025 08:10:00", "2025-01-15T08:10:00-05:00", 1)
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_offset.csv"
            temporary.write_text(source, encoding="utf-8")
            code, result = run("historical", temporary)
        self.assertEqual(code, 1)
        self.assertGreater(result["metrics"]["timestamp_parse_failures"]["started_at"], 0)

    def test_missing_gbfs_required_timestamp_fails(self):
        code, result = run("discovery", "invalid_missing_last_updated.json")
        self.assertEqual(code, 1)
        self.assertIn("last_updated", " ".join(result["errors"]))

    def test_missing_station_last_reported_fails(self):
        source = json.loads((FIXTURES / "station_status.json").read_text(encoding="utf-8"))
        del source["data"]["stations"][0]["last_reported"]
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_station_status.json"
            temporary.write_text(json.dumps(source), encoding="utf-8")
            code, result = run("station_status", temporary)
        self.assertEqual(code, 1)
        self.assertIn("last_reported", " ".join(result["errors"]))

    def test_null_gbfs_required_fields_fail(self):
        source = json.loads((FIXTURES / "station_status.json").read_text(encoding="utf-8"))
        source["data"]["stations"][0]["last_reported"] = None
        source["data"]["stations"][0]["num_bikes_available"] = None
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_nulls.json"
            temporary.write_text(json.dumps(source), encoding="utf-8")
            code, result = run("station_status", temporary)
        self.assertEqual(code, 1)
        errors = " ".join(result["errors"])
        self.assertIn("last_reported", errors)
        self.assertIn("num_bikes_available", errors)

    def test_string_posix_and_numeric_version_fail(self):
        discovery = json.loads((FIXTURES / "gbfs.json").read_text(encoding="utf-8"))
        discovery["last_updated"] = "1736930000"
        discovery["version"] = 2.3
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_types.json"
            temporary.write_text(json.dumps(discovery), encoding="utf-8")
            code, result = run("discovery", temporary)
        self.assertEqual(code, 1)
        self.assertIn("last_updated", " ".join(result["errors"]))
        self.assertIn("version", " ".join(result["errors"]))

    def test_missing_feed_url_fails(self):
        discovery = json.loads((FIXTURES / "gbfs.json").read_text(encoding="utf-8"))
        del discovery["data"]["en"]["feeds"][0]["url"]
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_url.json"
            temporary.write_text(json.dumps(discovery), encoding="utf-8")
            code, result = run("discovery", temporary)
        self.assertEqual(code, 1)
        self.assertIn("URLs", " ".join(result["errors"]))

    def test_url_without_host_fails(self):
        discovery = json.loads((FIXTURES / "gbfs.json").read_text(encoding="utf-8"))
        discovery["data"]["en"]["feeds"][0]["url"] = "https://"
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_url_host.json"
            temporary.write_text(json.dumps(discovery), encoding="utf-8")
            code, result = run("discovery", temporary)
        self.assertEqual(code, 1)
        self.assertIn("URLs", " ".join(result["errors"]))

    def test_malformed_discovery_entry_fails_structurally(self):
        discovery = json.loads((FIXTURES / "gbfs.json").read_text(encoding="utf-8"))
        discovery["data"]["en"]["feeds"].append([])
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_entry.json"
            temporary.write_text(json.dumps(discovery), encoding="utf-8")
            code, result = run("discovery", temporary)
        self.assertEqual(code, 1)
        self.assertIn("records", " ".join(result["errors"]))

    def test_invalid_inventory_type_fails_but_negative_is_observed(self):
        source = json.loads((FIXTURES / "station_status.json").read_text(encoding="utf-8"))
        source["data"]["stations"][0]["num_bikes_available"] = 2.5
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "invalid_inventory_type.json"
            temporary.write_text(json.dumps(source), encoding="utf-8")
            code, result = run("station_status", temporary)
        self.assertEqual(code, 1)
        self.assertEqual(result["metrics"]["invalid_availability_types"], 1)

        source["data"]["stations"][0]["num_bikes_available"] = -1
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "negative_inventory.json"
            temporary.write_text(json.dumps(source), encoding="utf-8")
            code, result = run("station_status", temporary)
        self.assertEqual(code, 0)
        self.assertEqual(result["metrics"]["negative_availability_values"], 1)

    def test_gbfs_coordinate_range_is_observed(self):
        source = json.loads((FIXTURES / "station_information.json").read_text(encoding="utf-8"))
        source["data"]["stations"][0]["lat"] = 91
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "out_of_range.json"
            temporary.write_text(json.dumps(source), encoding="utf-8")
            code, result = run("station_information", temporary)
        self.assertEqual(code, 0)
        self.assertEqual(result["metrics"]["coordinate_out_of_range_count"], 1)
        self.assertTrue(result["warnings"])

    def test_nonpositive_duration_is_counted(self):
        source = (FIXTURES / "historical_trip_sample.csv").read_text(encoding="utf-8")
        source = source.replace("01/15/2025 08:24:00", "01/15/2025 08:10:00", 1)
        with tempfile.TemporaryDirectory(prefix="source-contract-test-") as directory:
            temporary = Path(directory) / "nonpositive_duration.csv"
            temporary.write_text(source, encoding="utf-8")
            code, result = run("historical", temporary)
        self.assertEqual(code, 0)
        self.assertEqual(result["metrics"]["nonpositive_duration_count"], 1)

    def test_non_object_json_fails_structurally(self):
        code, result = run("station_status", "invalid_top_level.json")
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("top-level JSON", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
