#!/usr/bin/env python3
"""Collect Citi Bike GBFS feeds and normalize station status snapshots.

The collector intentionally keeps the provider schema at the edge.  Downstream
code should consume ``station_status_event_v1`` records produced here instead
of depending on GBFS field names directly.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .contracts import GBFS_FEEDS, GBFS_VERSION, LOCAL_TIME_ZONE

DEFAULT_DISCOVERY_URL = f"https://gbfs.citibikenyc.com/gbfs/{GBFS_VERSION}/gbfs.json"
LOCAL_ZONE = ZoneInfo(LOCAL_TIME_ZONE)
EVENT_FIELDS = (
    "station_id",
    "snapshot_at_utc",
    "snapshot_at_local",
    "num_bikes_available",
    "num_bikes_disabled",
    "num_docks_available",
    "num_docks_disabled",
    "is_installed",
    "is_renting",
    "is_returning",
    "last_reported_at_utc",
    "ingested_at_utc",
    "source_version",
)
NON_NULL_EVENT_FIELDS = (
    "station_id",
    "snapshot_at_utc",
    "snapshot_at_local",
    "num_bikes_available",
    "is_installed",
    "is_renting",
    "is_returning",
    "ingested_at_utc",
    "source_version",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Format an aware datetime as an RFC 3339 timestamp."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def posix_to_utc(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"POSIX timestamp must be numeric, got {value!r}")
    return datetime.fromtimestamp(value, tz=timezone.utc)


def fetch_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    payload, _ = fetch_json_bytes(url, timeout)
    return payload


def fetch_json_bytes(url: str, timeout: float = 30.0) -> tuple[dict[str, Any], bytes]:
    """读取 JSON，同时保留用于 snapshot_id 的原始响应字节。"""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "bigdata-practicum-gbfs-collector/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"GBFS response at {url} is not a JSON object")
    return payload, raw


def _feed_entries(discovery: Mapping[str, Any], locale: str) -> list[Mapping[str, Any]]:
    """读取 GBFS 2.3 的本地化 feed 列表。"""

    data = discovery.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("discovery feed has no object-valued data field")

    localized = data.get(locale)
    if not isinstance(localized, Mapping) or not isinstance(
        localized.get("feeds"), list
    ):
        raise ValueError(f"discovery feed has no feeds for locale {locale!r}")
    entries = localized["feeds"]

    if not all(isinstance(entry, Mapping) for entry in entries):
        raise ValueError("discovery feed contains a malformed feed entry")
    return list(entries)


def discover_feed_urls(
    discovery: Mapping[str, Any], locale: str = "en"
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for entry in _feed_entries(discovery, locale):
        name, url = entry.get("name"), entry.get("url")
        if isinstance(name, str) and isinstance(url, str):
            urls[name] = url
    missing = [name for name in GBFS_FEEDS if name not in urls]
    if missing:
        raise ValueError(f"discovery feed is missing required feeds: {', '.join(missing)}")
    return {name: urls[name] for name in GBFS_FEEDS}


def _nullable_int(record: Mapping[str, Any], field: str) -> int | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer or null")
    return value


def _required_int(record: Mapping[str, Any], field: str) -> int:
    value = _nullable_int(record, field)
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def _gbfs_bool(record: Mapping[str, Any], field: str) -> bool:
    value = record.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be a boolean or 0/1 integer")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _feed_rows(feed: Mapping[str, Any], key: str, feed_name: str) -> list[Mapping[str, Any]]:
    data = feed.get("data")
    rows = data.get(key) if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError(f"{feed_name} feed has no data.{key} array")
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError(f"{feed_name} feed contains a non-object record")
    return list(rows)


def _validate_version(feed: Mapping[str, Any], feed_name: str) -> list[str]:
    if feed.get("version") != GBFS_VERSION:
        return [f"{feed_name}.version must be {GBFS_VERSION!r}"]
    return []


def validate_station_information(feed: Mapping[str, Any]) -> list[str]:
    errors = _validate_version(feed, "station_information")
    try:
        stations = _feed_rows(feed, "stations", "station_information")
    except ValueError as error:
        return errors + [str(error)]
    for index, station in enumerate(stations):
        prefix = f"station_information.data.stations[{index}]"
        if not isinstance(station.get("station_id"), str) or not station["station_id"]:
            errors.append(f"{prefix}.station_id must be a non-empty string")
        if not isinstance(station.get("name"), str):
            errors.append(f"{prefix}.name must be a string")
        for field in ("lat", "lon"):
            if not _is_number(station.get(field)):
                errors.append(f"{prefix}.{field} must be numeric")
        # Optional metadata fields are retained in raw and normalised to null
        # with a quality warning by gbfs_stream when their provider value is
        # malformed.
    return errors


def validate_station_status_feed(feed: Mapping[str, Any]) -> list[str]:
    errors = _validate_version(feed, "station_status")
    try:
        stations = _feed_rows(feed, "stations", "station_status")
    except ValueError as error:
        return errors + [str(error)]
    required = (
        "station_id",
        "num_bikes_available",
        "is_installed",
        "is_renting",
        "is_returning",
        "last_reported",
        "vehicle_types_available",
    )
    for index, station in enumerate(stations):
        prefix = f"station_status.data.stations[{index}]"
        for field in required:
            if field not in station:
                errors.append(f"{prefix}.{field} is missing")
        if "station_id" in station and (
            not isinstance(station["station_id"], str) or not station["station_id"]
        ):
            errors.append(f"{prefix}.station_id must be a non-empty string")
        if station.get("num_bikes_available") is None:
            errors.append(f"{prefix}.num_bikes_available is required")
        for field in (
            "num_bikes_available",
            "num_bikes_disabled",
            "num_docks_available",
            "num_docks_disabled",
        ):
            if field in station:
                try:
                    _nullable_int(station, field)
                except ValueError as error:
                    errors.append(f"{prefix}: {error}")
        for field in ("is_installed", "is_renting", "is_returning"):
            if field in station:
                try:
                    _gbfs_bool(station, field)
                except ValueError as error:
                    errors.append(f"{prefix}: {error}")
        if "last_reported" in station:
            try:
                posix_to_utc(station["last_reported"])
            except ValueError as error:
                errors.append(f"{prefix}: {error}")
        vehicle_types = station.get("vehicle_types_available")
        if not isinstance(vehicle_types, list):
            errors.append(f"{prefix}.vehicle_types_available must be an array")
        else:
            for vehicle_index, vehicle in enumerate(vehicle_types):
                vehicle_prefix = f"{prefix}.vehicle_types_available[{vehicle_index}]"
                if not isinstance(vehicle, Mapping):
                    errors.append(f"{vehicle_prefix} must be an object")
                    continue
                if not isinstance(vehicle.get("vehicle_type_id"), str):
                    errors.append(f"{vehicle_prefix}.vehicle_type_id must be a string")
                if isinstance(vehicle.get("count"), bool) or not isinstance(
                    vehicle.get("count"), int
                ):
                    errors.append(f"{vehicle_prefix}.count must be an integer")
                elif vehicle["count"] < 0:
                    errors.append(f"{vehicle_prefix}.count must not be negative")
    return errors


def validate_vehicle_types(feed: Mapping[str, Any]) -> list[str]:
    errors = _validate_version(feed, "vehicle_types")
    try:
        vehicle_types = _feed_rows(feed, "vehicle_types", "vehicle_types")
    except ValueError as error:
        return errors + [str(error)]
    for index, vehicle in enumerate(vehicle_types):
        prefix = f"vehicle_types.data.vehicle_types[{index}]"
        for field in ("vehicle_type_id", "form_factor", "propulsion_type"):
            if not isinstance(vehicle.get(field), str) or not vehicle[field]:
                errors.append(f"{prefix}.{field} must be a non-empty string")
        if "name" in vehicle and vehicle["name"] is not None and not isinstance(
            vehicle["name"], str
        ):
            errors.append(f"{prefix}.name must be a string or null")
        if "max_range_meters" in vehicle and vehicle["max_range_meters"] is not None and not _is_number(
            vehicle["max_range_meters"]
        ):
            errors.append(f"{prefix}.max_range_meters must be numeric or null")
    return errors


def validate_target_feeds(feeds: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    validators = {
        "station_information": validate_station_information,
        "station_status": validate_station_status_feed,
        "vehicle_types": validate_vehicle_types,
    }
    return {
        name: errors
        for name, validator in validators.items()
        if (errors := validator(feeds[name]))
    }


def station_status_quality_warnings(feed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """记录 provider 快照中可追踪但不阻断标准化的质量异常。"""

    warnings: list[dict[str, Any]] = []
    stations = _feed_rows(feed, "stations", "station_status")
    for station in stations:
        vehicle_types = station.get("vehicle_types_available")
        if not isinstance(vehicle_types, list):
            continue
        vehicle_total = sum(
            vehicle["count"]
            for vehicle in vehicle_types
            if isinstance(vehicle, Mapping) and isinstance(vehicle.get("count"), int)
        )
        bikes_available = station.get("num_bikes_available")
        if isinstance(bikes_available, int) and vehicle_total != bikes_available:
            warnings.append({
                "type": "vehicle_count_mismatch",
                "station_id": station.get("station_id"),
                "num_bikes_available": bikes_available,
                "vehicle_types_available_total": vehicle_total,
                "resolution": "num_bikes_available 是 station_status_event_v1 的权威总数",
            })
        for field in (
            "num_bikes_available",
            "num_bikes_disabled",
            "num_docks_available",
            "num_docks_disabled",
        ):
            value = station.get(field)
            if isinstance(value, int) and value < 0:
                warnings.append({
                    "type": "negative_inventory",
                    "station_id": station.get("station_id"),
                    "field": field,
                    "value": value,
                    "resolution": "保留原值，交由下游标记 INVALID_DATA",
                })
    return warnings


def normalize_station_status(
    status_feed: Mapping[str, Any],
    ingested_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Map provider station records to ``station_status_event_v1``."""

    schema_errors = validate_station_status_feed(status_feed)
    if schema_errors:
        raise ValueError("; ".join(schema_errors))
    provider_updated = status_feed.get("last_updated")
    snapshot_at = posix_to_utc(provider_updated)
    ingested_at = ingested_at or utc_now()
    version = str(status_feed["version"])

    data = status_feed.get("data")
    stations = data["stations"]

    snapshot_utc = format_timestamp(snapshot_at)
    snapshot_local = format_timestamp(snapshot_at.astimezone(LOCAL_ZONE))
    ingested_utc = format_timestamp(ingested_at.astimezone(timezone.utc))
    events: list[dict[str, Any]] = []
    for station in stations:
        if not isinstance(station, Mapping):
            raise ValueError("station_status contains a non-object station")
        station_id = station.get("station_id")
        if not isinstance(station_id, str) or not station_id:
            raise ValueError("station_id must be a non-empty string")
        last_reported = station.get("last_reported")
        event = {
            "station_id": station_id,
            "snapshot_at_utc": snapshot_utc,
            "snapshot_at_local": snapshot_local,
            "num_bikes_available": _required_int(station, "num_bikes_available"),
            "num_bikes_disabled": _nullable_int(station, "num_bikes_disabled"),
            "num_docks_available": _nullable_int(station, "num_docks_available"),
            "num_docks_disabled": _nullable_int(station, "num_docks_disabled"),
            "is_installed": _gbfs_bool(station, "is_installed"),
            "is_renting": _gbfs_bool(station, "is_renting"),
            "is_returning": _gbfs_bool(station, "is_returning"),
            "last_reported_at_utc": (
                format_timestamp(posix_to_utc(last_reported))
                if last_reported is not None
                else None
            ),
            "ingested_at_utc": ingested_utc,
            "source_version": version,
        }
        events.append(event)
    return events


