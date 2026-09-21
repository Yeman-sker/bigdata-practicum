"""Portable foreground launcher; infrastructure and cold data setup are explicit.

See docs/team-runtime.md. doctor/plan never read passwords or contact services.
No installation, daemon discovery, database initialization or offset resets.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PATH_KEYS = (
    "DATA_DIR", "JAVA8_HOME", "JAVA17_HOME", "HADOOP_HOME", "HADOOP_CONF_DIR",
    "HIVE_HOME", "HIVE_CONF_DIR", "SPARK_HOME", "SPARK_CONF_DIR", "SQOOP_HOME",
    "KAFKA_HOME", "MYSQL_PASSWORD_FILE",
)
PORT_KEYS = ("MYSQL_PORT", "BACKEND_PORT", "FRONTEND_PORT")
TEXT_KEYS = (
    "KAFKA_BOOTSTRAP_SERVERS", "KAFKA_TOPIC", "SPRING_KAFKA_CONSUMER_GROUP_ID",
    "MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER",
)
SERVICES = ("backend", "collector", "frontend")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def outside_checkout(path: Path) -> bool:
    resolved = path.resolve()
    return not (resolved == ROOT or ROOT in resolved.parents or resolved in ROOT.parents)


def load_config(path: Path) -> dict[str, str]:
    try:
        config = json.loads(path.read_text(), object_pairs_hook=unique_keys)
    except json.JSONDecodeError:
        raise ValueError("configuration must be JSON, not shell syntax") from None
    required = set(PATH_KEYS + PORT_KEYS + TEXT_KEYS)
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("configuration keys must match config/team-runtime.example.json (no inline secrets)")
    for key in PATH_KEYS + TEXT_KEYS:
        value = config[key]
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise ValueError(f"{key} must be a nonempty single-line string")
    for key in PATH_KEYS:
        if not Path(config[key]).is_absolute() or ".." in Path(config[key]).parts:
            raise ValueError(f"{key} must be an absolute path without '..' (no ~ or variable expansion)")
    for key in ("DATA_DIR", "MYSQL_PASSWORD_FILE"):
        if not outside_checkout(Path(config[key])):
            raise ValueError(f"{key} must be outside the checkout, in a dedicated location")
    for key in PORT_KEYS:
        if type(config[key]) is not int or not 1 <= config[key] <= 65535:
            raise ValueError(f"{key} must be an integer port in 1..65535")
    if config["BACKEND_PORT"] == config["FRONTEND_PORT"]:
        raise ValueError("BACKEND_PORT and FRONTEND_PORT must differ")
    for key in ("MYSQL_DATABASE", "MYSQL_USER"):
        if not re.fullmatch(r"[A-Za-z0-9_]+", config[key]):
            raise ValueError(f"{key} must contain only letters, digits and underscores")
    for key in ("KAFKA_TOPIC", "SPRING_KAFKA_CONSUMER_GROUP_ID"):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,248}", config[key]):
            raise ValueError(f"{key} must be a plain Kafka name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", config["MYSQL_HOST"]):
        raise ValueError("MYSQL_HOST must be a hostname or IPv4 address, not a URL")
    for broker in config["KAFKA_BOOTSTRAP_SERVERS"].split(","):
        match = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*:([0-9]+)", broker)
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError("KAFKA_BOOTSTRAP_SERVERS must contain hostname:port pairs")
    return {key: str(value) for key, value in config.items()}


def environment(config: dict[str, str], jdk: str) -> dict[str, str]:
    # Do not inherit an unrelated Spring profile, database, JVM options or test opt-ins.
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TERM", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    env.update(config)
    env["JAVA_HOME"] = config[f"JAVA{jdk}_HOME"]
    homes = ("HADOOP_HOME", "HIVE_HOME", "SPARK_HOME", "SQOOP_HOME", "KAFKA_HOME")
    env["PATH"] = os.pathsep.join([env["JAVA_HOME"] + "/bin", *(config[k] + "/bin" for k in homes), os.environ.get("PATH", os.defpath)])
    for component in ("COMMON", "HDFS", "MAPRED", "YARN"):
        env[f"HADOOP_{component}_HOME"] = config["HADOOP_HOME"]
    env.update(
        REPO_ROOT=str(ROOT), PYTHONPATH=str(ROOT), PYSPARK_PYTHON=sys.executable,
        SPRING_PROFILES_ACTIVE="live", SERVER_ADDRESS="127.0.0.1",
        SERVER_PORT=config["BACKEND_PORT"], SPRING_DATASOURCE_USERNAME=config["MYSQL_USER"],
        SPRING_DATASOURCE_URL=f"jdbc:mysql://{config['MYSQL_HOST']}:{config['MYSQL_PORT']}/{config['MYSQL_DATABASE']}?serverTimezone=UTC",
        CITIBIKE_API_TARGET=f"http://127.0.0.1:{config['BACKEND_PORT']}",
    )
    return env


def service_command(config: dict[str, str], service: str) -> list[str]:
    if service == "backend":
        return [config["JAVA17_HOME"] + "/bin/java", "-jar",
                str(ROOT / "backend/target/citibike-backend-1.1.0-SNAPSHOT.jar"),
                "--spring.profiles.active=live"]
    if service == "collector":
        return [sys.executable, "-m", "citibike.gbfs_stream", "--output-dir", config["DATA_DIR"] + "/gbfs",
                "--bootstrap-servers", config["KAFKA_BOOTSTRAP_SERVERS"], "--topic", config["KAFKA_TOPIC"],
                "--producer-command", config["KAFKA_HOME"] + "/bin/kafka-console-producer.sh",
                "--interval-seconds", "60"]
    return ["node", str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
            "--host", "127.0.0.1", "--port", config["FRONTEND_PORT"], "--strictPort"]


def check_password_file(config: dict[str, str]) -> Path:
    path = Path(config["MYSQL_PASSWORD_FILE"])
    info = path.stat()  # doctor checks metadata only, never the contents.
    if not outside_checkout(path) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("MYSQL_PASSWORD_FILE must be an external regular file owned by you, mode 600 or 400")
    if not os.access(path, os.R_OK) or info.st_size == 0:
        raise ValueError("MYSQL_PASSWORD_FILE must be readable and nonempty")
    return path


def configured_metastore(properties) -> bool:
    if (properties.get("hive.metastore.uris") or "").startswith("thrift://"):
        return True
    # Classroom setups also use an embedded metastore backed by MySQL.
    # Accept explicit persistence, never an implicit local Derby database.
    return (properties.get("javax.jdo.option.ConnectionURL") or "").startswith("jdbc:mysql://") and all(
        properties.get(key) for key in (
            "javax.jdo.option.ConnectionDriverName", "javax.jdo.option.ConnectionUserName",
        )
    )


def doctor(config: dict[str, str]) -> int:
    failures = []

    def check(label, condition, remedy):
        print(f"{'OK' if condition else 'GAP'} {label}" + ("" if condition else f": {remedy}"))
        if not condition:
            failures.append(label)

    check("Ubuntu 22.04", sys.platform == "linux" and 'VERSION_ID="22.04"' in Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else False,
          "run inside your Ubuntu 22.04 WSL2/OrbStack guest")
    check("Python 3.10/3.11", (3, 10) <= sys.version_info[:2] <= (3, 11), "use the guest Python 3.10/3.11 for Spark 3.5")
    data = Path(config["DATA_DIR"])
    check("DATA_DIR", data.is_dir() and os.access(data, os.W_OK | os.X_OK), "create a dedicated writable external directory")
    try:
        check_password_file(config)
        check("password file permissions (contents not read)", True, "")
    except (OSError, ValueError):
        check("password file permissions", False, "create your own external password file, owned by you, chmod 600")
    for jdk, version in (("8", r'version "1\.8\.'), ("17", r'version "17[.\"]')):
        java = config[f"JAVA{jdk}_HOME"] + "/bin/java"
        try:
            result = subprocess.run([java, "-version"], env=environment(config, jdk), capture_output=True, text=True, timeout=10)
            valid = result.returncode == 0 and re.search(version, result.stderr + result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            valid = False
        check(f"JDK {jdk}", bool(valid), f"correct JAVA{jdk}_HOME to the preinstalled JDK {jdk}")
    for home, binary in (("HADOOP_HOME", "hdfs"), ("HADOOP_HOME", "yarn"), ("HIVE_HOME", "hive"),
                         ("SPARK_HOME", "spark-submit"), ("SQOOP_HOME", "sqoop"),
                         ("KAFKA_HOME", "kafka-console-producer.sh"), ("KAFKA_HOME", "kafka-topics.sh"),
                         ("KAFKA_HOME", "kafka-consumer-groups.sh")):
        check(binary, os.access(Path(config[home]) / "bin" / binary, os.X_OK), f"correct {home}; see classroom version matrix")
    for binary in ("node", "npm", "mvn", "mysql", "curl"):
        check(binary, shutil.which(binary) is not None, f"put the preinstalled {binary} on PATH")
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        version = tuple(int(part) for part in result.stdout.strip().lstrip("v").split("."))
        node_ok = result.returncode == 0 and version >= (22, 12, 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        node_ok = False
    check("Node >=22.12", node_ok, "select the classroom Node >=22.12")
    for key, filename in (("HADOOP_CONF_DIR", "core-site.xml"), ("HADOOP_CONF_DIR", "hdfs-site.xml"),
                          ("HADOOP_CONF_DIR", "yarn-site.xml"), ("HADOOP_CONF_DIR", "mapred-site.xml"),
                          ("HIVE_CONF_DIR", "hive-site.xml"), ("SPARK_CONF_DIR", "hive-site.xml")):
        try:
            properties = {p.findtext("name"): p.findtext("value") for p in ET.parse(Path(config[key]) / filename).getroot().findall("property")}
            if filename == "hive-site.xml":
                valid = configured_metastore(properties)
            elif filename == "core-site.xml":
                valid = (properties.get("fs.defaultFS") or "").startswith("hdfs://")
            else:
                valid = True
        except (OSError, ET.ParseError):
            valid = False
        check(f"{key}/{filename}", valid, "provide classroom XML: HDFS fs.defaultFS and explicit Thrift or MySQL-backed Hive metastore")
    for key in ("HIVE_HOME", "SQOOP_HOME"):
        check(f"{key} MySQL JDBC driver", any((Path(config[key]) / "lib").glob("*mysql*connector*.jar")), "use the classroom MySQL JDBC driver in lib/")
    for artifact in ("backend/target/citibike-backend-1.1.0-SNAPSHOT.jar", "frontend/node_modules/vite/bin/vite.js"):
        check(artifact, (ROOT / artifact).is_file(), "build/prepare locked dependencies as described in the quickstart")
    print("Local checks only: component versions/config compatibility, service readiness, topic partition count, DB schema and full-month data still require the quickstart checks.")
    return int(bool(failures))


def metadata_file(config: dict[str, str]) -> Path:
    directory = Path(config["DATA_DIR"]) / "gbfs"
    log = json.loads((directory / "collection_log.json").read_text())
    if not isinstance(log, dict) or not isinstance(log.get("snapshots"), list):
        raise ValueError("collector log must contain a snapshots array")
    for batch in reversed(log["snapshots"]):
        if not isinstance(batch, dict):
            raise ValueError("collector batch must be an object")
        if batch.get("status") == "PUBLISHED" and batch.get("data_origin") == "GBFS_LIVE":
            version = batch.get("metadata_version", "")
            if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
                raise ValueError("invalid collector metadata version")
            path = directory / "metadata" / (version + ".json")
            if hashlib.sha256(path.read_bytes()).hexdigest() != version:
                raise ValueError("collector metadata hash mismatch")
            return path
    raise ValueError("no PUBLISHED GBFS_LIVE batch; start the real collector and wait for a complete batch")


def run_service(config: dict[str, str], service: str) -> None:
    env = environment(config, "17")
    command = service_command(config, service)
    binaries = [command[0]]
    if service == "collector":
        binaries += [config["JAVA17_HOME"] + "/bin/java", config["KAFKA_HOME"] + "/bin/kafka-console-producer.sh"]
    if any(shutil.which(binary, path=env["PATH"]) is None for binary in binaries):
        raise ValueError(f"{service} executable missing; run doctor and correct the component homes/PATH")
    if service == "frontend" and not Path(command[1]).is_file():
        raise ValueError("prepare frontend locked dependencies before starting; see quickstart")
    data = Path(config["DATA_DIR"])
    if not data.is_dir() or not outside_checkout(data):
        raise ValueError("create DATA_DIR outside the checkout before starting")
    if service == "backend":
        password = check_password_file(config).read_text().removesuffix("\n").removesuffix("\r")
        if not password or "\n" in password or "\r" in password or "\0" in password:
            raise ValueError("password file must contain one nonempty line")
        env["SPRING_DATASOURCE_PASSWORD"] = password
        if not Path(command[2]).is_file():
            raise ValueError("build backend first: mvn -f backend/pom.xml package")
    if service in ("backend", "frontend"):
        port = int(config[service.upper() + "_PORT"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))  # Refuse an occupied port; never stop its owner.
    # The inherited flock survives exec and is released by process exit; no stale PID files.
    lock = os.open(data / (".team-runtime-" + service + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(lock, True)
        os.chdir(ROOT / "frontend" if service == "frontend" else ROOT)
        os.execvpe(command[0], command, env)
    finally:
        os.close(lock)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="external JSON copy of config/team-runtime.example.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="local prerequisite checks only; no service connections or password reads")
    sub.add_parser("metadata", help="print hash-verified metadata path from the last published live collector batch")
    for action in ("plan", "run"):
        target = sub.add_parser(action, help="print only" if action == "plan" else "foreground process; Ctrl-C stops only this terminal's process group")
        target.add_argument("service", choices=SERVICES)
    execute = sub.add_parser("exec", help="run an explicit command with isolated component/JDK environment; no password injection")
    execute.add_argument("--jdk", choices=("8", "17"), required=True)
    execute.add_argument("command", nargs=argparse.REMAINDER, help="-- executable args; this command may write, review it first")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == "doctor":
            return doctor(config)
        if args.action == "metadata":
            print(metadata_file(config))
        elif args.action == "plan":
            print("JAVA_HOME=" + shlex.quote(config["JAVA17_HOME"]))
            print("profile=live; password supplied only at backend execution (never printed)")
            print(shlex.join(service_command(config, args.service)))
            print(f"frontend: http://127.0.0.1:{config['FRONTEND_PORT']}/?api=1")
        elif args.action == "run":
            run_service(config, args.service)
        else:
            command = args.command
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                raise ValueError("exec requires an explicit executable after --")
            os.chdir(ROOT)
            os.execvpe(command[0], command, environment(config, args.jdk))
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not dump config, environment, subprocess output or password contents.
        print(f"runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
