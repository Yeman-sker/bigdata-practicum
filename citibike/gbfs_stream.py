#!/usr/bin/env python3
"""生成 canonical GBFS 批次并顺序发送到 Kafka。

该入口把 provider UUID 与内部 canonical station_id 隔离开，先原子保存
raw/metadata，再发送 station 记录，最后才发送 snapshot_end。Kafka 发送使用
仓库运行手册中已有的官方 console producer，因此测试可以注入假的 producer，
真实环境仍然走 Kafka broker，而不是内存替身。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .contracts import GBFS_FEEDS, GBFS_VERSION
from .gbfs import (
    DEFAULT_DISCOVERY_URL,
    _feed_rows,
    discover_feed_urls,
    fetch_json_bytes,
    format_timestamp,
    normalize_station_status,
    posix_to_utc,
    station_status_quality_warnings,
    validate_event,
    validate_target_feeds,
)

TOPIC = "bike.station.status.v1"
CONTRACT_VERSION = "1.1"
ORIGINS = frozenset({"GBFS_LIVE", "GBFS_REPLAY", "FIXTURE"})
ID_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
MAX_STATION_ID_LENGTH = 128
MAX_STATIONS = 5_000


class Producer(Protocol):
    def send_records(self, records: list[dict[str, Any]]) -> None: ...


class StreamError(ValueError):
    """输入、映射或批次契约错误。"""


class CollectionError(StreamError):
    """采集阶段失败，同时携带已取得的 raw 证据。"""

    def __init__(
        self,
        message: str,
        raw: Mapping[str, bytes],
        feed_urls: Mapping[str, str],
    ) -> None:
        super().__init__(message)
        self.raw = dict(raw)
        self.feed_urls = dict(feed_urls)


class KafkaSendError(RuntimeError):
    """Kafka producer 未能确认一组记录。"""


@dataclass(frozen=True)
class Batch:
    metadata: list[dict[str, Any]]
    metadata_bytes: bytes
    metadata_version: str
    events: list[dict[str, Any]]
    records: list[dict[str, Any]]
    snapshot_id: str
    snapshot_at_utc: str
    ingested_at_utc: str
    station_count: int
    provider_last_updated: Any
    mapping_warnings: list[dict[str, Any]]
    quality_warnings: list[dict[str, Any]]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def metadata_version(metadata_bytes: bytes) -> str:
    return hashlib.sha256(metadata_bytes).hexdigest()


def snapshot_id(raw_status_bytes: bytes, metadata_version_value: str) -> str:
    if not ID_RE.fullmatch(metadata_version_value):
        raise StreamError("metadata_version must be a lowercase SHA-256 hex digest")
    return hashlib.sha256(
        raw_status_bytes + b"\n" + metadata_version_value.encode("ascii")
    ).hexdigest()


def _required_provider_id(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise StreamError(f"{context}.station_id must be a non-empty trimmed string")
    if len(value) > MAX_STATION_ID_LENGTH or any(char.isspace() for char in value):
        raise StreamError(
            f"{context}.station_id must be at most "
            f"{MAX_STATION_ID_LENGTH} non-whitespace characters"
        )
    return value


def _canonical_id(provider_id: str) -> str:
    value = f"gbfs:{provider_id}"
    if len(value) > MAX_STATION_ID_LENGTH:
        raise StreamError(
            "isolated station_id is longer than "
            f"{MAX_STATION_ID_LENGTH} characters: {provider_id}"
        )
    return value


def _validate_station_id(value: Any) -> None:
    """校验 replay 中的 canonical station_id，避免绕过映射边界。"""

    if not isinstance(value, str) or not value:
        raise StreamError("station_id must be a non-empty string")
    if len(value) > MAX_STATION_ID_LENGTH or any(char.isspace() for char in value):
        raise StreamError(
            "station_id must be at most "
            f"{MAX_STATION_ID_LENGTH} non-whitespace characters"
        )


def _validate_utc_timestamp(value: Any, field: str) -> None:
    """校验契约要求的 RFC 3339 UTC 时间。"""

    if not isinstance(value, str):
        raise StreamError(f"{field} must be a timestamp string")
    if not UTC_TIMESTAMP_RE.fullmatch(value):
        raise StreamError(f"{field} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StreamError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise StreamError(f"{field} must use UTC")


def _metadata_time(feed: Mapping[str, Any], fallback: datetime | None = None) -> str:
    value = feed.get("last_updated")
    if value is None and fallback is not None:
        return format_timestamp(fallback.astimezone(timezone.utc))
    try:
        return format_timestamp(posix_to_utc(value))
    except ValueError as error:
        raise StreamError(f"station_information.last_updated: {error}") from error


def _safe_station_fields(
    station: Mapping[str, Any], provider_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    name = station.get("name")
    station_name = name.strip() if isinstance(name, str) and name.strip() else None

    lat = station.get("lat")
    if (
        not isinstance(lat, (int, float))
        or isinstance(lat, bool)
        or not -90 <= lat <= 90
    ):
        if lat is not None:
            warnings.append(
                {
                    "type": "invalid_coordinate",
                    "station_id": provider_id,
                    "field": "lat",
                }
            )
        lat = None
    lon = station.get("lon")
    if (
        not isinstance(lon, (int, float))
        or isinstance(lon, bool)
        or not -180 <= lon <= 180
    ):
        if lon is not None:
            warnings.append(
                {
                    "type": "invalid_coordinate",
                    "station_id": provider_id,
                    "field": "lon",
                }
            )
        lon = None

    capacity = station.get("capacity")
    if capacity is not None and (
        isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0
    ):
        warnings.append({"type": "invalid_capacity", "station_id": provider_id})
        capacity = None
    region_id = station.get("region_id")
    if region_id is not None and not isinstance(region_id, str):
        warnings.append({"type": "invalid_region_id", "station_id": provider_id})
        region_id = None
    elif isinstance(region_id, str):
        region_id = region_id.strip() or None
    return {
        "station_name": station_name,
        "lat": lat,
        "lon": lon,
        "capacity": capacity,
        "region_id": region_id,
    }, warnings


def build_canonical_metadata(
    station_information: Mapping[str, Any],
    station_status: Mapping[str, Any] | None = None,
    snapshot_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """构造 canonical metadata、provider 映射和可观察质量告警。"""

    if station_information.get("version") != GBFS_VERSION:
        raise StreamError(f"station_information.version must be {GBFS_VERSION!r}")
    info_rows = _feed_rows(station_information, "stations", "station_information")
    seen_provider: set[str] = set()
    provider_rows: list[tuple[str, Mapping[str, Any]]] = []
    for index, station in enumerate(info_rows):
        provider_id = _required_provider_id(
            station.get("station_id"), f"station_information.data.stations[{index}]"
        )
        if provider_id in seen_provider:
            raise StreamError(
                f"duplicate provider station_id in metadata: {provider_id}"
            )
        seen_provider.add(provider_id)
        provider_rows.append((provider_id, station))

    short_name_owners: dict[str, list[str]] = {}
    for provider_id, station in provider_rows:
        short_name = station.get("short_name")
        if isinstance(short_name, str) and short_name.strip():
            short_name_owners.setdefault(short_name.strip(), []).append(provider_id)

    metadata_time = _metadata_time(station_information)
    metadata: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    for provider_id, station in provider_rows:
        short_name = station.get("short_name")
        normalized_short_name = (
            short_name.strip() if isinstance(short_name, str) else ""
        )
        owners = short_name_owners.get(normalized_short_name, [])
        if not normalized_short_name:
            canonical = _canonical_id(provider_id)
            status, reason = "ISOLATED", "MISSING_SHORT_NAME"
            warnings.append(
                {
                    "type": "isolated_mapping",
                    "provider_station_id": provider_id,
                    "mapping_reason": reason,
                }
            )
        elif len(owners) != 1:
            canonical = _canonical_id(provider_id)
            status, reason = "ISOLATED", "DUPLICATE_SHORT_NAME"
            warnings.append(
                {
                    "type": "isolated_mapping",
                    "provider_station_id": provider_id,
                    "mapping_reason": reason,
                }
            )
        else:
            canonical = normalized_short_name
            if len(canonical) > 128 or any(char.isspace() for char in canonical):
                raise StreamError(
                    f"station_information short_name is an invalid station ID: {canonical!r}"
                )
            else:
                status, reason = "SHORT_NAME", None
        fields, field_warnings = _safe_station_fields(station, provider_id)
        warnings.extend(field_warnings)
        mapping[provider_id] = canonical
        metadata.append(
            {
                "provider_station_id": provider_id,
                "station_id": canonical,
                "mapping_status": status,
                "mapping_reason": reason,
                **fields,
                "is_current": True,
                "metadata_source": "GBFS",
                "metadata_updated_at": metadata_time,
            }
        )

    if station_status is not None:
        status_rows = _feed_rows(station_status, "stations", "station_status")
        status_seen: set[str] = set()
        for index, station in enumerate(status_rows):
            provider_id = _required_provider_id(
                station.get("station_id"), f"station_status.data.stations[{index}]"
            )
            if provider_id in status_seen:
                raise StreamError(
                    f"duplicate provider station_id in status: {provider_id}"
                )
            status_seen.add(provider_id)
            if provider_id in mapping:
                continue
            canonical = _canonical_id(provider_id)
            mapping[provider_id] = canonical
            metadata.append(
                {
                    "provider_station_id": provider_id,
                    "station_id": canonical,
                    "mapping_status": "ISOLATED",
                    "mapping_reason": "MISSING_METADATA",
                    "station_name": None,
                    "lat": None,
                    "lon": None,
                    "capacity": None,
                    "region_id": None,
                    "is_current": True,
                    "metadata_source": "GBFS",
                    "metadata_updated_at": _metadata_time(station_status, snapshot_at),
                }
            )
            warnings.append(
                {"type": "missing_metadata", "provider_station_id": provider_id}
            )

    metadata.sort(key=lambda row: row["station_id"])
    if len({row["station_id"] for row in metadata}) != len(metadata):
        raise StreamError("canonical station_id collision in metadata")
    return metadata, mapping, warnings


def _canonical_events(
    status_feed: Mapping[str, Any], mapping: Mapping[str, str], ingested_at: datetime
) -> list[dict[str, Any]]:
    events = normalize_station_status(status_feed, ingested_at)
    seen: set[str] = set()
    for event in events:
        provider_id = event["station_id"]
        canonical = mapping.get(provider_id)
        if canonical is None:
            raise StreamError(f"station status has no canonical mapping: {provider_id}")
        event["station_id"] = canonical
        if canonical in seen:
            raise StreamError(f"duplicate canonical station_id in status: {canonical}")
        seen.add(canonical)
        errors = validate_event(event)
        if errors:
            raise StreamError(f"normalized event {canonical}: {errors}")
    return events


def _record(
    event: Mapping[str, Any], snapshot: str, metadata: str, origin: str
) -> dict[str, Any]:
    if origin not in ORIGINS:
        raise StreamError(f"unsupported data_origin: {origin}")
    station_id = event.get("station_id")
    return {
        "key": station_id,
        "headers": {
            "contract_version": CONTRACT_VERSION,
            "record_type": "station",
            "snapshot_id": snapshot,
            "metadata_version": metadata,
            "data_origin": origin,
        },
        "value": dict(event),
    }


def build_batch(
    station_information: Mapping[str, Any],
    station_status: Mapping[str, Any],
    raw_status_bytes: bytes,
    ingested_at: datetime,
    data_origin: str = "GBFS_LIVE",
) -> Batch:
    if data_origin not in ORIGINS:
        raise StreamError(f"unsupported data_origin: {data_origin}")
    metadata, mapping, mapping_warnings = build_canonical_metadata(
        station_information, station_status, ingested_at
    )
    metadata_bytes = _json_bytes(metadata)
    metadata_hash = metadata_version(metadata_bytes)
    events = _canonical_events(station_status, mapping, ingested_at)
    if len(events) > MAX_STATIONS:
        raise StreamError(f"batch exceeds {MAX_STATIONS} stations")
    snapshot_at = posix_to_utc(station_status.get("last_updated"))
    snapshot_at_value = format_timestamp(snapshot_at)
    ingested_value = format_timestamp(ingested_at.astimezone(timezone.utc))
    snapshot_hash = snapshot_id(raw_status_bytes, metadata_hash)
    records = [
        _record(event, snapshot_hash, metadata_hash, data_origin) for event in events
    ]
    records.append(
        {
            "key": "__snapshot_end__",
            "headers": {
                "contract_version": CONTRACT_VERSION,
                "record_type": "snapshot_end",
                "snapshot_id": snapshot_hash,
                "metadata_version": metadata_hash,
                "data_origin": data_origin,
            },
            "value": {
                "station_count": len(events),
                "snapshot_at_utc": snapshot_at_value,
                "ingested_at_utc": ingested_value,
            },
        }
    )
    return Batch(
        metadata=metadata,
        metadata_bytes=metadata_bytes,
        metadata_version=metadata_hash,
        events=events,
        records=records,
        snapshot_id=snapshot_hash,
        snapshot_at_utc=snapshot_at_value,
        ingested_at_utc=ingested_value,
        station_count=len(events),
        provider_last_updated=station_status.get("last_updated"),
        mapping_warnings=mapping_warnings,
        quality_warnings=station_status_quality_warnings(station_status),
    )


def _validate_record(record: Mapping[str, Any]) -> None:
    if not isinstance(record.get("key"), str):
        raise StreamError("Kafka record key must be a string")
    headers = record.get("headers")
    value = record.get("value")
    if not isinstance(headers, Mapping) or not isinstance(value, Mapping):
        raise StreamError("Kafka record headers and value must be objects")
    required = (
        "contract_version",
        "record_type",
        "snapshot_id",
        "metadata_version",
        "data_origin",
    )
    missing = [field for field in required if not isinstance(headers.get(field), str)]
    if missing:
        raise StreamError(f"Kafka record missing string headers: {missing}")
    if headers["contract_version"] != CONTRACT_VERSION:
        raise StreamError("unsupported contract_version")
    if not ID_RE.fullmatch(headers["snapshot_id"]) or not ID_RE.fullmatch(
        headers["metadata_version"]
    ):
        raise StreamError(
            "snapshot_id and metadata_version must be lowercase SHA-256 hex"
        )
    if headers["data_origin"] not in ORIGINS:
        raise StreamError("unsupported data_origin")
    if headers["record_type"] == "station":
        _validate_station_id(value.get("station_id"))
        if record["key"] != value.get("station_id"):
            raise StreamError("station Kafka key must equal canonical station_id")
        errors = validate_event(value)
        if errors:
            raise StreamError(f"invalid station event: {errors}")
    elif headers["record_type"] == "snapshot_end":
        if record["key"] != "__snapshot_end__":
            raise StreamError("snapshot_end key must be __snapshot_end__")
        if (
            isinstance(value.get("station_count"), bool)
            or not isinstance(value.get("station_count"), int)
            or value["station_count"] < 0
        ):
            raise StreamError(
                "snapshot_end.station_count must be a non-negative integer"
            )
        for field in ("snapshot_at_utc", "ingested_at_utc"):
            _validate_utc_timestamp(value.get(field), f"snapshot_end.{field}")
    else:
        raise StreamError(f"unsupported Kafka record_type: {headers['record_type']}")


def validate_batch_records(records: list[dict[str, Any]]) -> None:
    if not records:
        raise StreamError("replay contains no Kafka records")
    if len(records) > MAX_STATIONS + 1:
        raise StreamError(f"batch exceeds {MAX_STATIONS} stations")
    for record in records:
        _validate_record(record)
    first_headers = records[0]["headers"]
    for record in records:
        headers = record["headers"]
        for field in (
            "contract_version",
            "snapshot_id",
            "metadata_version",
            "data_origin",
        ):
            if headers[field] != first_headers[field]:
                raise StreamError(f"batch header {field} is inconsistent")
    ends = [
        record
        for record in records
        if record["headers"]["record_type"] == "snapshot_end"
    ]
    if len(ends) != 1 or records[-1] is not ends[0]:
        raise StreamError("batch must contain exactly one final snapshot_end")
    stations = records[:-1]
    station_ids = [record["key"] for record in stations]
    if len(set(station_ids)) != len(station_ids):
        raise StreamError("batch contains duplicate canonical station IDs")
    end = ends[0]
    if end["value"]["station_count"] != len(stations):
        raise StreamError("snapshot_end.station_count does not match station records")
    if stations:
        first_station = stations[0]["value"]
        if end["value"]["snapshot_at_utc"] != first_station["snapshot_at_utc"]:
            raise StreamError(
                "snapshot_end.snapshot_at_utc does not match station records"
            )
        if end["value"]["ingested_at_utc"] != first_station["ingested_at_utc"]:
            raise StreamError(
                "snapshot_end.ingested_at_utc does not match station records"
            )


def replay_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise StreamError(f"cannot read replay file: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise StreamError(
                f"replay line {line_number} is not JSON: {error}"
            ) from error
        if not isinstance(value, dict):
            raise StreamError(f"replay line {line_number} must be an object")
        records.append(value)
    validate_batch_records(records)
    origin = records[0]["headers"]["data_origin"]
    if origin == "GBFS_LIVE":
        raise StreamError("GBFS_LIVE records cannot be used as replay input")
    replayed = []
    for record in records:
        copied = {
            "key": record["key"],
            "headers": dict(record["headers"]),
            "value": dict(record["value"]),
        }
        copied["headers"]["data_origin"] = "GBFS_REPLAY"
        replayed.append(copied)
    return replayed


def _headers_line(record: Mapping[str, Any]) -> bytes:
    headers = record["headers"]
    header_text = ",".join(
        f"{key}:{headers[key]}"
        for key in (
            "contract_version",
            "record_type",
            "snapshot_id",
            "metadata_version",
            "data_origin",
        )
    )
    return (
        f"{header_text}\t{record['key']}\t{_json_value(record['value'])}\n"
    ).encode()


class KafkaCliProducer:
    """用官方 CLI 批量发送一组记录，并在 producer 关闭时等待确认。"""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        command: str | None = None,
        timeout: float = 60.0,
    ):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.command = (
            command
            or os.environ.get("KAFKA_CONSOLE_PRODUCER")
            or shutil.which("kafka-console-producer.sh")
        )
        if (
            not self.command
            and Path("/opt/kafka/bin/kafka-console-producer.sh").exists()
        ):
            self.command = "/opt/kafka/bin/kafka-console-producer.sh"
        if not self.command:
            raise KafkaSendError(
                "kafka-console-producer.sh not found; set KAFKA_CONSOLE_PRODUCER"
            )
        self.timeout = timeout

    def send_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        for record in records:
            _validate_record(record)
        command = [
            self.command,
            "--bootstrap-server",
            self.bootstrap_servers,
            "--topic",
            self.topic,
            "--producer-property",
            "acks=all",
            "--producer-property",
            "enable.idempotence=true",
            "--producer-property",
            "retries=5",
            "--property",
            "parse.headers=true",
            "--property",
            "parse.key=true",
            "--property",
            f"key.separator={chr(9)}",
            "--property",
            f"headers.delimiter={chr(9)}",
            "--property",
            "headers.separator=,",
            "--property",
            "headers.key.separator=:",
        ]
        try:
            result = subprocess.run(
                command,
                input=b"".join(_headers_line(record) for record in records),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KafkaSendError(
                f"Kafka producer failed to start or timed out: {error}"
            ) from error
        if result.returncode:
            detail = (
                (result.stderr or result.stdout)
                .decode("utf-8", errors="replace")
                .strip()
            )
            raise KafkaSendError(f"Kafka producer exited {result.returncode}: {detail}")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_batch_files(
    output_dir: Path, feeds: Mapping[str, bytes], batch: Batch
) -> Path:
    """先写 raw 与 metadata；成功后才返回可用于发送的快照目录。"""

    metadata_path = output_dir / "metadata" / f"{batch.metadata_version}.json"
    _atomic_write(metadata_path, batch.metadata_bytes)
    stamp = batch.ingested_at_utc.replace("-", "").replace(":", "").replace(".", "")
    snapshot_root = output_dir / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    final_dir = snapshot_root / f"{stamp}-{batch.snapshot_id[:12]}"
    if final_dir.exists():
        return final_dir
    with tempfile.TemporaryDirectory(
        prefix=f".{stamp}.", dir=snapshot_root
    ) as temporary:
        temporary_dir = Path(temporary)
        for name, content in feeds.items():
            (temporary_dir / f"{name}.json").write_bytes(content)
        (temporary_dir / "station_status_event_v1.sample.json").write_bytes(
            _json_bytes(batch.events[:5])
        )
        os.replace(temporary_dir, final_dir)
    return final_dir


def _load_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"snapshots": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StreamError(f"collection log is unreadable: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("snapshots", []), list):
        raise StreamError("collection log must contain a snapshots array")
    return value


def _append_log(path: Path, base: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    snapshots = list(base.get("snapshots", []))
    snapshots.append(dict(record))
    _atomic_write(path, _json_bytes({**base, "snapshots": snapshots}))


def _write_collection_failure(output_dir: Path, error: CollectionError) -> Path:
    """保留采集失败时已经取得的 raw，并返回失败证据目录。"""

    raw_digest = hashlib.sha256(
        b"\n".join(error.raw[name] for name in sorted(error.raw))
    ).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = output_dir / "failures" / f"{stamp}-{raw_digest or 'no-raw'}"
    if final_dir.exists():
        return final_dir
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{stamp}-", dir=final_dir.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        for name, content in error.raw.items():
            (temporary_dir / f"{name}.json").write_bytes(content)
        _atomic_write(
            temporary_dir / "feed_manifest.json",
            _json_bytes({"feeds": error.feed_urls, "raw_files": sorted(error.raw)}),
        )
        os.replace(temporary_dir, final_dir)
    return final_dir


def _collection_failure_log(output_dir: Path, error: CollectionError) -> dict[str, Any]:
    failure_dir = _write_collection_failure(output_dir, error)
    return {
        "status": "FAILED_COLLECTION",
        "data_origin": "GBFS_LIVE",
        "failure_count": 1,
        "error": str(error),
        "failure_directory": str(failure_dir.relative_to(output_dir)),
        "raw_files": sorted(error.raw),
        "fetched_feed_count": len(error.raw),
        "expected_feed_count": len(GBFS_FEEDS) + 1,
        "feed_urls": error.feed_urls,
    }


def _fetch_with_bytes(
    fetcher: Callable[[str, float], Any], url: str, timeout: float
) -> tuple[dict[str, Any], bytes]:
    result = fetcher(url, timeout)
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], dict)
        and isinstance(result[1], bytes)
    ):
        return result
    if isinstance(result, dict):
        return result, _json_bytes(result)
    raise StreamError(f"fetcher returned an unsupported result for {url}")


def collect_batch(
    discovery_url: str,
    locale: str,
    timeout: float,
    fetcher: Callable[[str, float], Any] = fetch_json_bytes,
) -> tuple[Batch, dict[str, bytes], dict[str, str], dict[str, Any]]:
    raw: dict[str, bytes] = {}
    feed_urls: dict[str, str] = {}
    try:
        discovery, discovery_bytes = _fetch_with_bytes(fetcher, discovery_url, timeout)
        raw["discovery"] = discovery_bytes
        if discovery.get("version") != GBFS_VERSION:
            raise StreamError(f"discovery.version must be {GBFS_VERSION!r}")
        feed_urls = discover_feed_urls(discovery, locale)
        fetched: dict[str, dict[str, Any]] = {}
        for name, url in feed_urls.items():
            fetched[name], raw[name] = _fetch_with_bytes(fetcher, url, timeout)
        feed_errors = validate_target_feeds(fetched)
        if feed_errors:
            raise StreamError(f"GBFS feed validation failed: {feed_errors}")
        ingested_at = datetime.now(timezone.utc)
        batch = build_batch(
            fetched["station_information"],
            fetched["station_status"],
            raw["station_status"],
            ingested_at,
        )
    except Exception as error:
        raise CollectionError(str(error), raw, feed_urls) from error
    return (
        batch,
        raw,
        feed_urls,
        {"discovery": discovery, "discovery_bytes": discovery_bytes},
    )


def send_batch(producer: Producer, batch: Batch) -> None:
    """station 全部确认后才发送 end；失败时绝不发送 end。"""

    producer.send_records(batch.records[:-1])
    producer.send_records(batch.records[-1:])


def run_once(
    output_dir: Path,
    bootstrap_servers: str,
    topic: str,
    discovery_url: str = DEFAULT_DISCOVERY_URL,
    locale: str = "en",
    timeout: float = 30.0,
    producer: Producer | None = None,
    fetcher: Callable[[str, float], Any] = fetch_json_bytes,
    data_origin: str = "GBFS_LIVE",
) -> dict[str, Any]:
    try:
        batch, raw, feed_urls, discovery_info = collect_batch(
            discovery_url, locale, timeout, fetcher
        )
    except CollectionError as error:
        log_path = output_dir / "collection_log.json"
        log = _load_log(log_path)
        log.setdefault("discovery_url", discovery_url)
        log.setdefault("tracked_station_ids", [])
        log.setdefault("source_version", GBFS_VERSION)
        _append_log(log_path, log, _collection_failure_log(output_dir, error))
        raise
    if data_origin != "GBFS_LIVE":
        batch = _with_origin(batch, data_origin)
    snapshot_dir = write_batch_files(output_dir, raw, batch)
    log_path = output_dir / "collection_log.json"
    log = _load_log(log_path)
    log.setdefault("discovery_url", discovery_url)
    log.setdefault("tracked_station_ids", [])
    log.setdefault("source_version", GBFS_VERSION)
    _atomic_write(output_dir / "discovery.json", discovery_info["discovery_bytes"])
    _atomic_write(
        output_dir / "feed_manifest.json",
        _json_bytes(
            {
                "discovery_url": discovery_url,
                "locale": locale,
                "source_version": GBFS_VERSION,
                "feeds": feed_urls,
            }
        ),
    )
    raw_hash = hashlib.sha256(raw["station_status"]).hexdigest()
    previous = [item for item in log["snapshots"] if isinstance(item, Mapping)]
    source_time = batch.provider_last_updated
    same_source = [
        item for item in previous if item.get("provider_last_updated") == source_time
    ]
    if any(
        item.get("status") == "PUBLISHED" and item.get("status_raw_sha256") == raw_hash
        for item in same_source
    ):
        state = "SKIPPED_DUPLICATE"
    elif any(item.get("status") == "PUBLISHED" for item in same_source):
        state = "REJECTED_CONFLICT"
    else:
        try:
            send_batch(producer or KafkaCliProducer(bootstrap_servers, topic), batch)
        except Exception as error:
            state = "FAILED_SEND"
            _append_log(
                log_path,
                log,
                _batch_log(
                    batch, snapshot_dir, output_dir, raw_hash, state, str(error)
                ),
            )
            raise
        state = "PUBLISHED"
    record = _batch_log(batch, snapshot_dir, output_dir, raw_hash, state, None)
    _append_log(log_path, log, record)
    return record


def _with_origin(batch: Batch, origin: str) -> Batch:
    records = []
    for record in batch.records:
        copied = {
            "key": record["key"],
            "headers": dict(record["headers"]),
            "value": dict(record["value"]),
        }
        copied["headers"]["data_origin"] = origin
        records.append(copied)
    return Batch(**{**batch.__dict__, "records": records})


def _batch_log(
    batch: Batch,
    snapshot_dir: Path,
    output_dir: Path,
    raw_hash: str,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "snapshot_id": batch.snapshot_id,
        "metadata_version": batch.metadata_version,
        "data_origin": batch.records[0]["headers"]["data_origin"]
        if batch.records
        else "GBFS_LIVE",
        "status": status,
        "snapshot_directory": str(snapshot_dir.relative_to(output_dir)),
        "provider_last_updated": batch.provider_last_updated,
        "ingested_at_utc": batch.ingested_at_utc,
        "station_count": batch.station_count,
        "status_raw_sha256": raw_hash,
        "failure_count": 1 if status.startswith("FAILED") else 0,
        "mapping_success_count": sum(
            row.get("mapping_status") == "SHORT_NAME" for row in batch.metadata
        ),
        "mapping_isolated_count": sum(
            row.get("mapping_status") == "ISOLATED" for row in batch.metadata
        ),
        "mapping_warnings": batch.mapping_warnings,
        "quality_warnings": batch.quality_warnings,
    }
    if error:
        value["error"] = error
    return value


def run_replay(
    path: Path,
    producer: Producer,
    output_dir: Path | None = None,
    metadata_file: Path | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    try:
        if metadata_file is None:
            raise StreamError("metadata_file is required for replay")
        records = replay_records(path)
        try:
            actual_metadata = hashlib.sha256(metadata_file.read_bytes()).hexdigest()
        except OSError as error:
            raise StreamError(f"cannot read metadata file: {error}") from error
        expected_metadata = records[0]["headers"]["metadata_version"]
        if actual_metadata != expected_metadata:
            raise StreamError(
                f"metadata hash mismatch: expected {expected_metadata}, got {actual_metadata}"
            )
        producer.send_records(records[:-1])
        producer.send_records(records[-1:])
    except Exception as error:
        if output_dir is not None:
            result: dict[str, Any] = {
                "status": "FAILED_REPLAY",
                "data_origin": "GBFS_REPLAY",
                "failure_count": 1,
                "error": str(error),
                "replay_file": str(path),
            }
            if records:
                result.update(
                    {
                        "snapshot_id": records[0]["headers"]["snapshot_id"],
                        "metadata_version": records[0]["headers"]["metadata_version"],
                        "station_count": records[-1]["value"]["station_count"],
                    }
                )
            _append_log(
                output_dir / "collection_log.json",
                _load_log(output_dir / "collection_log.json"),
                result,
            )
        raise
    result = {
        "status": "PUBLISHED",
        "data_origin": "GBFS_REPLAY",
        "snapshot_id": records[0]["headers"]["snapshot_id"],
        "metadata_version": records[0]["headers"]["metadata_version"],
        "station_count": records[-1]["value"]["station_count"],
        "replay_file": str(path),
    }
    if output_dir is not None:
        _append_log(
            output_dir / "collection_log.json",
            _load_log(output_dir / "collection_log.json"),
            result,
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成并发送 GBFS canonical Kafka 快照批次"
    )
    parser.add_argument(
        "--bootstrap-servers",
        required=True,
        help="Kafka broker 地址，例如 localhost:9092",
    )
    parser.add_argument("--topic", default=TOPIC)
    parser.add_argument(
        "--output-dir", default=None, help="raw、metadata 和 collection log 的绝对目录"
    )
    parser.add_argument("--discovery-url", default=DEFAULT_DISCOVERY_URL)
    parser.add_argument("--locale", default="en")
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument(
        "--snapshots", type=int, default=0, help="采集次数；0 表示持续运行"
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--producer-command", default=None)
    parser.add_argument(
        "--replay", type=Path, default=None, help="发送 events.ndjson 的录制批次"
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        default=None,
        help="校验 replay 所引用的 metadata 文件",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅校验并打印，不连接 Kafka"
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if args.snapshots < 0:
        raise SystemExit("--snapshots cannot be negative")
    if args.interval_seconds < 0:
        raise SystemExit("--interval-seconds cannot be negative")
    if args.replay is None and not args.output_dir:
        raise SystemExit("--output-dir is required for live collection")
    if args.replay is not None and not args.output_dir:
        raise SystemExit("--output-dir is required with --replay")
    if args.replay is not None and args.snapshots:
        raise SystemExit("--snapshots cannot be used with --replay")
    if args.replay is not None and args.metadata_file is None:
        raise SystemExit("--metadata-file is required with --replay")
    return args


class _DryRunProducer:
    def send_records(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            print(_json_value(record))


def run(args: argparse.Namespace) -> None:
    producer: Producer = (
        _DryRunProducer()
        if args.dry_run
        else KafkaCliProducer(
            args.bootstrap_servers, args.topic, args.producer_command, args.timeout
        )
    )
    if args.replay is not None:
        print(
            json.dumps(
                run_replay(
                    args.replay,
                    producer,
                    Path(args.output_dir) if args.output_dir else None,
                    args.metadata_file,
                ),
                ensure_ascii=False,
            )
        )
        return
    output_dir = Path(args.output_dir)
    count = 0
    while args.snapshots == 0 or count < args.snapshots:
        try:
            result = run_once(
                output_dir,
                args.bootstrap_servers,
                args.topic,
                args.discovery_url,
                args.locale,
                args.timeout,
                producer,
            )
        except (OSError, StreamError, KafkaSendError, ValueError) as error:
            if args.snapshots != 0:
                raise
            print(f"gbfs stream: {error}", file=sys.stderr)
            time.sleep(args.interval_seconds)
            continue
        print(json.dumps(result, ensure_ascii=False))
        count += 1
        if args.snapshots == 0 or count < args.snapshots:
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    try:
        run(parse_args())
    except (OSError, StreamError, KafkaSendError, ValueError) as error:
        print(f"gbfs stream: {error}", file=sys.stderr)
        raise SystemExit(1)
