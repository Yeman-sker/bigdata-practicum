# 课堂版本的兼容性记录

只读取和处理本次涉及的组件。以下是既有课堂环境的修复经验，先检查实际版本与错误，不对正常环境反复执行替换。

## WSL2 systemd

需要服务管理但 PID 1 不是 systemd 时，检查 `/etc/wsl.conf` 的 `[boot]` 中是否有 `systemd=true`。生效通常需要重启发行版；先确认运行任务和已有重启授权，避免中断其他作业。

## Hive / Hadoop Guava

Hive 3.1.3 自带 Guava 19，而课堂 Hadoop 3.3.6 使用 Guava 27；冲突可能表现为 `NoSuchMethodError: Preconditions.checkArgument`。核验 jar 后备份 Hive 旧 jar 到 lib 外，并使用 Hadoop common 下匹配的 `guava-27.0-jre.jar`，不要让两版同时留在类路径上。

## MySQL Metastore

课堂基准中新建 Hive `metastore` 使用 `latin1`，业务库如 `traveldb` 使用 `utf8mb4`。若出现组合索引长度错误，检查实际字符集、索引和存储配置。不要为套用基准重建已有库或转换已有业务数据。

## Sqoop / Hadoop 3

Sqoop 1.4.7 在 Hadoop 3.x 下可能缺少 `org.apache.commons.lang.StringUtils`。出现该错误时检查 `/opt/sqoop/lib/` 的 `commons-lang-2.6.jar`，补齐缺失依赖，不重复堆放不同版本。
