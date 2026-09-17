from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from citibike.offline import (
    OfflineError,
    _parse_csv_line,
    dataset_id,
    load_manifest,
    load_metadata,
    sha256_file,
)
from citibike.serving_export import (
    ExportError,
    export_release,
    publication_sql,
    validate_staging,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "day2"
DATASET_ID = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e"


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
        rows = load_metadata(FIXTURE / "metadata.json")
        self.assertEqual([row["station_id"] for row in rows], ["4199.12", "5484.09"])
        rows.append(dict(rows[0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaisesRegex(OfflineError, "duplicate metadata station_id"):
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


class ServingPublicationTest(unittest.TestCase):
    def _evidence(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "dataset_id": DATASET_ID,
                    "source_months": ["2025-01"],
                    "min_service_date": "2025-01-08",
                    "max_service_date": "2025-02-01",
                    "published_at_utc": "2025-02-05T12:59:30Z",
                    "counts": {"dim": 3, "flow": 144, "profile": 96, "od": 3},
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
        self.assertEqual(sqoop[sqoop.index("--input-null-string") + 1], r"\\N")

    def test_staging_tables_are_unconstrained_for_explicit_validation(self) -> None:
        ddl = (ROOT / "sql" / "serving.sql").read_text(encoding="utf-8")
        staging = ddl[ddl.index("CREATE TABLE IF NOT EXISTS dim_station_v1_load") :]
        self.assertNotIn("PRIMARY KEY", staging)
        self.assertNotIn(" CHECK ", staging)

    def test_empty_business_result_is_a_valid_staging_release(self) -> None:
        responses = iter(["0\t0\t0\n0"] * 4 + ["0\t0\n0\t0\n0"])

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, next(responses), "")

        counts = validate_staging(
            DATASET_ID,
            {table: 0 for table in (
                "dim_station_v1", "dws_station_hourly_flow_v1",
                "dws_station_hour_profile_v1", "dws_station_od_hourly_v1",
            )},
            mysql_bin="mysql", host="localhost", port=3306, database="citibike",
            username="citibike", password="secret", runner=runner,
        )
        self.assertEqual(set(counts.values()), {0})


if __name__ == "__main__":
    unittest.main()
