from __future__ import annotations

import json
import math
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from citibike.offline import (
    OfflineError,
    _parse_csv_line,
    _timestamp_expr,
    dataset_id,
    load_manifest,
    load_metadata,
    sha256_file,
    write_outputs,
)
from citibike.serving_export import (
    ExportError,
    PublicationUncertainError,
    export_release,
    publication_sql,
    validate_staging,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "day2"
DATASET_ID = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e"

try:
    from pyspark.sql import SparkSession
    from pyspark.sql import types as T
except ImportError:
    SparkSession = None
    T = None


class OfflineContractTest(unittest.TestCase):
    def test_fixture_versions_follow_contract_formula(self) -> None:
        metadata_version = sha256_file(FIXTURE / "metadata.json")
        manifest = load_manifest(FIXTURE / "manifest.json", "2025-01")
        self.assertEqual(
            dataset_id(metadata_version, [("2025-01", manifest["zip_sha256"])]),
            DATASET_ID,
        )
        expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata_version, expected["metadata_version"])

    def test_metadata_requires_unique_current_gbfs_rows(self) -> None:
        loaded = load_metadata(FIXTURE / "metadata.json")
        self.assertEqual([row["station_id"] for row in loaded], ["4199.12", "5484.09"])
        rows = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
        rows.append(dict(rows[0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaisesRegex(OfflineError, "duplicate metadata station_id"):
                load_metadata(path)

    def test_metadata_rejects_malformed_canonical_values(self) -> None:
        row = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))[0]
        invalid_values = {
            "provider_station_id": "",
            "station_id": "bad station",
            "mapping_status": "UNKNOWN",
            "mapping_reason": "MISSING_SHORT_NAME",
            "station_name": 1,
            "lat": math.nan,
            "lon": 181,
            "capacity": 0,
            "region_id": 1,
            "metadata_updated_at": "not-a-timestamp",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            for field, value in invalid_values.items():
                with self.subTest(field=field):
                    changed = dict(row)
                    changed[field] = value
                    path.write_text(json.dumps([changed]), encoding="utf-8")
                    with self.assertRaises(OfflineError):
                        load_metadata(path)

    def test_manifest_rejects_path_traversal(self) -> None:
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        manifest["csv_files"][0]["filename"] = "../part-a.csv"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(OfflineError, "unsafe manifest filename"):
                load_manifest(path, "2025-01")

    def test_record_parser_supports_quoted_commas_and_rejects_multiline_fragments(self) -> None:
        values = [f"field-{index}" for index in range(13)]
        values[4] = "station, with comma"
        import csv

        with tempfile.SpooledTemporaryFile(mode="w+", newline="", encoding="utf-8") as buffer:
            csv.writer(buffer).writerow(values)
            buffer.seek(0)
            self.assertEqual(_parse_csv_line(buffer.read()), tuple(values))
        with self.assertRaisesRegex(OfflineError, "13-column"):
            _parse_csv_line("only,two")
        unterminated = ",".join([f"field-{index}" for index in range(12)] + ['"unterminated'])
        with self.assertRaisesRegex(OfflineError, "malformed CSV"):
            _parse_csv_line(unterminated)


class ServingPublicationTest(unittest.TestCase):
    def _evidence(self, path: Path) -> None:
        release_root = f"/warehouse/releases/{DATASET_ID}"
        path.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "contract_version": "1.1",
                    "release_format": "immutable-v1",
                    "serving_format": "tsv-unquoted-v1",
                    "dataset_id": DATASET_ID,
                    "release_root": release_root,
                    "source_months": ["2025-01"],
                    "min_service_date": "2025-01-08",
                    "max_service_date": "2025-02-01",
                    "published_at_utc": "2025-02-05T12:59:30Z",
                    "tsv_null_token": f"__CITIBIKE_NULL_{DATASET_ID}_0__",
                    "counts": {"valid": 5, "dim": 3, "flow": 144, "profile": 96, "od": 3},
                    "paths": {
                        f"export_{table}": f"{release_root}/serving_export/{table}"
                        for table in (
                            "dim_station_v1",
                            "dws_station_hourly_flow_v1",
                            "dws_station_hour_profile_v1",
                            "dws_station_od_hourly_v1",
                        )
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_publication_is_one_dml_transaction(self) -> None:
        evidence = {
            "source_months": ["2025-01"],
            "min_service_date": "2025-01-08",
            "max_service_date": "2025-02-01",
            "published_at_utc": "2025-02-05T12:59:30Z",
        }
        sql = publication_sql(DATASET_ID, evidence)
        self.assertIn("START TRANSACTION;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertNotIn("CREATE ", sql.upper())
        self.assertEqual(sql.count("INSERT INTO `dws_station_hourly_flow_v1`"), 1)

    def test_sqoop_failure_never_runs_publication(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def runner(command, *, input=None, **kwargs):
            calls.append((command, input))
            if command[0] == "sqoop":
                return subprocess.CompletedProcess(command, 1, "", "forced export failure")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "offline.json"
            password = root / "mysql.password"
            self._evidence(evidence)
            password.write_text("secret\n", encoding="utf-8")
            with self.assertRaisesRegex(ExportError, "forced export failure"):
                export_release(
                    dataset=DATASET_ID,
                    hdfs_root="/warehouse",
                    offline_evidence=evidence,
                    password_file=password,
                    connect="jdbc:mysql://localhost/citibike",
                    host="localhost",
                    port=3306,
                    database="citibike",
                    username="citibike",
                    ddl_path=None,
                    runner=runner,
                )
        mysql_inputs = [stdin or "" for command, stdin in calls if command[0] == "mysql"]
        self.assertFalse(any("START TRANSACTION" in sql for sql in mysql_inputs))
        sqoop = next(command for command, _ in calls if command[0] == "sqoop")
        self.assertIn("--batch", sqoop)
        self.assertEqual(
            sqoop[sqoop.index("--input-null-string") + 1],
            f"__CITIBIKE_NULL_{DATASET_ID}_0__",
        )
        self.assertEqual(
            sqoop[sqoop.index("--export-dir") + 1],
            f"/warehouse/releases/{DATASET_ID}/serving_export/dim_station_v1",
        )

    def test_staging_tables_are_unconstrained_for_explicit_validation(self) -> None:
        ddl = (ROOT / "sql" / "serving.sql").read_text(encoding="utf-8")
        staging = ddl[ddl.index("CREATE TABLE IF NOT EXISTS dim_station_v1_load") :]
        self.assertNotIn("PRIMARY KEY", staging)
        self.assertNotIn(" CHECK ", staging)

    def test_failed_publication_confirmation_is_uncertain(self) -> None:
        table_counts = {
            "dim_station_v1_load": 3,
            "dws_station_hourly_flow_v1_load": 144,
            "dws_station_hour_profile_v1_load": 96,
            "dws_station_od_hourly_v1_load": 3,
        }

        def runner(command, *, input=None, **kwargs):
            if command[0] == "sqoop":
                return subprocess.CompletedProcess(command, 0, "", "")
            sql = input or ""
            if "COUNT(DISTINCT dataset_id)" in sql:
                table = next(name for name in table_counts if f"`{name}`" in sql)
                return subprocess.CompletedProcess(
                    command, 0, f"{table_counts[table]}\t1\t0\n0", ""
                )
            if "WITH expected_profile" in sql:
                return subprocess.CompletedProcess(command, 0, "5\t5\t0\n5\t0\n0\n0", "")
            if "START TRANSACTION" in sql:
                return subprocess.CompletedProcess(command, 1, "", "connection lost")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "offline.json"
            password = root / "mysql.password"
            self._evidence(evidence)
            password.write_text("secret\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicationUncertainError, "could not be confirmed"):
                export_release(
                    dataset=DATASET_ID,
                    hdfs_root="/warehouse",
                    offline_evidence=evidence,
                    password_file=password,
                    connect="jdbc:mysql://localhost/citibike",
                    host="localhost",
                    port=3306,
                    database="citibike",
                    username="citibike",
                    ddl_path=None,
                    runner=runner,
                )

    def test_empty_business_result_is_a_valid_staging_release(self) -> None:
        responses = iter(["0\t0\t0\n0"] * 4 + ["0\t0\t0\n0\t0\n0\n0"])

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, next(responses), "")

        counts = validate_staging(
            DATASET_ID,
            {table: 0 for table in (
                "dim_station_v1", "dws_station_hourly_flow_v1",
                "dws_station_hour_profile_v1", "dws_station_od_hourly_v1",
            )},
            expected_valid=0,
            mysql_bin="mysql", host="localhost", port=3306, database="citibike",
            username="citibike", password="secret", runner=runner,
        )
        self.assertEqual(set(counts.values()), {0})

    def test_staging_totals_must_equal_offline_valid_count(self) -> None:
        responses = iter(["1\t1\t0\n0"] * 4 + ["4\t4\t0\n4\t0\n0\n0"])

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, next(responses), "")

        with self.assertRaisesRegex(ExportError, "offline valid count"):
            validate_staging(
                DATASET_ID,
                {table: 1 for table in (
                    "dim_station_v1", "dws_station_hourly_flow_v1",
                    "dws_station_hour_profile_v1", "dws_station_od_hourly_v1",
                )},
                expected_valid=5,
                mysql_bin="mysql", host="localhost", port=3306, database="citibike",
                username="citibike", password="secret", runner=runner,
            )

    def test_staging_profile_must_match_flow_dates(self) -> None:
        responses = iter(["1\t1\t0\n0"] * 4 + ["5\t5\t0\n5\t0\n1\n0"])

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, next(responses), "")

        with self.assertRaisesRegex(ExportError, "profile"):
            validate_staging(
                DATASET_ID,
                {table: 1 for table in (
                    "dim_station_v1", "dws_station_hourly_flow_v1",
                    "dws_station_hour_profile_v1", "dws_station_od_hourly_v1",
                )},
                expected_valid=5,
                mysql_bin="mysql", host="localhost", port=3306, database="citibike",
                username="citibike", password="secret", runner=runner,
            )

    def test_hive_ddl_rebuilds_external_tables_at_release_root(self) -> None:
        ddl = (ROOT / "hive" / "warehouse_v1.sql").read_text(encoding="utf-8")
        self.assertIn("${hiveconf:release_root}", ddl)
        self.assertLess(
            ddl.index("DROP TABLE IF EXISTS dwd_trip_v1"),
            ddl.index("CREATE EXTERNAL TABLE dwd_trip_v1"),
        )


@unittest.skipUnless(SparkSession is not None, "pyspark is not installed")
class OfflineReleaseIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("offline-release-integration-test")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "America/New_York")
            .getOrCreate()
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def _frames(self, dataset: str) -> dict[str, object]:
        token_zero = f"__CITIBIKE_NULL_{dataset}_0__"
        dim_schema = T.StructType([
            T.StructField("station_id", T.StringType(), False),
            T.StructField("station_name", T.StringType(), True),
            T.StructField("lat", T.DoubleType(), True),
            T.StructField("lon", T.DoubleType(), True),
            T.StructField("capacity", T.IntegerType(), True),
            T.StructField("region_id", T.StringType(), True),
            T.StructField("is_current", T.BooleanType(), False),
            T.StructField("metadata_source", T.StringType(), False),
            T.StructField("metadata_updated_at", T.TimestampType(), False),
        ])
        updated = datetime(2025, 2, 5, 12, 59, tzinfo=timezone.utc)
        dim = self.spark.createDataFrame([
            ("a", 'Station "Quoted" \\ path', 40.0, -74.0, None, "line\tbreak", True, "GBFS", updated),
            ("b", r"\N", 40.1, -74.1, 1, "region", True, "GBFS", updated),
            ("c", None, 40.2, -74.2, 2, None, False, "HISTORICAL", updated),
            ("d", token_zero, 40.3, -74.3, 3, "region", True, "GBFS", updated),
        ], dim_schema)
        flow = self.spark.createDataFrame([
            ("a", date(2025, 1, 1), 0, 1, 0, 1, 1, 0, 0, 0, 0)
        ], "station_id string, service_date date, hour byte, inbound_rides long, outbound_rides long, net_flow long, total_activity long, electric_outbound long, classic_outbound long, member_outbound long, casual_outbound long")
        profile = self.spark.createDataFrame([
            ("a", 3, 0, 1.0, 0.0, 1.0, 1.0, 1)
        ], "station_id string, day_of_week byte, hour byte, avg_inbound double, avg_outbound double, avg_net_flow double, median_net_flow double, sample_days long")
        od = self.spark.createDataFrame([
            (date(2025, 1, 1), 0, "a", "b", 1)
        ], "service_date date, hour byte, from_station_id string, to_station_id string, ride_count long")
        dwd = self.spark.createDataFrame(
            [("ride", 2025, 1)], "ride_id string, source_year int, source_month int"
        )
        return {
            "dataset_id": dataset,
            "dwd_trip_v1": dwd,
            "dim_station_v1": dim,
            "dws_station_hourly_flow_v1": flow,
            "dws_station_hour_profile_v1": profile,
            "dws_station_od_hourly_v1": od,
        }

    @staticmethod
    def _evidence(dataset: str, built_at: str = "2025-02-05T12:59:00Z") -> dict[str, object]:
        return {
            "status": "PASS",
            "contract_version": "1.1",
            "dataset_id": dataset,
            "metadata_version": "b" * 64,
            "source_months": ["2025-01"],
            "ingest_batch_ids": ["c" * 64],
            "built_at_utc": built_at,
            "published_at_utc": "2025-02-05T12:59:30Z",
            "min_service_date": "2025-01-01",
            "max_service_date": "2025-01-01",
            "counts": {
                "valid": 1, "dim": 4, "flow": 1, "profile": 1, "od": 1, "ods": None,
            },
        }

    def test_release_commit_is_idempotent_and_tsv_is_literal(self) -> None:
        from pyspark.sql import functions as F

        timestamp_values = [
            "01/02/2025 03:04:05.1",
            "01/02/2025 03:04:05",
            "2025-01-02 03:04:05.123456",
            "2025-01-02 03:04:05",
            "2025-01-02T03:04:05.123",
            "2025-01-02T03:04:05",
        ]
        parsed = (
            self.spark.createDataFrame([(value,) for value in timestamp_values], "value string")
            .select(F.date_format(_timestamp_expr(F, "value"), "yyyy-MM-dd HH:mm:ss.SSSSSS"))
            .collect()
        )
        self.assertEqual(
            [row[0] for row in parsed],
            [
                "2025-01-02 03:04:05.100000",
                "2025-01-02 03:04:05.000000",
                "2025-01-02 03:04:05.123456",
                "2025-01-02 03:04:05.000000",
                "2025-01-02 03:04:05.123000",
                "2025-01-02 03:04:05.000000",
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "warehouse").as_uri()
            release = write_outputs(
                self._frames(DATASET_ID), root, evidence=self._evidence(DATASET_ID)
            )
            self.assertTrue(release["release_root"].endswith(f"/releases/{DATASET_ID}"))
            self.assertEqual(
                release["tsv_null_token"], f"__CITIBIKE_NULL_{DATASET_ID}_1__"
            )
            release_path = Path(unquote(urlparse(release["release_root"]).path))
            part = next((release_path / "serving_export" / "dim_station_v1").glob("part-*"))
            lines = part.read_text(encoding="utf-8").splitlines()
            self.assertIn('a\tStation "Quoted" \\ path\t40.0\t-74.0\t', lines[0])
            self.assertIn("\tline break\t1\tGBFS\t", lines[0])
            self.assertTrue(lines[1].startswith("b\t\\N\t"))
            self.assertTrue(lines[2].startswith(f"c\t{release['tsv_null_token']}\t"))
            self.assertTrue(lines[3].startswith(f"d\t__CITIBIKE_NULL_{DATASET_ID}_0__\t"))

            descriptor = release_path / "release.json"
            descriptor_mtime = descriptor.stat().st_mtime_ns
            rerun_evidence = self._evidence(DATASET_ID, "2030-01-01T00:00:00Z")
            rerun_evidence["counts"]["ods"] = 1
            reused = write_outputs(
                self._frames(DATASET_ID),
                root,
                evidence=rerun_evidence,
            )
            self.assertEqual(reused["built_at_utc"], "2025-02-05T12:59:00Z")
            self.assertEqual(descriptor.stat().st_mtime_ns, descriptor_mtime)

            failed_dataset = "a" * 64
            with patch("citibike.offline._choose_null_token", side_effect=RuntimeError("forced")):
                with self.assertRaisesRegex(RuntimeError, "forced"):
                    write_outputs(
                        self._frames(failed_dataset),
                        root,
                        evidence=self._evidence(failed_dataset),
                    )
            self.assertTrue(descriptor.is_file())
            self.assertFalse((release_path.parent / failed_dataset).exists())


if __name__ == "__main__":
    unittest.main()
