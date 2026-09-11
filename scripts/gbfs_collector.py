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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_DISCOVERY_URL = "https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json"
LOCAL_ZONE = ZoneInfo("America/New_York")
FEED_NAMES = ("station_information", "station_status", "vehicle_types")
REQUIRED_EVENT_FIELDS = (
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
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "bigdata-practicum-gbfs-collector/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"GBFS response at {url} is not a JSON object")
    return payload


def _feed_entries(discovery: Mapping[str, Any], locale: str) -> list[Mapping[str, Any]]:
    """Read both GBFS 2.x localized and older flat discovery layouts."""

    data = discovery.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("discovery feed has no object-valued data field")

    if isinstance(data.get("feeds"), list):
        entries = data["feeds"]
    else:
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
    missing = [name for name in FEED_NAMES if name not in urls]
    if missing:
        raise ValueError(f"discovery feed is missing required feeds: {', '.join(missing)}")
    return {name: urls[name] for name in FEED_NAMES}


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


def normalize_station_status(
    status_feed: Mapping[str, Any],
    ingested_at: datetime | None = None,
    source_version: str | None = None,
) -> list[dict[str, Any]]:
    """Map provider station records to ``station_status_event_v1``."""

    provider_updated = status_feed.get("last_updated")
    snapshot_at = posix_to_utc(provider_updated)
    ingested_at = ingested_at or utc_now()
    version = source_version or str(status_feed.get("version", ""))
    if not version:
        raise ValueError("GBFS status feed has no version")

    data = status_feed.get("data")
    stations = data.get("stations") if isinstance(data, Mapping) else None
    if not isinstance(stations, list):
        raise ValueError("station_status feed has no data.stations array")

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
    for field in REQUIRED_EVENT_FIELDS:
        if field not in event or event[field] is None:
            errors.append(f"missing required field: {field}")
    if "station_id" in event and not isinstance(event["station_id"], str):
        errors.append("station_id must be a string")
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
    if event.get("source_version") != "2.3":
        errors.append("source_version must be '2.3'")
    for field in (
        "snapshot_at_utc",
        "snapshot_at_local",
        "last_reported_at_utc",
        "ingested_at_utc",
    ):
        value = event.get(field)
        if value is not None:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                errors.append(f"{field} must be an RFC 3339 timestamp")
    return errors


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def collect_snapshot(
    discovery_url: str,
    output_dir: Path,
    locale: str,
    sample_size: int,
    timeout: float,
    fetcher: Callable[[str, float], dict[str, Any]] = fetch_json,
) -> dict[str, Any]:
    discovery = fetcher(discovery_url, timeout)
    feed_urls = discover_feed_urls(discovery, locale)
    fetched = {name: fetcher(url, timeout) for name, url in feed_urls.items()}
    ingested_at = utc_now()
    stamp = ingested_at.strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = output_dir / "snapshots" / stamp
    for name, payload in fetched.items():
        _write_json(snapshot_dir / f"{name}.json", payload)

    events = normalize_station_status(fetched["station_status"], ingested_at)
    for event in events:
        errors = validate_event(event)
        if errors:
            raise ValueError(f"normalized event {event.get('station_id')}: {errors}")
    _write_json(snapshot_dir / "station_status_event_v1.sample.json", events[:sample_size])
    record = {
        "snapshot_directory": str(snapshot_dir.relative_to(output_dir)),
        "provider_last_updated": fetched["station_status"].get("last_updated"),
        "ingested_at_utc": format_timestamp(ingested_at),
        "station_count": len(events),
        "sample_station_ids": [event["station_id"] for event in events[:sample_size]],
        "sample_station_status": [
            {
                "station_id": event["station_id"],
                "num_bikes_available": event["num_bikes_available"],
                "num_docks_available": event["num_docks_available"],
                "is_installed": event["is_installed"],
                "is_renting": event["is_renting"],
                "is_returning": event["is_returning"],
            }
            for event in events[:sample_size]
        ],
        "source_version": str(fetched["station_status"].get("version", "")),
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
        )
        records.append(result["record"])
        if index + 1 < args.snapshots:
            time.sleep(args.interval_seconds)
    _write_json(output_dir / "collection_log.json", {
        "discovery_url": args.discovery_url,
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
    parser.add_argument("--timeout", type=float, default=30)
    return parser


if __name__ == "__main__":
    try:
        run(build_parser().parse_args())
    except (OSError, ValueError) as error:
        print(f"gbfs collector: {error}", file=sys.stderr)
        raise SystemExit(1)
