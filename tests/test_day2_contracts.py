"""Validate the shared specifications and examples, not a second business implementation."""

import csv
import hashlib
import json
import re
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from openapi_spec_validator import validate_spec

from citibike.gbfs import normalize_station_status, validate_event


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/day2"


def read_json(name):
    return json.loads((FIXTURE / name).read_text())


class Day2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec_path = ROOT / "docs/contracts/openapi.yaml"
        cls.spec = yaml.safe_load(cls.spec_path.read_text())
        cls.expected = read_json("expected.json")
        cls.tables = cls.expected["tables"]

    def validator(self, name):
        return Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}", "components": self.spec["components"]},
            format_checker=FormatChecker(),
        )

    def test_openapi_and_every_http_example(self):
        validate_spec(self.spec, base_uri=self.spec_path.as_uri())
        self.assertEqual(len(self.spec["paths"]), 3)
        for example in read_json("http-examples.json").values():
            response = self.spec["paths"][example["x-path"]]["get"]["responses"][str(example["x-status"])]
            name = response["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
            with self.subTest(name=example["summary"]):
                self.validator(name).validate(example["value"])
                if example["x-path"] == "/api/v1/map" and example["x-status"] != 400:
                    self.validator("MapQuery").validate(example["x-query"])
        for query in [{"mode": "live", "hour": 8}, {"mode": "replay"}, {"mode": "livee"}]:
            with self.assertRaises(ValidationError):
                self.validator("MapQuery").validate(query)
        self.validator("MapQuery").validate({"mode": "replay", "service_date": "2025-01-15", "hour": 8})

    def test_canonical_mapping_transport_and_hashes(self):
        metadata = read_json("metadata.json")
        meta_hash = hashlib.sha256((FIXTURE / "metadata.json").read_bytes()).hexdigest()
        self.assertEqual(meta_hash, self.expected["metadata_version"])
        mapping = {row["provider_station_id"]: row["station_id"] for row in metadata}
        for row in metadata:
            self.validator("Metadata").validate(row)
        raw = read_json("station_status.json")
        normalized = normalize_station_status(raw, datetime(2025, 2, 5, 13, 0, 2, tzinfo=timezone.utc))
        records = [json.loads(line) for line in (FIXTURE / "events.ndjson").read_text().splitlines()]
        station_records = records[:-1]
        for record, old_event in zip(station_records, normalized, strict=True):
            self.validator("KafkaRecord").validate(record)
            self.assertEqual(validate_event(record["value"]), [])
            self.assertEqual(record["key"], mapping[old_event["station_id"]])
            self.assertEqual(record["value"], old_event | {"station_id": record["key"]})
        self.validator("KafkaRecord").validate(records[-1])
        self.assertEqual(records[-1]["value"]["station_count"], len(station_records))
        snapshot_hash = hashlib.sha256(
            (FIXTURE / "station_status.json").read_bytes() + b"\n" + meta_hash.encode()
        ).hexdigest()
        self.assertEqual(snapshot_hash, self.expected["snapshot_id"])
        manifest = read_json("manifest.json")
        dataset_hash = hashlib.sha256(f"1.1\n{meta_hash}\n2025-01:{manifest['zip_sha256']}\n".encode()).hexdigest()
        self.assertEqual(dataset_hash, self.expected["dataset_id"])

    def test_historical_lineage_dense_hours_and_profiles(self):
        source = []
        for path in sorted((FIXTURE / "trips").glob("*.csv")):
            with path.open() as stream:
                source.extend(csv.DictReader(stream))
        self.assertEqual(len(source), 8)
        dwd = self.tables["dwd_trip_v1"]
        self.assertEqual(len(dwd), len(source))
        self.assertEqual(sum(row["is_duplicate_ride"] for row in dwd), 1)
        valid = [row for row in dwd if row["is_valid_station_trip"]]
        self.assertEqual(len(valid), 5)
        self.assertEqual(len({(r["ingest_batch_id"], r["source_file"], r["source_row_number"]) for r in dwd}), 8)
        flow = self.tables["dws_station_hourly_flow_v1"]
        od = self.tables["dws_station_od_hourly_v1"]
        self.assertEqual(len(flow), 144)
        self.assertEqual(len(od), 3)
        for field in ["inbound_rides", "outbound_rides"]:
            self.assertEqual(sum(r[field] for r in flow), len(valid))
        self.assertEqual(sum(r["ride_count"] for r in od), len(valid))
        days = defaultdict(list)
        profiles = defaultdict(list)
        for row in flow:
            self.assertEqual(row["net_flow"], row["inbound_rides"] - row["outbound_rides"])
            self.assertEqual(row["total_activity"], row["inbound_rides"] + row["outbound_rides"])
            days[row["station_id"], row["service_date"]].append(row["hour"])
            weekday = datetime.fromisoformat(row["service_date"]).isoweekday()
            profiles[row["station_id"], weekday, row["hour"]].append(row)
        for hours in days.values():
            self.assertEqual(sorted(hours), list(range(24)))
        self.assertEqual(len(self.tables["dws_station_hour_profile_v1"]), 96)
        for row in self.tables["dws_station_hour_profile_v1"]:
            group = profiles[row["station_id"], row["day_of_week"], row["hour"]]
            self.assertEqual(row["sample_days"], len(group))
            for actual, base in [("avg_inbound", "inbound_rides"), ("avg_outbound", "outbound_rides"), ("avg_net_flow", "net_flow")]:
                self.assertAlmostEqual(row[actual], mean(r[base] for r in group))
            self.assertAlmostEqual(row["median_net_flow"], median(r["net_flow"] for r in group))
        cross_month = next(r for r in flow if r["station_id"] == "7354.01" and r["hour"] == 0)
        self.assertEqual((cross_month["service_date"], cross_month["inbound_rides"]), ("2025-02-01", 1))

    def test_api_matches_serving_tables_and_expiry_cases(self):
        examples = read_json("http-examples.json")
        live = examples["live"]["value"]
        rows = [{k: v for k, v in r.items() if k != "snapshot_id"} for r in self.tables["ads_station_current_risk"]]
        self.assertEqual(live["stations"], rows)
        suggestion = live["suggestions"][0]
        self.assertEqual((suggestion["move_bikes"], suggestion["distance_meters"]), (8, 84))
        for row in rows:
            self.assertEqual(row["projected_bikes_1h"], row["num_bikes_available"] + row["expected_net_flow_1h"])
        for row in examples["replay"]["value"]["stations"]:
            self.assertIsNone(row["num_bikes_available"])
            self.assertEqual(row["current_status"], "NOT_APPLICABLE")
        for name in ["no_baseline", "stale"]:
            self.assertEqual(examples[name]["value"]["suggestions"], [])
            for row in examples[name]["value"]["stations"]:
                self.assertIsNone(row["projected_bikes_1h"])
        self.assertEqual(examples["no_baseline"]["value"]["stations"][0]["current_status"], rows[0]["current_status"])
        cases = read_json("cases.json")
        self.assertEqual((len(cases["risk"]), len(cases["rebalance"]), len(cases["batches"])), (22, 9, 10))
        double = next(c for c in cases["rebalance"] if c["name"] == "one-source-two-targets")
        self.assertEqual([r["move"] for r in double["expected"]], [8, 4])
        self.assertEqual(sum(r["move"] for r in double["expected"]), 12)

    def test_serving_ddl_columns_match_every_fixture_table(self):
        ddl = (ROOT / "sql/serving.sql").read_text()
        definitions = dict(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\) ENGINE=InnoDB", ddl, re.S))
        for name, rows in self.tables.items():
            if name == "dwd_trip_v1":
                continue
            self.assertIn(name, definitions)
            columns = set(re.findall(r"\b(\w+)\s+(?:VARCHAR|CHAR|INT|TINYINT|BIGINT|DOUBLE|DATETIME|BOOLEAN|JSON|DATE)\b", definitions[name]))
            for row in rows:
                self.assertEqual(set(row), columns, name)

    def test_local_document_links(self):
        paths = [ROOT / "README.md", ROOT / "CONTEXT.md", FIXTURE / "README.md"]
        paths += list((ROOT / "docs/contracts").glob("*.md"))
        paths += list((ROOT / "docs/plans").glob("*.md"))
        paths += [ROOT / "docs" / name for name in ["product.md", "architecture.md", "runbook.md"]]
        paths += list((ROOT / "docs/adr").glob("*.md"))
        for path in paths:
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
                if not target.startswith(("http://", "https://", "#")):
                    self.assertTrue((path.parent / target.split("#", 1)[0]).exists(), f"{path}: {target}")


if __name__ == "__main__":
    unittest.main()
