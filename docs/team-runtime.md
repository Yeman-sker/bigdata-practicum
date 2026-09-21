# 每位组员独立运行真实数据

在**自己的 Ubuntu 22.04 WSL2 / OrbStack guest** 中克隆本仓库。每人使用自己的 HDFS、Hive metastore、Kafka、业务 MySQL、外部 DATA_DIR；不连接其他成员的数据库。以下闭环使用官方完整 **2025-01** 历史月和新采集的 **GBFS_LIVE**，不装载 seed，不使用 mock。

启动器只负责项目进程及每个子进程的环境；已有课堂组件是前置条件，不会安装、格式化、重建数据库或管理基础设施守护进程。冷数据准备使用已有 CLI，步骤明确分开。后续启动只需本文末尾的重启流程。

## 1. 前置条件与外部配置

按 [版本矩阵](../README.md#1-基础环境与版本矩阵) 准备 JDK 8 / 17、Hadoop 3.3.6、Hive 3.1.3、Spark 3.5.7、Sqoop 1.4.7、Kafka 3.9.2、MySQL 8；另需 Python **3.10/3.11**、Node **>=22.12**、npm、Maven、curl、Bash。Flume 是可选日志链路，不影响此业务闭环。已安装不等于已配置：

- HDFS/YARN 已由课堂环境完成初始化；不要再运行 NameNode/KRaft format。Hadoop XML 指向自己的集群，当前 Linux 用户有 RAW/warehouse 写权限。
- Hive metastore schema 已初始化。可沿用课堂的 MySQL-backed embedded metastore（显式 JDO ConnectionURL、driver、账号），也可使用已运行的 Thrift metastore；Hive 和 Spark 配置必须指向预期的持久元数据库，并核验客户端/schema 兼容性。不要为了启动器改造已正常工作的 metastore，也不能意外建立本地 Derby。Hive/Sqoop 的 `lib/` 已有兼容的 MySQL JDBC driver。doctor 只检查显式配置，真正兼容性仍以 Hive/Spark 读写为准。
- Kafka broker 已运行，advertised listener 从 Python/JVM 所在 guest 可达；下面创建自己的单分区 topic。示例为无认证课堂网络；启动器不支持 Kafka SASL/TLS 配置。
- MySQL 服务已运行；业务库与 metastore 库分离。自己创建的服务账号有业务库 DDL/DML 权限；Sqoop mapper 也必须能连接该 MySQL 地址。`localhost` 只适合 mapper 与 MySQL 同机。
- 为全月 CSV、HDFS 双副本、Spark 中间文件和 MySQL 表预留磁盘与内存。源 ZIP/CSV 各约 414 MB，处理空间远大于源文件；离线作业的内存需求取决于本机 Spark 配置，资源不足时先调整课堂资源，不改成抽样。

从仓库根目录运行。以下目录均是示例，换成自己可写的**绝对路径**。组件 homes 也可指向已有安装。配置不执行 shell，不展开 `$HOME` / `~`，不接受未知字段、内联密码或 URL 内凭据。

```bash
umask 077
mkdir -p "$HOME/.config/citibike"
cp config/team-runtime.example.json "$HOME/.config/citibike/runtime.json"
export RUNTIME_CONFIG="$HOME/.config/citibike/runtime.json"
# 编辑 JSON 中的绝对目录、自己的 Kafka/DB 地址、账号和端口。
# DATA_DIR 和 MYSQL_PASSWORD_FILE 必须在 checkout 外；DATA_DIR 使用专用持久目录。
${EDITOR:-vi} "$RUNTIME_CONFIG"
```

自行创建所配置的 DATA_DIR（建议权限 700）和凭据父目录（700）。用编辑器在 `MYSQL_PASSWORD_FILE` 写入服务账号的一行密码，`chmod 600`；不要把密码写到命令行、JSON、shell history 或日志，不启用 `set -x`。Sqoop 会在凭据父目录创建权限 600 的短期副本，故父目录也必须可写。配置文件也留在仓库外。

```bash
python3 -m citibike.team_runtime --help
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" doctor
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" plan backend
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" plan collector
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" plan frontend
```

`doctor` 返回 1 表示有缺口：检查本地可执行文件、双 JDK / Node 版本、XML、JDBC driver、构建产物、DATA_DIR 及凭据**文件权限**。它不读取密码、不连接任何服务、不检查真实 DB/schema/数据或网络；组件详细版本兼容性仍按矩阵核对。`plan` 不创建文件、不读取密码、不启动子进程，显示真实命令及 `?api=1` 地址。启动时仍需下文的服务健康检查。仅 `run backend` 向自己的 JVM 环境注入密码；不会打印环境或密码。

## 2. 构建与课堂基础设施健康检查

锁定依赖须预先可用，首次缺失时由环境负责人准备；启动器不代安装。以下 Maven/npm 是项目构建入口，在线缓存未准备时可能访问依赖源。

```bash
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 17 -- \
  mvn -f backend/pom.xml --batch-mode package
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 17 -- npm --prefix frontend ci
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 17 -- npm --prefix frontend test
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 17 -- npm --prefix frontend run build
```

`exec` 只运行你显式给出的命令，不注入密码；命令本身可能写数据。环境只继承基本用户信息与 PATH，不继承别的 Spring 配置、测试开关、JVM 参数或 `HADOOP_USER_NAME`。需要 simple-auth 身份时必须由环境负责人确认后显式传入，例如 `exec --jdk 8 -- env HADOOP_USER_NAME=自己的授权账号 hdfs dfs -ls /`。不要借用其他成员账号。

开一个 **JDK 8 准备终端**（仍从仓库根目录调用）：

```bash
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 8 -- bash --noprofile --norc
set -euo pipefail
set +x
# 子 shell 已有 JSON 中的 DATA_DIR、MYSQL_*、组件 homes、JAVA_HOME 和 REPO_ROOT。
hdfs dfs -ls /
yarn node -list
hive -S -e 'SHOW DATABASES;'
MYSQL_PWD="$(cat "$MYSQL_PASSWORD_FILE")" mysql --protocol=tcp \
  --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" -e 'SELECT VERSION();'
```

检查 Kafka 时另开 **JDK 17 准备终端**：

```bash
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" exec --jdk 17 -- bash --noprofile --norc
set -euo pipefail
kafka-topics.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" --list
# 仅冷启动创建新 topic；若同名已存在，本命令失败，先核对其归属/分区，不覆盖。
kafka-topics.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" \
  --create --topic "$KAFKA_TOPIC" --partitions 1 --replication-factor 1
kafka-topics.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" --describe --topic "$KAFKA_TOPIC"
```

必须恰好一分区。已有自己的 topic 在重启时只做 describe，保留原消费组和 offsets；不要重置 offsets。基础设施未启动时，使用自己的课堂服务管理方法启动；本指南不猜测 systemd unit、PID 或数据目录。

## 3. 冷启动：新业务库、完整月下载与校验

本节及第 4 节在 **JDK 8 准备终端**执行。只对自己新建的空业务库、未使用的历史分区操作。若已有这些数据，跳到重启流程，不能把冷启动当重置脚本。

管理员通过交互密码创建新业务库（没有 `IF NOT EXISTS`，同名即失败）；配置服务账号的权限由管理员处理，然后用服务账号装载 DDL：

```bash
mysql --protocol=tcp --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user=root --password \
  -e "CREATE DATABASE \`$MYSQL_DATABASE\` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;"
MYSQL_PWD="$(cat "$MYSQL_PASSWORD_FILE")" mysql --protocol=tcp \
  --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" "$MYSQL_DATABASE" < sql/serving.sql
```

此处不装载任何 fixture/seed。已有真实库的索引迁移由负责人按 [runbook](runbook.md#初始化启动停止) 单独执行，不作为启动副作用。

下载 [官方完整月对象](https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip)，用仓库已有来源证据校验字节数、SHA-256 和 ZIP CRC，再解压并逐行生成自己的 manifest。固定 2025-01 的三个 CSV 均需处理；不能用 sample CSV。这里 `mkdir` 不带 `-p`，防止覆盖上次准备目录。

```bash
mkdir "$DATA_DIR/historical"
curl -fL --retry 3 -o "$DATA_DIR/historical/month.zip.part" \
  'https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip'
python3 - <<'PY'
import json, os, zipfile
from pathlib import Path
from citibike.historical_source import sha256_file
reference = json.loads(Path('docs/source/citibike-202501-manifest.json').read_text())
download = Path(os.environ['DATA_DIR']) / 'historical/month.zip.part'
assert download.stat().st_size == reference['zip_size_bytes'], 'incomplete ZIP'
assert sha256_file(download) == reference['zip_sha256'], 'ZIP differs from verified official month'
with zipfile.ZipFile(download) as archive:
    assert archive.testzip() is None, 'ZIP CRC failure'
    assert sorted(archive.namelist()) == sorted(reference['zip_members']), 'ZIP members differ'
download.rename(download.with_name('202501-citibike-tripdata.zip'))
PY
python3 -m citibike.historical_source \
  --zip "$DATA_DIR/historical/202501-citibike-tripdata.zip" \
  --extract-dir "$DATA_DIR/historical/extracted" \
  --manifest "$DATA_DIR/historical/manifest.json" \
  --source-month 2025-01 \
  --source-url 'https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip'
python3 - <<'PY'
import json, os
from pathlib import Path
reference = json.loads(Path('docs/source/citibike-202501-manifest.json').read_text())
actual = json.loads((Path(os.environ['DATA_DIR']) / 'historical/manifest.json').read_text())
for key in ('source_month', 'zip_sha256', 'zip_size_bytes', 'csv_file_count', 'record_count'):
    assert actual[key] == reference[key], key
files = lambda m: sorted((f['filename'], f['size_bytes'], f['record_count']) for f in m['csv_files'])
assert files(actual) == files(reference), 'all three complete CSVs required'
print('PASS complete month:', actual['record_count'], 'rows')
PY
```

预期完整来源为 2,124,475 行。校验失败就停；官方对象若变化，先重新审核来源，不跳过 hash 或把旧 manifest 强行套到新数据。下载/解压中断保留目录调查，不自动删除。准备失败后重新执行时应选新 DATA_DIR 或人工确认仅清理自己的未完成 staging。

## 4. 落 HDFS RAW/ODS，再注册 Hive

现有 landing CLI 使用 `-put -f` / `-cp -f`；因此先用**不带 `-p` 的分区 mkdir**确认目标为新分区，拒绝重跑覆盖。父目录可已存在。任一步失败即停，不自动回收创建到一半的目录。

```bash
hdfs dfs -mkdir -p /raw/citibike/trips /warehouse/ods/ods_trip_raw
hdfs dfs -mkdir /raw/citibike/trips/year=2025
hdfs dfs -mkdir /warehouse/ods/ods_trip_raw/year=2025
hdfs dfs -mkdir /raw/citibike/trips/year=2025/month=01
hdfs dfs -mkdir /warehouse/ods/ods_trip_raw/year=2025/month=01
python3 -m citibike.historical_landing \
  --staging-dir "$DATA_DIR/historical/extracted" --source-month 2025-01 \
  --manifest "$DATA_DIR/historical/manifest.json" --dry-run
python3 -m citibike.historical_landing \
  --staging-dir "$DATA_DIR/historical/extracted" --source-month 2025-01 \
  --manifest "$DATA_DIR/historical/manifest.json" > "$DATA_DIR/historical/landing.json"
# 先人工核对 SHOW DATABASES 输出：冷启动 metastore 中不应已有 citibike_ods/citibike_dw。
# 这些 Hive 数据库名由现有 DDL 固定，不能与别人的项目共享同一 metastore namespace。
hive -S -e 'SHOW DATABASES;'
mkdir -p "$DATA_DIR/hive-local"
hive --hiveconf hive.exec.local.scratchdir="$DATA_DIR/hive-local" -f hive/ods/ods_trip_raw.sql
hive --hiveconf hive.exec.local.scratchdir="$DATA_DIR/hive-local" \
  --hiveconf source_year=2025 --hiveconf source_month=1 \
  -f hive/queries/verify_ods_trip_raw.sql
hdfs dfs -count /raw/citibike/trips/year=2025/month=01
hdfs dfs -count /warehouse/ods/ods_trip_raw/year=2025/month=01
```

门禁：RAW/ODS 各 3 文件、414,212,882 字节；Hive count 2,124,475、header 0、重复 ride_id 组 0。只有这些结果一致才继续。仓库 [HDFS/Hive handoff](hdfs-hive/README.md) 解释固定路径/类型；此启动器不改这些业务契约。

## 5. 启动 live backend、真实采集器，取得 metadata

各开一个普通终端，从仓库根运行（每个新终端先设置自己的 `RUNTIME_CONFIG`）。保持前台运行，backend 和 collector 必须使用同一配置、DATA_DIR、topic；同一业务库只允许一个 writer。不要同时用 Maven 再起一个 backend。

```bash
export RUNTIME_CONFIG="$HOME/.config/citibike/runtime.json"
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" run backend
```

```bash
export RUNTIME_CONFIG="$HOME/.config/citibike/runtime.json"
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" run collector
```

collector 默认从官方 GBFS discovery 请求实时 feed，每 60 秒采集一批，并用配置的 Kafka producer 发送。它先写 raw 和 metadata，broker 确认完整批次后写 `PUBLISHED / GBFS_LIVE`。失败日志在 `$DATA_DIR/gbfs/collection_log.json`，不要通过 replay 或手工 JSON 填补。

另一个普通终端执行：

```bash
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" metadata
```

该命令只从**已发布的 live collector 批次**取 metadata_version，校验该 hash 命名文件的真实字节，再输出绝对路径；没有成功批次即失败。不会选 fixtures，也不取目录里碰巧最新的文件。保存这个路径用于下一步，同一离线重试必须沿用它。

刚开始没有发布行时 API 503 正常；第一完整 GBFS 批次后 live 返回 200，但历史接口仍 503，有效站点预测为 `NO_BASELINE`。

## 6. 全月 offline → Sqoop → 下一 live 快照

回到 **JDK 8 准备终端**，设置 `RUNTIME_CONFIG` 后冻结本次输入路径。保持 collector/backend 持续运行：

```bash
export RUNTIME_CONFIG="$HOME/.config/citibike/runtime.json"
METADATA_FILE=$(python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" metadata)
printf '%s\n' "$METADATA_FILE" > "$DATA_DIR/historical/metadata-file.txt"
mkdir -p "$DATA_DIR/jobs"
cd "$DATA_DIR/jobs"
spark-submit --master 'local[2]' --driver-memory 4g \
  "$REPO_ROOT/citibike/offline.py" \
  --hive-table citibike_ods.ods_trip_raw --raw-root /raw/citibike/trips \
  --source-month 2025-01 --manifest "$DATA_DIR/historical/manifest.json" \
  --metadata "$METADATA_FILE" --output-root /warehouse \
  --register-warehouse --warehouse-ddl "$REPO_ROOT/hive/warehouse_v1.sql" \
  --evidence "$DATA_DIR/offline.json"
DATASET_ID=$(python3 -c 'import json,sys; e=json.load(open(sys.argv[1])); assert e["status"]=="PASS"; print(e["dataset_id"])' "$DATA_DIR/offline.json")
python3 -m citibike.serving_export \
  --dataset-id "$DATASET_ID" --hdfs-root /warehouse \
  --offline-evidence "$DATA_DIR/offline.json" \
  --password-file "$MYSQL_PASSWORD_FILE" --evidence "$DATA_DIR/export.json" \
  --connect "$SPRING_DATASOURCE_URL" \
  --host "$MYSQL_HOST" --port "$MYSQL_PORT" --database "$MYSQL_DATABASE" --username "$MYSQL_USER" \
  --ddl "$REPO_ROOT/sql/serving.sql"
```

`local[2]` 是真实 Spark/HDFS/Hive 全月处理，Sqoop 默认仍用 YARN。只有隔离单机诊断确需 mapper 本地运行时才显式加 `--local-mapreduce`；不能把它写成 YARN 已验收。`--output-root`、`--hdfs-root` 必须一致；隔离验证可同时改为新的 HDFS 路径。ODS 路径和 Hive namespace 仍固定。

offline 成功后写不可变 release，再注册 Hive aliases；重试使用保存的 metadata 路径、同一 manifest 和 output-root。Sqoop 清空自己的 load 表、导出并对账，最后事务替换业务库历史发布；这是**显式历史发布操作**，不是启动步骤。禁止并行发布，禁止指向有他人数据的库。发生“publication uncertain”时先核对 `historical_release`，不要盲目重跑或清库。详情见 [runbook](runbook.md) 和实际 [export CLI](../citibike/serving_export.py)。

等待 collector **下一完整新快照**被 backend 消费，预测才采用新历史基线。不要把刚发布历史但尚未处理新快照的 `NO_BASELINE` 误判为失败。

## 7. 前端和真实健康验收

新普通终端启动前端（开发模式必须带 `?api=1`，裸根地址是开发 fixture）：

```bash
export RUNTIME_CONFIG="$HOME/.config/citibike/runtime.json"
python3 -m citibike.team_runtime --config "$RUNTIME_CONFIG" run frontend
# 默认真实入口：http://127.0.0.1:5173/?api=1；自定义端口看 plan frontend。
```

前端代理自动指向配置的 backend 端口，两个进程都绑定 guest loopback。WSL/OrbStack 的宿主转发取决于本机设置；先在 guest 用 curl 验证。底图瓦片/字体还需要网络，独立于 API 数据链路。

在带配置环境的准备终端执行**只读**检查（不需要重新运行 doctor 来代替这些检查）：

```bash
curl --fail-with-body "http://127.0.0.1:$BACKEND_PORT/api/v1/history/availability" > "$DATA_DIR/availability.json"
curl --fail-with-body "http://127.0.0.1:$FRONTEND_PORT/api/v1/map?mode=live" > "$DATA_DIR/live.json"
python3 - <<'PY'
import datetime as dt, json, os
from pathlib import Path
d = Path(os.environ['DATA_DIR'])
offline, exported, availability, live = [json.loads((d / name).read_text()) for name in
    ('offline.json', 'export.json', 'availability.json', 'live.json')]
assert offline['status'] == exported['status'] == 'PASS'
assert offline['dataset_id'] == exported['dataset_id'] == availability['dataset_id'] == live['baseline_dataset_id']
assert availability['source_months'] == ['2025-01']
assert live['data_origin'] == 'GBFS_LIVE' and live['clock_mode'] == 'wall'
assert live['stations'], 'no stations'
assert dt.datetime.fromisoformat(live['expires_at_utc'].replace('Z', '+00:00')) > dt.datetime.now(dt.timezone.utc), 'stale snapshot'
print('PASS real month + next live batch:', live['snapshot_id'], live['metadata_version'])
print('Replay date/hour choices:', availability['dates'][:1])
PY
```

再从 availability 返回的日期/小时选择回放请求 `/api/v1/map?mode=replay&service_date=...&hour=...`，从真实 station_id 选择 `/api/v1/stations/{station_id}/history?day_of_week=...&service_date=...`。不要把 fixture 日期/站点硬套到真实月份。检查 offline/export 对账、MySQL `historical_release` 与 `live_release`、各服务表 dataset_id；完整数值门禁见 [runbook](runbook.md#五条-p0-验收路径)。HTTP 200 本身不能证明完整月对账成功。

在 **JDK 17 准备终端**观察消费进度（读 committed offset、log-end 和 lag）：

```bash
kafka-consumer-groups.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" \
  --group "$SPRING_KAFKA_CONSUMER_GROUP_ID" --describe
```

手动分区分配可能显示 “no active members”，不能据此判定服务停止。记录下一快照和 committed offset 推进，lag 应随处理收敛。

## 8. 后续停止、启动、重启

1. 停止自己的前台终端：**frontend → collector → backend**，各按 Ctrl-C；不运行 `pkill`、全局 stop 脚本或按未知 PID 杀进程。基础设施保持运行；确需停机时由课堂服务负责人只停自己启动的服务。
2. 保留 DATA_DIR（含全部 raw、metadata、collector 日志）、MySQL、HDFS release、Kafka topic 和同一消费组/offset。不能删 metadata 后只恢复 Kafka，也不能在重启时装载 seed、清库、重建 topic 或重置 offset。
3. 基础设施启动后重复只读 HDFS/YARN/Hive/MySQL/topic 检查；再依次 `run backend`、`run collector`、`run frontend`。不重复冷数据步骤或 Sqoop 发布。
4. 重做第 7 节检查；历史 dataset_id 不变，新 GBFS snapshot_id 和 as_of 向前，live 基线匹配历史发布。刚重启时旧快照可能已过期，等待下一完整批次。

启动器用 DATA_DIR 内的内核文件锁阻止同一个目录的重复同类进程，进程退出自动释放；占用端口直接失败，不停止占用者。它不能跨不同 DATA_DIR/机器检测同一业务库的第二个 writer，必须遵守每库一个 writer 约定。没有后台 daemon/PID 管理器；前台运行就是本工具的 start/stop 界面。锁文件可保留，勿在运行中删除。不要用 shell `&` 逃离前台生命周期。

## 开发验证边界

```bash
python3 -B -m unittest discover -s tests -p 'test_team_runtime.py' -v
```

单测验证配置拒绝、secret 不输出、实际 collector 参数、JDK/env 隔离、argv 不经 shell、端口/锁保护、metadata 来源/hash。它们不证明本机真实组件或完整数据已跑通。提交前还应跑仓库 Python、Maven、前端测试与构建；未安装的课堂组件和未执行的真实数据步骤必须如实记录。
