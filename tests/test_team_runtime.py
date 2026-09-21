"""No live infrastructure: check launcher boundaries and real CLI argument parsing."""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from citibike import gbfs_stream, historical_landing, historical_source, offline, serving_export, team_runtime as runtime


class TeamRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.values = json.loads((runtime.ROOT / "config/team-runtime.example.json").read_text())
        self.values["DATA_DIR"] = str(self.root / "data with spaces")
        self.values["MYSQL_PASSWORD_FILE"] = str(self.root / "password")
        self.config_path = self.root / "runtime.json"
        self.write_config()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.values))
        return runtime.load_config(self.config_path)

    def test_strict_configuration_and_symlink_escape(self):
        original = copy.copy(self.values)
        for key, value in (("DATA_DIR", "relative"), ("DATA_DIR", str(runtime.ROOT / "data")),
                           ("DATA_DIR", "/"), ("DATA_DIR", "/srv/../data"),
                           ("MYSQL_PORT", True), ("MYSQL_PORT", 0), ("MYSQL_HOST", "localhost/password=x"),
                           ("MYSQL_USER", "user\npassword"), ("MYSQL_DATABASE", "db?password=oops"),
                           ("KAFKA_BOOTSTRAP_SERVERS", "localhost:65536"),
                           ("SPRING_DATASOURCE_PASSWORD", "not permitted")):
            with self.subTest(key=key, value=value):
                self.values = {**original, key: value}
                with self.assertRaises(ValueError):
                    self.write_config()
        self.values = original
        link = self.root / "checkout-link"
        link.symlink_to(runtime.ROOT, target_is_directory=True)
        self.values["MYSQL_PASSWORD_FILE"] = str(link / "password")
        with self.assertRaises(ValueError):
            self.write_config()
        self.config_path.write_text('{"DATA_DIR":"/one","DATA_DIR":"/two"}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runtime.load_config(self.config_path)

    def test_environment_selects_jdk_without_inheriting_writer_or_test_settings(self):
        config = self.write_config()
        with patch.dict(os.environ, {"JAVA_HOME": "/wrong", "JAVA_TOOL_OPTIONS": "bad",
                                    "SPRING_PROFILES_ACTIVE": "fixture", "SPRING_DATASOURCE_PASSWORD": "secret",
                                    "SPRING_APPLICATION_JSON": "bad", "MYSQL_PWD": "secret",
                                    "CITIBIKE_KAFKA_IT": "true", "HADOOP_USER_NAME": "another-user"}):
            for jdk in ("8", "17"):
                env = runtime.environment(config, jdk)
                self.assertEqual(env["JAVA_HOME"], config[f"JAVA{jdk}_HOME"])
                self.assertTrue(env["PATH"].startswith(env["JAVA_HOME"] + "/bin" + os.pathsep))
                self.assertEqual(env["SPRING_PROFILES_ACTIVE"], "live")
                self.assertEqual(env["PYTHONPATH"], str(runtime.ROOT))
                self.assertEqual(env["HADOOP_MAPRED_HOME"], config["HADOOP_HOME"])
                for key in ("JAVA_TOOL_OPTIONS", "SPRING_DATASOURCE_PASSWORD", "MYSQL_PWD",
                            "SPRING_APPLICATION_JSON", "CITIBIKE_KAFKA_IT", "HADOOP_USER_NAME"):
                    self.assertNotIn(key, env)

    def test_service_commands_match_collector_parser_and_config(self):
        self.values.update(KAFKA_TOPIC="personal-live", KAFKA_BOOTSTRAP_SERVERS="broker:19092", BACKEND_PORT=18080, FRONTEND_PORT=15173)
        config = self.write_config()
        collector = runtime.service_command(config, "collector")
        args = gbfs_stream.parse_args(collector[3:])
        self.assertEqual(args.topic, "personal-live")
        self.assertEqual(args.bootstrap_servers, "broker:19092")
        self.assertEqual(args.output_dir, config["DATA_DIR"] + "/gbfs")
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.replay)
        self.assertEqual(args.snapshots, 0)
        self.assertEqual(args.producer_command, config["KAFKA_HOME"] + "/bin/kafka-console-producer.sh")
        backend = runtime.service_command(config, "backend")
        self.assertEqual(backend[0], config["JAVA17_HOME"] + "/bin/java")
        self.assertEqual(backend[-1], "--spring.profiles.active=live")
        self.assertIn("--strictPort", runtime.service_command(config, "frontend"))
        self.assertEqual(runtime.environment(config, "17")["CITIBIKE_API_TARGET"], "http://127.0.0.1:18080")

    def test_plan_does_not_read_password_connect_or_create_data(self):
        self.write_config()
        out = io.StringIO()
        with patch.object(runtime, "check_password_file", side_effect=AssertionError("password touched")), \
             patch.object(runtime.subprocess, "run", side_effect=AssertionError("process started")), \
             patch.object(runtime.socket, "socket", side_effect=AssertionError("network touched")), contextlib.redirect_stdout(out):
            for service in runtime.SERVICES:
                self.assertEqual(runtime.main(["--config", str(self.config_path), "plan", service]), 0)
        self.assertIn("?api=1", out.getvalue())
        self.assertFalse(Path(self.values["DATA_DIR"]).exists())

    def test_password_permissions_and_backend_injection_only_at_exec(self):
        config = self.write_config()
        Path(config["DATA_DIR"]).mkdir()
        password = Path(config["MYSQL_PASSWORD_FILE"])
        password.write_text("test-only-secret\n")
        password.chmod(0o644)
        with self.assertRaises(ValueError):
            runtime.check_password_file(config)
        password.chmod(0o600)
        self.assertEqual(runtime.check_password_file(config), password)
        with patch.object(runtime.os, "chdir"), patch.object(runtime.os, "execvpe") as execute, \
             patch.object(runtime.shutil, "which", return_value="test-executable"), \
             patch.object(Path, "is_file", return_value=True), patch.object(runtime.socket, "socket"):
            runtime.run_service(config, "backend")
        executable, command, env = execute.call_args.args
        self.assertEqual(env["SPRING_DATASOURCE_PASSWORD"], "test-only-secret")
        self.assertNotIn("test-only-secret", " ".join(command))
        self.assertEqual(executable, config["JAVA17_HOME"] + "/bin/java")
        password.write_text("line1\nline2")
        with patch.object(runtime.shutil, "which", return_value="test-executable"), self.assertRaisesRegex(ValueError, "one nonempty line"):
            runtime.run_service(config, "backend")

    def test_existing_instance_and_occupied_port_are_not_stopped(self):
        config = self.write_config()
        Path(config["DATA_DIR"]).mkdir()
        lock_path = Path(config["DATA_DIR"]) / ".team-runtime-collector.lock"
        with lock_path.open("w") as lock, patch.object(runtime.os, "execvpe") as execute, \
             patch.object(runtime.shutil, "which", return_value="test-executable"):
            runtime.fcntl.flock(lock, runtime.fcntl.LOCK_EX | runtime.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                runtime.run_service(config, "collector")
            execute.assert_not_called()
        with patch.object(runtime.socket, "socket") as socket, patch.object(runtime.os, "execvpe") as execute, \
             patch.object(runtime.shutil, "which", return_value="test-executable"), \
             patch.object(Path, "is_file", return_value=True):
            socket.return_value.__enter__.return_value.bind.side_effect = OSError("in use")
            with self.assertRaises(OSError):
                runtime.run_service(config, "frontend")
            socket.return_value.__enter__.return_value.bind.assert_called_once()
            execute.assert_not_called()

    def test_metadata_uses_published_live_batch_and_validates_bytes(self):
        config = self.write_config()
        folder = Path(config["DATA_DIR"]) / "gbfs"
        (folder / "metadata").mkdir(parents=True)
        raw = b'[{"station_id":"test-only"}]'
        version = hashlib.sha256(raw).hexdigest()
        path = folder / "metadata" / (version + ".json")
        path.write_bytes(raw)
        log = folder / "collection_log.json"
        batches = [{"status": "PUBLISHED", "data_origin": "GBFS_LIVE", "metadata_version": version},
                   {"status": "FAILED_SEND", "data_origin": "GBFS_LIVE", "metadata_version": "wrong"},
                   {"status": "PUBLISHED", "data_origin": "GBFS_REPLAY", "metadata_version": "wrong"}]
        log.write_text(json.dumps({"snapshots": batches}))
        self.assertEqual(runtime.metadata_file(config), path)
        path.write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            runtime.metadata_file(config)
        log.write_text(json.dumps({"snapshots": batches[1:]}))
        with self.assertRaisesRegex(ValueError, "no PUBLISHED"):
            runtime.metadata_file(config)

    def test_exec_runs_argv_without_shell_expansion_or_side_effects(self):
        self.write_config()
        marker = str(self.root / "must-not-exist")
        literal = "$(touch " + marker + ")"
        result = subprocess.run([sys.executable, "-m", "citibike.team_runtime", "--config", str(self.config_path),
                                 "exec", "--jdk", "8", "--", sys.executable, "-c",
                                 "import os,sys; print(os.environ['JAVA_HOME']); print(sys.argv[1])", literal],
                                cwd=runtime.ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [self.values["JAVA8_HOME"], literal])
        self.assertFalse(Path(marker).exists())

    def test_quickstart_cold_commands_parse_with_existing_clis(self):
        document = (runtime.ROOT / "docs/team-runtime.md").read_text().replace("\\\n", " ")
        seen = set()
        for line in document.splitlines():
            args = shlex.split(line) if line.startswith(("python3 -m citibike.", "spark-submit ")) else []
            if not args:
                continue
            if args[:2] == ["python3", "-m"]:
                name, args = args[2].split(".")[-1], args[3:]
            elif "$REPO_ROOT/citibike/offline.py" in args:
                name, args = "offline", args[args.index("$REPO_ROOT/citibike/offline.py") + 1:]
            else:
                continue
            if ">" in args:
                args = args[:args.index(">")]
            module = {"historical_source": historical_source, "historical_landing": historical_landing,
                      "offline": offline, "serving_export": serving_export}.get(name)
            if module is None:
                continue
            # argparse must see a numeric port after normal shell expansion.
            args = ["3306" if arg == "$MYSQL_PORT" else arg for arg in args]
            with patch.object(sys, "argv", [name, *args]):
                parsed = module.build_parser().parse_args(args) if hasattr(module, "build_parser") else module.parse_args()
            self.assertIsNotNone(parsed)
            seen.add(name)
        self.assertEqual(seen, {"historical_source", "historical_landing", "offline", "serving_export"})

    def test_metastore_accepts_classroom_mysql_and_thrift_but_not_implicit_derby(self):
        self.assertTrue(runtime.configured_metastore({"hive.metastore.uris": "thrift://localhost:9083"}))
        jdbc = {
            "javax.jdo.option.ConnectionURL": "jdbc:mysql://localhost/metastore",
            "javax.jdo.option.ConnectionDriverName": "com.mysql.cj.jdbc.Driver",
            "javax.jdo.option.ConnectionUserName": "hive",
        }
        self.assertTrue(runtime.configured_metastore(jdbc))
        self.assertFalse(runtime.configured_metastore({}))
        self.assertFalse(runtime.configured_metastore({"hive.metastore.uris": None}))
        self.assertFalse(runtime.configured_metastore({**jdbc, "javax.jdo.option.ConnectionURL": "jdbc:derby:metastore_db"}))
        self.assertFalse(runtime.configured_metastore({**jdbc, "javax.jdo.option.ConnectionDriverName": ""}))

    def test_doctor_only_executes_local_version_probes(self):
        config = self.write_config()
        output = io.StringIO()
        with patch.object(runtime, "check_password_file", side_effect=OSError("absent")), \
             patch.object(runtime.socket, "socket", side_effect=AssertionError("network touched")), \
             patch.object(runtime.subprocess, "run", side_effect=FileNotFoundError) as probe, \
             contextlib.redirect_stdout(output):
            self.assertEqual(runtime.doctor(config), 1)
        self.assertEqual([call.args[0] for call in probe.call_args_list], [
            [config["JAVA8_HOME"] + "/bin/java", "-version"],
            [config["JAVA17_HOME"] + "/bin/java", "-version"], ["node", "--version"]])
        self.assertIn("GAP", output.getvalue())
        self.assertFalse(Path(config["DATA_DIR"]).exists())

    def test_missing_frontend_dependencies_fail_before_port_or_process(self):
        config = self.write_config()
        with patch.object(runtime.shutil, "which", return_value="test-executable"), \
             patch.object(Path, "is_file", return_value=False), \
             patch.object(runtime.socket, "socket") as socket, \
             patch.object(runtime.os, "execvpe") as execute:
            with self.assertRaisesRegex(ValueError, "locked dependencies"):
                runtime.run_service(config, "frontend")
            socket.assert_not_called()
            execute.assert_not_called()

    def test_missing_collector_binary_fails_before_any_collection_or_lock(self):
        config = self.write_config()
        with patch.object(runtime.shutil, "which", return_value=None), \
             patch.object(runtime.os, "execvpe") as execute:
            with self.assertRaisesRegex(ValueError, "executable missing"):
                runtime.run_service(config, "collector")
            execute.assert_not_called()
        self.assertFalse(Path(config["DATA_DIR"]).exists())

    def test_service_lock_survives_exec_and_releases_on_exit(self):
        config = self.write_config()
        Path(config["DATA_DIR"]).mkdir()
        script = (
            "import sys; from citibike import team_runtime as r; "
            "c=r.load_config(r.Path(sys.argv[1])); "
            "r.shutil.which=lambda *a, **kw: a[0]; "
            "r.service_command=lambda *_: [sys.executable, '-c', 'print(\"ready\", flush=True); input()']; "
            "r.run_service(c, 'collector')"
        )
        process = subprocess.Popen([sys.executable, "-c", script, str(self.config_path)], cwd=runtime.ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with (Path(config["DATA_DIR"]) / ".team-runtime-collector.lock").open("r") as lock:
                with self.assertRaises(BlockingIOError):
                    runtime.fcntl.flock(lock, runtime.fcntl.LOCK_EX | runtime.fcntl.LOCK_NB)
                process.communicate("\n", timeout=5)
                self.assertEqual(process.returncode, 0)
                runtime.fcntl.flock(lock, runtime.fcntl.LOCK_EX | runtime.fcntl.LOCK_NB)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