def validate_event(event: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in EVENT_FIELDS:
        if field not in event:
            errors.append(f"missing field: {field}")
    for field in NON_NULL_EVENT_FIELDS:
        if field in event and event[field] is None:
            errors.append(f"missing required field: {field}")
    if "station_id" in event and (
        not isinstance(event["station_id"], str) or not event["station_id"]
    ):
        errors.append("station_id must be a non-empty string")
    for field in ("num_bikes_available",):
        if field in event and (
            isinstance(event[field], bool) or not isinstance(event[field], int)
        ):
            errors.append(f"{field} must be an integer")
    for field in (
        "num_bikes_disabled",
        "num_docks_available",
        "num_docks_disabled",
    ):
        if field in event and event[field] is not None and (
            isinstance(event[field], bool) or not isinstance(event[field], int)
        ):
            errors.append(f"{field} must be an integer or null")
    for field in ("is_installed", "is_renting", "is_returning"):
        if field in event and not isinstance(event[field], bool):
            errors.append(f"{field} must be boolean")
    if event.get("source_version") != GBFS_VERSION:
        errors.append(f"source_version must be {GBFS_VERSION!r}")
    parsed_times: dict[str, datetime] = {}
    for field in (
        "snapshot_at_utc",
        "snapshot_at_local",
        "last_reported_at_utc",
        "ingested_at_utc",
    ):
        value = event.get(field)
        if value is None:
            continue
        try:
            if not isinstance(value, str):
                raise ValueError("timestamp must be a string")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp must include a timezone")
            parsed_times[field] = parsed
        except ValueError as error:
            errors.append(f"{field}: {error}")
    for field in ("snapshot_at_utc", "last_reported_at_utc", "ingested_at_utc"):
        parsed = parsed_times.get(field)
        if parsed is not None and parsed.utcoffset() != timedelta(0):
            errors.append(f"{field} must use UTC")
    snapshot_utc = parsed_times.get("snapshot_at_utc")
    snapshot_local = parsed_times.get("snapshot_at_local")
    if snapshot_utc is not None and snapshot_local is not None:
        expected_local = snapshot_utc.astimezone(LOCAL_ZONE)
        if (
            snapshot_local.utcoffset() != expected_local.utcoffset()
            or snapshot_local.replace(tzinfo=None)
            != expected_local.replace(tzinfo=None)
        ):
            errors.append("snapshot_at_local must equal snapshot_at_utc in America/New_York")
    return errors


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _select_sample_events(
    events: list[dict[str, Any]], sample_size: int, tracked_station_ids: list[str]
) -> list[dict[str, Any]]:
    by_station_id = {event["station_id"]: event for event in events}
    selected: list[dict[str, Any]] = []
    for station_id in tracked_station_ids:
        event = by_station_id.get(station_id)
        if event is not None and event not in selected:
            selected.append(event)
    for event in events:
        if len(selected) >= sample_size:
            break
        if event not in selected:
            selected.append(event)
    return selected[:sample_size]


def collect_snapshot(
    discovery_url: str,
    output_dir: Path,
    locale: str,
    sample_size: int,
    timeout: float,
    tracked_station_ids: list[str] | None = None,
    fetcher: Callable[[str, float], dict[str, Any]] = fetch_json,
) -> dict[str, Any]:
    discovery = fetcher(discovery_url, timeout)
    feed_urls = discover_feed_urls(discovery, locale)
    fetched = {name: fetcher(url, timeout) for name, url in feed_urls.items()}
    feed_errors = validate_target_feeds(fetched)
    if feed_errors:
        raise ValueError(f"GBFS feed validation failed: {feed_errors}")
    ingested_at = utc_now()
    stamp = ingested_at.strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_dir = output_dir / "snapshots" / stamp
    for name, payload in fetched.items():
        _write_json(snapshot_dir / f"{name}.json", payload)

    events = normalize_station_status(fetched["station_status"], ingested_at)
    for event in events:
        errors = validate_event(event)
        if errors:
            raise ValueError(f"normalized event {event.get('station_id')}: {errors}")
    sample_events = _select_sample_events(events, sample_size, tracked_station_ids or [])
    _write_json(snapshot_dir / "station_status_event_v1.sample.json", sample_events)
    record = {
        "snapshot_directory": str(snapshot_dir.relative_to(output_dir)),
        "provider_last_updated": fetched["station_status"].get("last_updated"),
        "ingested_at_utc": format_timestamp(ingested_at),
        "station_count": len(events),
        "sample_station_ids": [event["station_id"] for event in sample_events],
        "sample_station_status": [
            {
                "station_id": event["station_id"],
                "num_bikes_available": event["num_bikes_available"],
                "num_bikes_disabled": event["num_bikes_disabled"],
                "num_docks_available": event["num_docks_available"],
                "num_docks_disabled": event["num_docks_disabled"],
                "is_installed": event["is_installed"],
                "is_renting": event["is_renting"],
                "is_returning": event["is_returning"],
                "last_reported_at_utc": event["last_reported_at_utc"],
            }
            for event in sample_events
        ],
        "source_version": str(fetched["station_status"].get("version", "")),
        "quality_warnings": station_status_quality_warnings(fetched["station_status"]),
    }
    return {"record": record, "discovery": discovery, "feed_urls": feed_urls}


def run(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    first = fetch_json(args.discovery_url, args.timeout)
    feed_urls = discover_feed_urls(first, args.locale)
    _write_json(output_dir / "discovery.json", first)
    _write_json(output_dir / "feed_manifest.json", {
        "discovery_url": args.discovery_url,
        "locale": args.locale,
        "source_version": str(first.get("version", "")),
        "feeds": feed_urls,
    })

    records: list[dict[str, Any]] = []
    for index in range(args.snapshots):
        result = collect_snapshot(
            args.discovery_url,
            output_dir,
            args.locale,
            args.sample_size,
            args.timeout,
            args.sample_station_ids,
        )
        records.append(result["record"])
        if index + 1 < args.snapshots:
            time.sleep(args.interval_seconds)
    _write_json(output_dir / "collection_log.json", {
        "discovery_url": args.discovery_url,
        "tracked_station_ids": args.sample_station_ids,
        "source_version": str(first.get("version", "")),
        "snapshots": records,
    })
    print(json.dumps({"output_dir": str(output_dir), "snapshots": records}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-url", default=DEFAULT_DISCOVERY_URL)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--locale", default="en")
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument(
        "--sample-station-id",
        dest="sample_station_ids",
        action="append",
        default=[],
        help="station ID to include first in every sample (repeatable)",
    )
    parser.add_argument("--timeout", type=float, default=30)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if args.snapshots < 3:
        raise SystemExit("--snapshots must be at least 3")
    if args.sample_size < 1:
        raise SystemExit("--sample-size must be at least 1")
    if args.interval_seconds < 0:
        raise SystemExit("--interval-seconds cannot be negative")
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except (OSError, ValueError) as error:
        print(f"gbfs collector: {error}", file=sys.stderr)
        raise SystemExit(1)
