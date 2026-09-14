"""Canonical source and integration contract constants."""

from __future__ import annotations

import re
from typing import Final


SOURCE_FIELDS: Final[tuple[str, ...]] = (
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
)
TIME_FIELDS: Final[tuple[str, ...]] = ("started_at", "ended_at")
COORDINATE_FIELDS: Final[tuple[str, ...]] = ("start_lat", "start_lng", "end_lat", "end_lng")
LATITUDE_FIELDS: Final[frozenset[str]] = frozenset({"start_lat", "end_lat"})
LONGITUDE_FIELDS: Final[frozenset[str]] = frozenset({"start_lng", "end_lng"})
STATION_ID_FIELDS: Final[tuple[str, ...]] = ("start_station_id", "end_station_id")
REQUIRED_STRING_FIELDS: Final[tuple[str, ...]] = (
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "member_casual",
)

RIDEABLE_TYPES: Final[frozenset[str]] = frozenset({"classic_bike", "electric_bike"})
MEMBER_TYPES: Final[frozenset[str]] = frozenset({"member", "casual"})
SOURCE_ENUMS: Final[dict[str, frozenset[str]]] = {
    "rideable_type": RIDEABLE_TYPES,
    "member_casual": MEMBER_TYPES,
}

HISTORICAL_TIME_FORMATS: Final[tuple[str, ...]] = (
    "%m/%d/%Y %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)
SPARK_TIME_PATTERNS: Final[tuple[str, ...]] = (
    "yyyy-MM-dd HH:mm:ss.SSSSSS",
    "yyyy-MM-dd HH:mm:ss.SSS",
    "yyyy-MM-dd HH:mm:ss",
    "MM/dd/yyyy HH:mm:ss.SSS",
    "MM/dd/yyyy HH:mm:ss",
)

LOCAL_TIME_ZONE: Final[str] = "America/New_York"
GBFS_VERSION: Final[str] = "2.3"
GBFS_FEEDS: Final[tuple[str, ...]] = ("station_information", "station_status", "vehicle_types")
SOURCE_MONTH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$"
)

SOURCE_COLUMN_TYPES: Final[dict[str, str]] = {
    **{field: "double" for field in COORDINATE_FIELDS},
    **{field: "string" for field in SOURCE_FIELDS if field not in COORDINATE_FIELDS},
}
