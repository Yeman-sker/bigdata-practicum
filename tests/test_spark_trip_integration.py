import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "spark"))

from read_trip_integration import (  # noqa: E402
    SOURCE_FIELDS,
    TIME_PATTERNS,
    load_expected_count,
    parse_source_month,
)

try:
    from pyspark.sql import SparkSession
except ImportError:
    SparkSession = None


class SparkTripIntegrationContractTests(unittest.TestCase):
    def test_source_contract_and_month_are_explicit(self):
        self.assertEqual(len(SOURCE_FIELDS), 13)
        self.assertEqual(SOURCE_FIELDS[5:8], ("start_station_id", "end_station_name", "end_station_id"))
        self.assertEqual(parse_source_month("2025-01"), (2025, 1))
        self.assertIn("yyyy-MM-dd HH:mm:ss.SSS", TIME_PATTERNS)
        with self.assertRaisesRegex(Exception, "YYYY-MM"):
            parse_source_month("2025/01")

    def test_manifest_count_is_checked(self):
        with tempfile.TemporaryDirectory(prefix="spark-trip-test-") as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps({"source_month": "2025-01", "record_count": 20}),
                encoding="utf-8",
            )
            self.assertEqual(load_expected_count(manifest, None, "2025-01"), 20)
            with self.assertRaisesRegex(Exception, "record_count"):
                load_expected_count(manifest, 19, "2025-01")


@unittest.skipUnless(SparkSession is not None, "pyspark is not installed")
class SparkTripIntegrationSmokeTests(unittest.TestCase):
    def test_synthetic_fixture_runs_the_contract_gate(self):
        from read_trip_integration import run_integration

        spark = (
            SparkSession.builder.master("local[2]")
            .appName("spark-trip-integration-test")
            .config("spark.sql.session.timeZone", "America/New_York")
            .getOrCreate()
        )
        try:
            summary = run_integration(
                spark,
                input_path=str(ROOT / "fixtures" / "citibike" / "202501_sample.csv"),
                source_month="2025-01",
                expected_count=20,
                sample_rows=2,
                display=False,
            )
        finally:
            spark.stop()
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["counts"]["status"], "PARTIAL")
        self.assertEqual(summary["counts"]["counts"]["spark"], 20)
        self.assertEqual(summary["schema"]["station_id_types"], {
            "start_station_id": "string",
            "end_station_id": "string",
        })
        self.assertEqual(summary["data_quality"]["invalid_started_at"], 0)
        self.assertEqual(summary["time_validation"]["timezone"], "America/New_York")


if __name__ == "__main__":
    unittest.main()
