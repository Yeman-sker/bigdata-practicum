import json
import tempfile
import unittest
from pathlib import Path

from citibike.contracts import SOURCE_FIELDS, SPARK_TIME_PATTERNS
from citibike.spark import load_expected_count, parse_source_month

ROOT = Path(__file__).parents[1]

try:
    from pyspark.sql import SparkSession
except ImportError:
    SparkSession = None


class SparkTripIntegrationContractTests(unittest.TestCase):
    def test_source_contract_and_month_are_explicit(self):
        self.assertEqual(len(SOURCE_FIELDS), 13)
        self.assertEqual(SOURCE_FIELDS[5:8], ("start_station_id", "end_station_name", "end_station_id"))
        self.assertEqual(parse_source_month("2025-01"), (2025, 1))
        self.assertIn("yyyy-MM-dd HH:mm:ss.SSS", SPARK_TIME_PATTERNS)
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
    def test_hive_read_drops_csv_header_row(self):
        from citibike.spark import run_integration

        spark = (
            SparkSession.builder.master("local[2]")
            .appName("spark-trip-hive-header-test")
            .config("spark.sql.session.timeZone", "America/New_York")
            .getOrCreate()
        )
        try:
            rows = [
                {
                    "ride_id": "ride_id",
                    "rideable_type": "rideable_type",
                    "started_at": "started_at",
                    "ended_at": "ended_at",
                    "start_station_name": "start_station_name",
                    "start_station_id": "start_station_id",
                    "end_station_name": "end_station_name",
                    "end_station_id": "end_station_id",
                    "start_lat": None,
                    "start_lng": None,
                    "end_lat": None,
                    "end_lng": None,
                    "member_casual": "member_casual",
                    "year": 2025,
                    "month": 1,
                },
                {
                    "ride_id": "ride-1",
                    "rideable_type": "classic_bike",
                    "started_at": "2025-01-01 10:00:00.000",
                    "ended_at": "2025-01-01 10:10:00.000",
                    "start_station_name": "Start",
                    "start_station_id": "5484.09",
                    "end_station_name": "End",
                    "end_station_id": "4199.12",
                    "start_lat": 40.0,
                    "start_lng": -74.0,
                    "end_lat": 40.1,
                    "end_lng": -74.1,
                    "member_casual": "member",
                    "year": 2025,
                    "month": 1,
                },
            ]
            spark.createDataFrame(rows).createOrReplaceTempView("hive_trip_header_fixture")
            summary = run_integration(
                spark,
                hive_table="hive_trip_header_fixture",
                source_month="2025-01",
                expected_count=1,
                display=False,
            )
        finally:
            spark.stop()
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["counts"]["counts"], {"source": 1, "hive": 1, "spark": 1})

    def test_synthetic_fixture_runs_the_contract_gate(self):
        from citibike.spark import run_integration

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
            incomplete = run_integration(
                spark,
                input_path=str(ROOT / "fixtures" / "citibike" / "202501_sample.csv"),
                source_month="2025-01",
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
        self.assertEqual(incomplete["counts"]["status"], "NOT_RUN")
        self.assertEqual(incomplete["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
