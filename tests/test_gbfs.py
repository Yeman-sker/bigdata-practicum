import unittest
from datetime import datetime, timezone

from citibike.gbfs import (
    discover_feed_urls,
    normalize_station_status,
    parse_args,
    _select_sample_events,
    validate_station_information,
    validate_station_status_feed,
    validate_vehicle_types,
    station_status_quality_warnings,
    validate_event,
)


class GbfsCollectorTest(unittest.TestCase):
    def test_discovers_localized_gbfs_23_layout(self):
        discovery = {
            "data": {"en": {"feeds": [
                {"name": "station_status", "url": "https://example/status"},
                {"name": "station_information", "url": "https://example/info"},
                {"name": "vehicle_types", "url": "https://example/types"},
            ]}}
        }
        self.assertEqual(
            discover_feed_urls(discovery),
            {
                "station_information": "https://example/info",
                "station_status": "https://example/status",
                "vehicle_types": "https://example/types",
            },
        )

    def test_normalizes_posix_times_and_numeric_booleans(self):
        status = {
            "version": "2.3",
            "last_updated": 0,
            "data": {"stations": [{
                "station_id": "5484.09",
                "num_bikes_available": 3,
                "num_bikes_disabled": 1,
                "num_docks_available": None,
                "num_docks_disabled": 0,
                "is_installed": 1,
                "is_renting": 0,
                "is_returning": True,
                "last_reported": 60,
                "vehicle_types_available": [{"vehicle_type_id": "1", "count": 3}],
            }]},
        }
        events = normalize_station_status(
            status, datetime(2026, 9, 11, 10, 0, 3, tzinfo=timezone.utc)
        )
        self.assertEqual(events[0]["station_id"], "5484.09")
        self.assertEqual(events[0]["snapshot_at_utc"], "1970-01-01T00:00:00Z")
        self.assertEqual(events[0]["snapshot_at_local"], "1969-12-31T19:00:00-05:00")
        self.assertEqual(events[0]["last_reported_at_utc"], "1970-01-01T00:01:00Z")
        self.assertFalse(events[0]["is_renting"])
        self.assertEqual(validate_event(events[0]), [])

    def test_event_validator_requires_nullable_keys_and_correct_timezones(self):
        event = normalize_station_status({
            "version": "2.3",
            "last_updated": 0,
            "data": {"stations": [{
                "station_id": "123",
                "num_bikes_available": 1,
                "num_bikes_disabled": None,
                "num_docks_available": None,
                "num_docks_disabled": None,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
                "last_reported": 0,
                "vehicle_types_available": [],
            }]},
        })[0]
        incomplete = dict(event)
        del incomplete["num_docks_available"]
        self.assertTrue(any("num_docks_available" in error for error in validate_event(incomplete)))

        naive = dict(event, snapshot_at_utc="1970-01-01T00:00:00")
        self.assertTrue(any("timezone" in error for error in validate_event(naive)))

        wrong_local = dict(event, snapshot_at_local="1970-01-01T00:00:00Z")
        self.assertTrue(any("America/New_York" in error for error in validate_event(wrong_local)))

    def test_normalization_rejects_non_23_source_version(self):
        status = {
            "version": "3.0",
            "last_updated": 1,
            "data": {"stations": []},
        }
        with self.assertRaisesRegex(ValueError, "version"):
            normalize_station_status(status)

    def test_provider_nullable_counts_are_optional_but_last_reported_is_required(self):
        status = {
            "version": "2.3",
            "last_updated": 1,
            "data": {"stations": [{
                "station_id": "123",
                "num_bikes_available": 1,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
                "last_reported": 1,
                "vehicle_types_available": [],
            }]},
        }
        event = normalize_station_status(status)[0]
        self.assertIsNone(event["num_bikes_disabled"])
        self.assertIsNone(event["num_docks_available"])
        self.assertIsNone(event["num_docks_disabled"])

        missing_last_reported = {
            **status,
            "data": {"stations": [{**status["data"]["stations"][0]}]},
        }
        del missing_last_reported["data"]["stations"][0]["last_reported"]
        with self.assertRaisesRegex(ValueError, "last_reported"):
            normalize_station_status(missing_last_reported)

    def test_vehicle_count_mismatch_is_recorded_as_quality_warning(self):
        status = {
            "version": "2.3",
            "last_updated": 1,
            "data": {"stations": [{
                "station_id": "123",
                "num_bikes_available": 1,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
                "last_reported": 1,
                "vehicle_types_available": [
                    {"vehicle_type_id": "1", "count": 2},
                ],
            }]},
        }
        warnings = station_status_quality_warnings(status)
        self.assertEqual(warnings[0]["type"], "vehicle_count_mismatch")
        self.assertEqual(warnings[0]["num_bikes_available"], 1)
        self.assertEqual(warnings[0]["vehicle_types_available_total"], 2)

    def test_station_information_and_vehicle_type_schema_checks(self):
        station_information = {
            "version": "2.3",
            "data": {"stations": [{
                "station_id": "123",
                "name": "Example",
                "lat": 40.7,
                "lon": -74.0,
                "capacity": None,
                "region_id": None,
            }]},
        }
        self.assertEqual(validate_station_information(station_information), [])
        invalid_station = dict(station_information)
        invalid_station["data"] = {"stations": [{"station_id": 123}]}
        self.assertTrue(validate_station_information(invalid_station))

        vehicle_types = {
            "version": "2.3",
            "data": {"vehicle_types": [{
                "vehicle_type_id": "1",
                "form_factor": "bicycle",
                "propulsion_type": "human",
            }]},
        }
        self.assertEqual(validate_vehicle_types(vehicle_types), [])
        invalid_vehicle = dict(vehicle_types)
        invalid_vehicle["data"] = {"vehicle_types": [{"vehicle_type_id": 1}]}
        self.assertTrue(validate_vehicle_types(invalid_vehicle))

        invalid_status = {
            "version": "2.3",
            "data": {"stations": [{
                "station_id": "123",
                "num_bikes_available": 1,
                "num_bikes_disabled": 0,
                "num_docks_available": 1,
                "num_docks_disabled": 0,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
                "vehicle_types_available": "not-an-array",
            }]},
        }
        self.assertTrue(validate_station_status_feed(invalid_status))

    def test_collection_requires_three_snapshots(self):
        with self.assertRaises(SystemExit):
            parse_args(["--output-dir", "/tmp/fixture", "--snapshots", "2"])

    def test_tracked_station_is_included_in_each_sample_first(self):
        events = [
            {"station_id": "first"},
            {"station_id": "tracked"},
        ]
        sample = _select_sample_events(events, 1, ["tracked"])
        self.assertEqual([event["station_id"] for event in sample], ["tracked"])

    def test_rejects_non_string_station_id(self):
        status = {
            "version": "2.3",
            "last_updated": 1,
            "data": {"stations": [{
                "station_id": 123,
                "num_bikes_available": 1,
                "is_installed": 1,
                "is_renting": 1,
                "is_returning": 1,
            }]},
        }
        with self.assertRaisesRegex(ValueError, "station_id"):
            normalize_station_status(status)


if __name__ == "__main__":
    unittest.main()
