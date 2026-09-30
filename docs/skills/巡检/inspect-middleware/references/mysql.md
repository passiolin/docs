# MySQL 巡检(inspect-middleware / mysql)

> **实测状态**:✅ 已实测 —— MySQL 8.4.11(Ubuntu 26.04 打包版,2026-09-30),单机模式;命令输出均已验证可解析。主从/集群相关项的输出格式以 8.4 语法(`SHOW REPLICA STATUS`)为准,8.0.22 之前为 `SHOW SLAVE STATUS`。

## 定位与依赖

- 本机 `mysqladmin` / `mysql` 客户端可用,巡检账号具备 PROCESS、REPLICATION CLIENT 权限(localhost root + auth_socket 即可);
- 指标面可选:mysqld_exporter 接入后 M02/M03/M04/M08 可换算 PromQL;
- 单机/主从/集群均可巡,集群(MGR/InnoDB Cluster)额外查 `performance_schema.replication_group_members`。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| M01 | 存活与版本 | 命令 | ping 不通即 P0 | 查进程/端口与 error log |
| M02 | 连接数利用率 | 命令 | ≥80% / ≥95% | 定位大客户端,评估调 max_connections |
| M03 | 活跃线程(Threads_running) | 命令 | ≥CPU 核数 / ≥2×核数 | SHOW PROCESSLIST 定位堆积来源 |
| M04 | 慢查询 | 命令 | 增速突增(P1);**慢日志未开启记 P2** | 先开 slow_query_log 再谈治理 |
| M05 | 异常连接(Aborted) | 命令 | 持续增长 P1 | 排查认证失败/连接未正确关闭 |
| M06 | 复制状态与延迟 | 命令 | 线程断 P1;延迟 ≥60s / ≥300s | 空输出=非从库(正常跳过) |
| M07 | binlog 与磁盘 | 命令 | binlog 空间失控增长 P2 | expire_logs_days/binlog 过期策略 |
| M08 | 缓冲池命中率 | 命令 | <95% P2 / <90% P1(样本充足时) | 评估 buffer pool 扩容 |
| M09 | 数据容量 | 命令 | 增速异常 P2(联动写满预测) | 大表归档/清理 |
| M10 | 错误日志 | 彙令 | 有 ERROR 级新增 P1 | 逐条甄别,锁等待/损坏类升 P0 |
| M11 | 关键配置基线 | 命令 | max_connections=151 默认值在生产 P2 | 按容量规划调整 |

## 检查命令明细

**M01 存活与版本**

```bash
mysqladmin ping                 # mysqld is alive
mysql -N -e "SELECT version();" # 8.4.11-0ubuntu0.26.04.1
```

**M02/M03/M05 连接与线程**

```bash
mysql -e "SHOW GLOBAL STATUS WHERE Variable_name IN
 ('Threads_connected','Threads_running','Max_used_connections','Aborted_connects','Aborted_clients');"
mysql -e "SHOW VARIABLES WHERE Variable_name IN ('max_connections');"
# 连接利用率 = Threads_connected / max_connections;Max_used_connections 是历史峰值,佐证是否长期贴顶
```

实测输出(Ubuntu 8.4 默认):`max_connections=151`,Threads_running 含后台线程,单机空载为 2。

**M04 慢查询**(实测:**Ubuntu 打包版默认 slow_query_log=OFF**,不开就无法统计——把"未开启"本身作为巡检结果)

```bash
mysql -e "SHOW VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time');"
mysql -e "SHOW GLOBAL STATUS LIKE 'Slow_queries';"   # 累计值,两次巡检差值 = 增速
```

**M06 复制状态**(8.4 语法;**单机输出为空属正常,判"非从库"跳过**)

```sql
SHOW REPLICA STATUS\G
-- 关注:Replica_IO_Running / Replica_SQL_Running = Yes
--       Seconds_Behind_Source(延迟秒数;NULL = 复制断开,P1)
```

**M07/M09 容量**

```bash
mysql -e "SHOW VARIABLES WHERE Variable_name IN ('log_bin','binlog_expire_logs_seconds');"
mysql -N -e "SELECT ROUND(SUM(data_length+index_length)/1024/1024,1) FROM information_schema.tables;"
```

**M08 缓冲池命中率**(空载小样本时无意义,累计读请求 >10 万再判)

```bash
mysql -e "SHOW GLOBAL STATUS WHERE Variable_name IN
 ('Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads');"
# 命中率 = 1 - reads/read_requests
```

**M10 错误日志**

```bash
mysql -N -e "SHOW VARIABLES LIKE 'log_error';"
grep -c "$(date +%Y-%m-%d)" <log_error 路径> 2>/dev/null   # 当日行数粗筛后人工/AI 甄别
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **M02 连接打满**:`SHOW PROCESSLIST` 找 Sleep 大户,确认后 `KILL <id>`(先核对业务归属);应急可临时调 `max_connections`,根治是连接池治理;
- **M04 慢查询**:开 `slow_query_log`、`long_query_time=1` 起步,用 `pt-query-digest` 聚类;勿在无索引大表上直接改写;
- **M06 复制断开/延迟**:先看 error log 报错(网络/主库 binlog 清理/大事务);延迟突增优先查从库单线程回放大事务(`SHOW PROCESSLIST` 的 SQL 线程状态);
- **M08 命中率低**:确认 buffer pool 是否远小于数据热集;调 `innodb_buffer_pool_size` 需评估主机内存(联动 inspect-server S03);
- **M10 见到 Table corruption / InnoDB: Database page corruption**:立即停止写入、留现场、按恢复流程处理,P0。
