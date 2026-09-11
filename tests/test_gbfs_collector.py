import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from gbfs_collector import (  # noqa: E402
    discover_feed_urls,
    normalize_station_status,
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
