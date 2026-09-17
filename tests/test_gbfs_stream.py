import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from citibike.gbfs_stream import (
    CollectionError,
    StreamError,
    build_batch,
    build_canonical_metadata,
    parse_args,
    replay_records,
    run,
    run_once,
    run_replay,
    send_batch,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "day2"


class FakeProducer:
    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def send_records(self, records):
        self.calls.append(records)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("broker unavailable")


def fixture_feeds():
    information = json.loads((FIXTURE / "station_information.json").read_text())
    status_bytes = (FIXTURE / "station_status.json").read_bytes()
    status = json.loads(status_bytes)
    return information, status, status_bytes


class GbfsStreamTest(unittest.TestCase):
    def test_fixture_batch_matches_canonical_hash_contract(self):
        information, status, status_bytes = fixture_feeds()
        batch = build_batch(
            information,
            status,
            status_bytes,
            datetime(2025, 2, 5, 13, 0, 2, tzinfo=timezone.utc),
            "FIXTURE",
        )
        self.assertEqual(
            batch.metadata_version,
            "84fb15f78a673f21687fce423e223a2970f7276903398e6e0c19c502ab1ea41b",
        )
        self.assertEqual(
            batch.snapshot_id,
            "144dc6c63f679f72b02b9ada5129d5393976fdb04be00534eda8ebe73c3fb3c3",
        )
        self.assertEqual(
            [record["key"] for record in batch.records],
            ["4199.12", "5484.09", "__snapshot_end__"],
        )
        self.assertEqual(batch.records[-1]["value"]["station_count"], 2)
        self.assertTrue(
            all(
                record["headers"]["data_origin"] == "FIXTURE"
                for record in batch.records
            )
        )

    def test_mapping_isolates_duplicate_and_missing_short_names(self):
        information, status, _ = fixture_feeds()
        information["data"]["stations"][0]["short_name"] = "same"
        information["data"]["stations"][1]["short_name"] = "same"
        information["data"]["stations"][1]["capacity"] = 0
        status["data"]["stations"].append(
            {
                "station_id": "missing-provider",
                "num_bikes_available": -1,
                "num_docks_available": 0,
                "num_bikes_disabled": 0,
                "num_docks_disabled": 0,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
                "last_reported": status["last_updated"],
                "vehicle_types_available": [],
            }
        )
        metadata, mapping, warnings = build_canonical_metadata(information, status)
        self.assertEqual(
            mapping["00000000-0000-4000-8000-000000000001"],
            "gbfs:00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(
            next(
                row
                for row in metadata
                if row["provider_station_id"] == "missing-provider"
            )["mapping_reason"],
            "MISSING_METADATA",
        )
        self.assertEqual(
            next(
                row
                for row in metadata
                if row["provider_station_id"] == "missing-provider"
            )["capacity"],
            None,
        )
        self.assertEqual(
            sum(item["type"] == "isolated_mapping" for item in warnings), 2
        )
        self.assertTrue(any(item["type"] == "missing_metadata" for item in warnings))

    def test_station_end_is_not_sent_after_station_failure(self):
        information, status, status_bytes = fixture_feeds()
        batch = build_batch(
            information, status, status_bytes, datetime.now(timezone.utc)
        )
        producer = FakeProducer(fail_on_call=1)
        with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
            send_batch(producer, batch)
        self.assertEqual(len(producer.calls), 1)
        self.assertEqual(len(producer.calls[0]), 2)

    def test_replay_rewrites_origin_but_preserves_payload_and_ids(self):
        records = replay_records(FIXTURE / "events.ndjson")
        self.assertEqual(records[0]["headers"]["data_origin"], "GBFS_REPLAY")
        self.assertEqual(records[0]["value"]["snapshot_at_utc"], "2025-02-05T13:00:00Z")
        self.assertEqual(records[-1]["value"]["station_count"], 2)

        live = json.loads((FIXTURE / "events.ndjson").read_text().splitlines()[0])
        live["headers"]["data_origin"] = "GBFS_LIVE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.ndjson"
            path.write_text(json.dumps(live) + "\n")
            with self.assertRaisesRegex(StreamError, "exactly one final"):
                replay_records(path)

    def test_replay_rejects_mixed_station_timestamps(self):
        records = [
            json.loads(line)
            for line in (FIXTURE / "events.ndjson").read_text().splitlines()
        ]
        records[1]["value"]["snapshot_at_utc"] = "2025-02-05T14:00:00Z"
        records[1]["value"]["snapshot_at_local"] = "2025-02-05T09:00:00-05:00"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed-times.ndjson"
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            with self.assertRaisesRegex(StreamError, "station record 2"):
                replay_records(path)

    def test_zero_station_batch_is_valid_replay_input(self):
        information, status, _ = fixture_feeds()
        status["data"]["stations"] = []
        batch = build_batch(
            information,
            status,
            b'{"data":{"stations":[]}}',
            datetime(2025, 2, 5, 13, tzinfo=timezone.utc),
            "FIXTURE",
        )
        self.assertEqual(batch.station_count, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.ndjson"
            path.write_text(json.dumps(batch.records[-1]) + "\n")
            replayed = replay_records(path)
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0]["headers"]["data_origin"], "GBFS_REPLAY")

    def test_replay_rejects_station_id_outside_contract(self):
        records = [
            json.loads(line)
            for line in (FIXTURE / "events.ndjson").read_text().splitlines()
        ]
        station = records[0]
        station_id = "x" * 129
        station["key"] = station_id
        station["value"]["station_id"] = station_id
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-station-id.ndjson"
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            with self.assertRaisesRegex(StreamError, "at most 128"):
                replay_records(path)

    def test_replay_rejects_invalid_snapshot_end_timestamps(self):
        information, status, _ = fixture_feeds()
        status["data"]["stations"] = []
        batch = build_batch(
            information,
            status,
            b'{"data":{"stations":[]}}',
            datetime(2025, 2, 5, 13, tzinfo=timezone.utc),
            "FIXTURE",
        )
        for field, invalid_value in {
            "snapshot_at_utc": "2025-02-05 13:00:00Z",
            "ingested_at_utc": "2025-02-05T08:00:00-05:00",
        }.items():
            with self.subTest(field=field):
                end = dict(batch.records[-1])
                end["value"] = dict(end["value"])
                end["value"][field] = invalid_value
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / f"invalid-{field}.ndjson"
                    path.write_text(json.dumps(end) + "\n")
                    with self.assertRaisesRegex(StreamError, "snapshot_end"):
                        replay_records(path)

    def test_replay_requires_matching_metadata_file_and_logs_failure(self):
        producer = FakeProducer()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(StreamError, "metadata_file is required"):
                run_replay(FIXTURE / "events.ndjson", producer, output)
            failure = json.loads((output / "collection_log.json").read_text())
            self.assertEqual(failure["snapshots"][-1]["status"], "FAILED_REPLAY")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                run_replay(
                    FIXTURE / "events.ndjson",
                    FakeProducer(fail_on_call=1),
                    output,
                    FIXTURE / "metadata.json",
                )
            failure = json.loads((output / "collection_log.json").read_text())
            self.assertEqual(failure["snapshots"][-1]["status"], "FAILED_REPLAY")

        producer = FakeProducer()
        result = run_replay(
            FIXTURE / "events.ndjson",
            producer,
            metadata_file=FIXTURE / "metadata.json",
        )
        self.assertEqual(result["status"], "PUBLISHED")

    def test_replay_dry_run_does_not_write_published_log(self):
        producer = FakeProducer()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run_replay(
                FIXTURE / "events.ndjson",
                producer,
                output,
                FIXTURE / "metadata.json",
                dry_run=True,
            )
            self.assertEqual(result["status"], "DRY_RUN")
            self.assertEqual(len(producer.calls), 2)
            self.assertFalse((output / "collection_log.json").exists())

    def test_invalid_feed_preserves_raw_and_failure_log(self):
        information, status, _ = fixture_feeds()
        feeds = {
            "discovery": {
                "version": "2.3",
                "data": {
                    "en": {
                        "feeds": [
                            {"name": "station_information", "url": "info"},
                            {"name": "station_status", "url": "status"},
                            {"name": "vehicle_types", "url": "types"},
                        ]
                    }
                },
            },
            "info": information,
            "status": status,
            "types": {"version": "2.2", "data": {"vehicle_types": []}},
        }

        def fetcher(url, timeout):
            return feeds[url]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaises(CollectionError):
                run_once(
                    output,
                    "unused",
                    "topic",
                    "discovery",
                    producer=FakeProducer(),
                    fetcher=fetcher,
                )
            log = json.loads((output / "collection_log.json").read_text())
            record = log["snapshots"][-1]
            self.assertEqual(record["status"], "FAILED_COLLECTION")
            self.assertEqual(record["failure_count"], 1)
            self.assertEqual(record["fetched_feed_count"], 4)
            failure_dir = output / record["failure_directory"]
            self.assertTrue((failure_dir / "station_status.json").exists())

    def test_replay_parser_requires_metadata_file(self):
        with self.assertRaisesRegex(SystemExit, "metadata-file is required"):
            parse_args(
                [
                    "--replay",
                    str(FIXTURE / "events.ndjson"),
                    "--bootstrap-servers",
                    "localhost:9092",
                    "--output-dir",
                    "/tmp/gbfs-stream-test",
                ]
            )

    def test_run_once_records_and_skips_identical_published_source(self):
        information, status, status_bytes = fixture_feeds()
        feeds = {
            "discovery": {
                "version": "2.3",
                "data": {
                    "en": {
                        "feeds": [
                            {"name": "station_information", "url": "info"},
                            {"name": "station_status", "url": "status"},
                            {"name": "vehicle_types", "url": "types"},
                        ]
                    }
                },
            },
            "info": information,
            "status": status,
            "types": {
                "version": "2.3",
                "data": {
                    "vehicle_types": [
                        {
                            "vehicle_type_id": "fixture-classic",
                            "form_factor": "bicycle",
                            "propulsion_type": "human",
                        }
                    ]
                },
            },
        }

        def fetcher(url, timeout):
            return feeds[url]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = run_once(
                output,
                "unused",
                "topic",
                "discovery",
                producer=FakeProducer(),
                fetcher=fetcher,
            )
            second_producer = FakeProducer()
            second = run_once(
                output,
                "unused",
                "topic",
                "discovery",
                producer=second_producer,
                fetcher=fetcher,
            )
            self.assertEqual(first["status"], "PUBLISHED")
            self.assertEqual(second["status"], "SKIPPED_DUPLICATE")
            self.assertEqual(second_producer.calls, [])
            log = json.loads((output / "collection_log.json").read_text())
            self.assertEqual(log["snapshots"][0]["mapping_success_count"], 2)
            self.assertEqual(log["snapshots"][0]["mapping_isolated_count"], 0)
            self.assertTrue(
                (output / "metadata" / f"{first['metadata_version']}.json").exists()
            )
            self.assertEqual(
                status_bytes, (FIXTURE / "station_status.json").read_bytes()
            )

    def test_failed_send_retries_persisted_batch_before_collecting_next(self):
        information, status, status_bytes = fixture_feeds()
        ingested_at = datetime(2025, 2, 5, 13, 0, 2, tzinfo=timezone.utc)
        batch = build_batch(information, status, status_bytes, ingested_at)
        collection = (
            batch,
            {
                "discovery": b"{}",
                "station_information": json.dumps(information).encode(),
                "station_status": status_bytes,
                "vehicle_types": b"{}",
            },
            {},
            {"discovery": {}, "discovery_bytes": b"{}"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first_producer = FakeProducer(fail_on_call=2)
            with (
                patch("citibike.gbfs_stream.collect_batch", return_value=collection),
                self.assertRaisesRegex(RuntimeError, "broker unavailable"),
            ):
                run_once(
                    output,
                    "unused",
                    "topic",
                    producer=first_producer,
                )
            failed_log = json.loads((output / "collection_log.json").read_text())
            failed = failed_log["snapshots"][-1]
            self.assertEqual(failed["status"], "FAILED_SEND")
            self.assertTrue(
                (output / failed["snapshot_directory"] / "events.ndjson").exists()
            )

            retry_producer = FakeProducer()
            with patch(
                "citibike.gbfs_stream.collect_batch",
                side_effect=AssertionError("must retry the persisted batch first"),
            ):
                retried = run_once(
                    output,
                    "unused",
                    "topic",
                    producer=retry_producer,
                )
            self.assertEqual(retried["status"], "PUBLISHED")
            self.assertEqual(retry_producer.calls, first_producer.calls)
            self.assertEqual(retried["ingested_at_utc"], failed["ingested_at_utc"])

    def test_dry_run_does_not_poison_publish_deduplication(self):
        information, status, status_bytes = fixture_feeds()
        batch = build_batch(
            information,
            status,
            status_bytes,
            datetime(2025, 2, 5, 13, 0, 2, tzinfo=timezone.utc),
        )
        collection = (
            batch,
            {
                "discovery": b"{}",
                "station_information": json.dumps(information).encode(),
                "station_status": status_bytes,
                "vehicle_types": b"{}",
            },
            {},
            {"discovery": {}, "discovery_bytes": b"{}"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch("citibike.gbfs_stream.collect_batch", return_value=collection):
                dry = run_once(
                    output,
                    "unused",
                    "topic",
                    producer=FakeProducer(),
                    dry_run=True,
                )
                real_producer = FakeProducer()
                real = run_once(
                    output,
                    "unused",
                    "topic",
                    producer=real_producer,
                )
            self.assertEqual(dry["status"], "DRY_RUN")
            self.assertEqual(real["status"], "PUBLISHED")
            self.assertEqual(len(real_producer.calls), 2)

    def test_conflict_is_counted_as_failed_conflict(self):
        information, status, status_bytes = fixture_feeds()
        ingested_at = datetime(2025, 2, 5, 13, 0, 2, tzinfo=timezone.utc)
        first_batch = build_batch(information, status, status_bytes, ingested_at)
        conflicting_status_bytes = status_bytes + b"\n"
        second_batch = build_batch(
            information, status, conflicting_status_bytes, ingested_at
        )
        base = (
            {
                "discovery": b"{}",
                "station_information": json.dumps(information).encode(),
                "station_status": status_bytes,
                "vehicle_types": b"{}",
            },
            {},
            {"discovery": {}, "discovery_bytes": b"{}"},
        )
        second = (
            second_batch,
            {**base[0], "station_status": conflicting_status_bytes},
            base[1],
            base[2],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch(
                "citibike.gbfs_stream.collect_batch",
                side_effect=[(first_batch, *base), second],
            ):
                self.assertEqual(
                    run_once(output, "unused", "topic", producer=FakeProducer())[
                        "status"
                    ],
                    "PUBLISHED",
                )
                conflict = run_once(output, "unused", "topic", producer=FakeProducer())
            self.assertEqual(conflict["status"], "REJECTED_CONFLICT")
            self.assertEqual(conflict["failure_count"], 1)
            self.assertEqual(conflict["conflict_count"], 1)
            self.assertEqual(conflict["failed_batch_count"], 1)

    def test_continuous_mode_retries_after_expected_failure(self):
        args = parse_args(
            [
                "--bootstrap-servers",
                "localhost:9092",
                "--output-dir",
                "/tmp/gbfs-stream-test",
                "--snapshots",
                "0",
                "--interval-seconds",
                "1",
                "--dry-run",
            ]
        )
        calls = []
        sleeps = []

        def fake_run_once(*run_args, **run_kwargs):
            calls.append(run_args)
            if len(calls) == 1:
                raise StreamError("temporary source failure")
            return {"status": "PUBLISHED"}

        def fake_sleep(seconds):
            sleeps.append(seconds)
            args.snapshots = 1

        with (
            patch("citibike.gbfs_stream.run_once", side_effect=fake_run_once),
            patch("citibike.gbfs_stream.time.sleep", side_effect=fake_sleep),
        ):
            run(args)

        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1.0])


if __name__ == "__main__":
    unittest.main()
