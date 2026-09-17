#!/usr/bin/env python3
"""Load offline TSVs through Sqoop staging and atomically publish to MySQL."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__:
    from .offline import SERVING_COLUMNS
else:
    from citibike.offline import SERVING_COLUMNS


class ExportError(RuntimeError):
    """Raised when staging or publication cannot be proven safe."""


Runner = Callable[..., subprocess.CompletedProcess[str]]
TABLES = tuple(SERVING_COLUMNS)
PRIMARY_KEYS = {
    "dim_station_v1": ("station_id",),
    "dws_station_hourly_flow_v1": ("station_id", "service_date", "hour"),
    "dws_station_hour_profile_v1": ("station_id", "day_of_week", "hour"),
    "dws_station_od_hourly_v1": ("service_date", "hour", "from_station_id", "to_station_id"),
}


def _run(command: list[str], runner: Runner, *, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    result = runner(command, input=stdin, check=False, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "command failed").strip()
        safe = ["***" if index and command[index - 1] == "--password" else value for index, value in enumerate(command)]
        raise ExportError(f"{shlex.join(safe)}: {details}")
    return (result.stdout or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExportError(f"cannot read offline evidence {path}: {error}") from error
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise ExportError("offline evidence is absent or not PASS")
    return value


def _password(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as error:
        raise ExportError(f"cannot read password file {path}: {error}") from error
    if not value or "\n" in value or "\r" in value:
        raise ExportError("password file must contain exactly one non-empty line")
    return value


def _mysql_command(mysql_bin: str, host: str, port: int, database: str, username: str) -> list[str]:
    return [
        mysql_bin, "--batch", "--skip-column-names", "--default-character-set=utf8mb4",
        "--host", host, "--port", str(port), "--user", username, database,
    ]


def _mysql(
    sql: str,
    *,
    mysql_bin: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    runner: Runner,
) -> str:
    env = os.environ.copy()
    env["MYSQL_PWD"] = password
    return _run(
        _mysql_command(mysql_bin, host, port, database, username), runner, env=env, stdin=sql
    )


def _sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _parse_ints(output: str, expected: int, label: str) -> tuple[int, ...]:
    fields = output.split("\t") if output else []
    if len(fields) != expected:
        raise ExportError(f"malformed MySQL validation result for {label}: {output!r}")
    try:
        return tuple(int(value) for value in fields)
    except ValueError as error:
        raise ExportError(f"non-integer MySQL validation result for {label}: {output!r}") from error


def validate_staging(
    dataset: str,
    expected_counts: dict[str, int],
    *,
    mysql_bin: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    runner: Runner,
) -> dict[str, int]:
    actual: dict[str, int] = {}
    for table in TABLES:
        load = f"{table}_load"
        keys = ", ".join(f"`{key}`" for key in PRIMARY_KEYS[table])
        output = _mysql(
            f"SELECT COUNT(*), COUNT(DISTINCT dataset_id), "
            f"COALESCE(SUM(dataset_id <> {_sql_quote(dataset)}),0) FROM `{load}`;\n"
            f"SELECT COUNT(*) FROM (SELECT {keys}, COUNT(*) c FROM `{load}` "
            f"GROUP BY {keys} HAVING c > 1) duplicate_keys;\n",
            mysql_bin=mysql_bin, host=host, port=port, database=database,
            username=username, password=password, runner=runner,
        ).splitlines()
        if len(output) != 2:
            raise ExportError(f"malformed validation output for {load}: {output!r}")
        row_count, versions, wrong_version = _parse_ints(output[0], 3, load)
        duplicate_keys = _parse_ints(output[1], 1, load)[0]
        expected = int(expected_counts[table])
        if row_count != expected:
            raise ExportError(f"{load}: row count {row_count} != offline evidence {expected}")
        expected_versions = 1 if expected else 0
        if versions != expected_versions or wrong_version != 0 or duplicate_keys != 0:
            raise ExportError(
                f"{load}: versions={versions}, wrong_version={wrong_version}, duplicate_keys={duplicate_keys}"
            )
        actual[table] = row_count
    conservation = _mysql(
        "SELECT COALESCE(SUM(inbound_rides),0), COALESCE(SUM(outbound_rides),0) "
        "FROM dws_station_hourly_flow_v1_load;\n"
        "SELECT COALESCE(SUM(ride_count),0), COALESCE(SUM(ride_count <= 0),0) "
        "FROM dws_station_od_hourly_v1_load;\n"
        "SELECT COALESCE(SUM(sample_days <= 0),0) FROM dws_station_hour_profile_v1_load;\n",
        mysql_bin=mysql_bin, host=host, port=port, database=database,
        username=username, password=password, runner=runner,
    ).splitlines()
    if len(conservation) != 3:
        raise ExportError(f"malformed conservation output: {conservation!r}")
    inbound, outbound = _parse_ints(conservation[0], 2, "flow conservation")
    od_rides, non_positive_od = _parse_ints(conservation[1], 2, "OD conservation")
    invalid_profile = _parse_ints(conservation[2], 1, "profile sample_days")[0]
    if inbound != outbound or outbound != od_rides:
        raise ExportError(f"staging conservation failed: inbound={inbound}, outbound={outbound}, od={od_rides}")
    if non_positive_od or invalid_profile:
        raise ExportError(
            f"staging contains non-positive OD/profile values: od={non_positive_od}, profile={invalid_profile}"
        )
    return actual


def publication_sql(dataset: str, evidence: dict[str, Any]) -> str:
    months = json.dumps(evidence["source_months"], separators=(",", ":"))
    minimum = evidence.get("min_service_date")
    maximum = evidence.get("max_service_date")
    published = evidence["published_at_utc"].replace("T", " ").removesuffix("Z")
    statements = ["SET time_zone = '+00:00';", "START TRANSACTION;"]
    for table in reversed(TABLES):
        statements.append(f"DELETE FROM `{table}`;")
    for table in TABLES:
        columns = ", ".join(f"`{name}`" for name in SERVING_COLUMNS[table])
        statements.append(f"INSERT INTO `{table}` ({columns}) SELECT {columns} FROM `{table}_load`;")
    statements.extend([
        "DELETE FROM historical_release WHERE singleton = 1;",
        (
            "INSERT INTO historical_release "
            "(singleton, dataset_id, source_months, min_service_date, max_service_date, published_at_utc) VALUES "
            f"(1, {_sql_quote(dataset)}, CAST({_sql_quote(months)} AS JSON), "
            f"{_sql_quote(minimum) if minimum else 'NULL'}, {_sql_quote(maximum) if maximum else 'NULL'}, "
            f"{_sql_quote(published)});"
        ),
        "COMMIT;",
    ])
    return "\n".join(statements) + "\n"


def export_release(
    *,
    dataset: str,
    hdfs_root: str,
    offline_evidence: Path,
    password_file: Path,
    connect: str,
    host: str,
    port: int,
    database: str,
    username: str,
    sqoop_bin: str = "sqoop",
    mysql_bin: str = "mysql",
    ddl_path: Path | None = None,
    local_mapreduce: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    evidence = _read_json(offline_evidence)
    if evidence.get("dataset_id") != dataset:
        raise ExportError(f"dataset id does not match offline evidence: {dataset}")
    password = _password(password_file)
    if ddl_path is not None:
        try:
            ddl = ddl_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ExportError(f"cannot read serving DDL {ddl_path}: {error}") from error
        _mysql(
            ddl, mysql_bin=mysql_bin, host=host, port=port, database=database,
            username=username, password=password, runner=runner,
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".sqoop-password-", dir=password_file.resolve().parent
    )
    temporary_password_file = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            # Sqoop 1.4.7 consumes every byte in --password-file, including a final newline.
            handle.write(password)
        for table in TABLES:
            _mysql(
                f"DELETE FROM `{table}_load`;\n", mysql_bin=mysql_bin, host=host, port=port,
                database=database, username=username, password=password, runner=runner,
            )
            export_dir = f"{hdfs_root.rstrip('/')}/serving_export/{dataset}/{table}"
            sqoop_password_uri = temporary_password_file.as_uri()
            command = [sqoop_bin, "export"]
            if local_mapreduce:
                local_directory = password_file.resolve().parent / "sqoop-local"
                local_directory.mkdir(mode=0o700, exist_ok=True)
                command.extend([
                    "-Dmapreduce.framework.name=local",
                    "-Dmapreduce.jobtracker.address=local",
                    f"-Dmapreduce.cluster.local.dir={local_directory}",
                ])
            command.extend([
                "--connect", connect, "--username", username,
                "--password-file", sqoop_password_uri, "--table", f"{table}_load",
                "--export-dir", export_dir, "--columns", ",".join(SERVING_COLUMNS[table]),
                "--input-fields-terminated-by", "\t", "--input-null-string", "\\\\N",
                "--input-null-non-string", "\\\\N", "--num-mappers", "1", "--batch",
            ])
            _run(command, runner)
    finally:
        temporary_password_file.unlink(missing_ok=True)
    expected_counts = {
        "dim_station_v1": int(evidence["counts"]["dim"]),
        "dws_station_hourly_flow_v1": int(evidence["counts"]["flow"]),
        "dws_station_hour_profile_v1": int(evidence["counts"]["profile"]),
        "dws_station_od_hourly_v1": int(evidence["counts"]["od"]),
    }
    staged = validate_staging(
        dataset, expected_counts, mysql_bin=mysql_bin, host=host, port=port,
        database=database, username=username, password=password, runner=runner,
    )
    _mysql(
        publication_sql(dataset, evidence), mysql_bin=mysql_bin, host=host, port=port,
        database=database, username=username, password=password, runner=runner,
    )
    published = _mysql(
        "SELECT dataset_id FROM historical_release WHERE singleton = 1;\n",
        mysql_bin=mysql_bin, host=host, port=port, database=database,
        username=username, password=password, runner=runner,
    )
    if published != dataset:
        raise ExportError(f"publication verification returned {published!r}, expected {dataset}")
    return {"status": "PASS", "dataset_id": dataset, "staging_counts": staged, "published": True}


def _write_evidence(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--hdfs-root", required=True)
    parser.add_argument("--offline-evidence", required=True, type=Path)
    parser.add_argument("--password-file", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--connect", default="jdbc:mysql://localhost:3306/citibike?useSSL=false&serverTimezone=UTC")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--database", default="citibike")
    parser.add_argument("--username", default="citibike")
    parser.add_argument("--sqoop-bin", default="sqoop")
    parser.add_argument("--mysql-bin", default="mysql")
    parser.add_argument("--ddl", type=Path, default=Path("sql/serving.sql"))
    parser.add_argument(
        "--local-mapreduce", action="store_true",
        help="run Sqoop's MapReduce job locally (single-node diagnostics only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_release(
            dataset=args.dataset_id, hdfs_root=args.hdfs_root,
            offline_evidence=args.offline_evidence, password_file=args.password_file,
            connect=args.connect, host=args.host, port=args.port, database=args.database,
            username=args.username, sqoop_bin=args.sqoop_bin, mysql_bin=args.mysql_bin,
            ddl_path=args.ddl, local_mapreduce=args.local_mapreduce,
        )
        _write_evidence(args.evidence, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:  # noqa: BLE001  # CLI must always leave failure evidence.
        failure = {
            "status": "FAIL", "dataset_id": args.dataset_id, "published": False,
            "error_type": type(error).__name__, "error": str(error),
        }
        try:
            _write_evidence(args.evidence, failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
