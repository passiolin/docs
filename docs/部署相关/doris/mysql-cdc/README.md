# MySQL Binlog 同步 Doris(实验子系列)

> - **版本基线**:MySQL 8.4.13、Doris 4.0.8、Flink 1.20.1、Flink CDC 3.7.0-1.20、MySQL Router 8.4.13;Ubuntu 26.04 LTS。
> - **实验环境**:PVE 10.10.10.44 上 11 台基线虚机(10.10.12.121–131),2026-11-12;多实例/多库验证再加 1 台 132(共 12 台,4C/8G 为主)。独立于主系列 2026-09 的 Doris 2.1.7 docker 环境;IP 有复用,按文章里的角色表区分。
> - **验证范围**:单机 MySQL、MGR+Router、同实例多库、多实例四条链路;全量快照、增量 DML、列级/表级 DDL、MGR 主库故障切换、百万行增量吞吐、Prometheus/Grafana 监控。
> - **结论**:四条 pipeline 的数据与 DDL 均能落到 Doris;MGR 主库切换时 Flink 作业保持 RUNNING,切换前后写入无丢失;两个实例同叫 `app_db` 的库通过 route 隔离,互不覆盖;300 万行增量同步期间端到端心跳延迟峰值 0.987s;同数据量下 Doris 的聚合/JOIN 查询比 MySQL(4G buffer pool)快 8–241 倍(第 2 篇给出查询与口径)。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [方案与部署](1.方案与部署.md) | 选型对比、11 台虚机拓扑、MySQL 单机/MGR+Router、Doris FE+BE、Flink CDC 3.7 部署 | ✅ 已实测 |
| 2 | [变更与故障验证](2.变更与故障验证.md) | DML/DDL 清单、MGR 主库切换、300 万行增量同步、MySQL/Doris 查询对比 | ✅ 已实测 |
| 3 | [监控与告警](3.监控与告警.md) | 心跳端到端延迟、Flink/checkpoint、binlog、MGR、Router、Doris 指标与告警 Runbook | ✅ 已实测 |
| 4 | [多实例与多库扩展](4.多实例与多库扩展.md) | 同实例多库、独立 132 实例、route 命名空间、4 条 pipeline 与监控扩展 | ✅ 已实测 |

## 阅读路径

- 从零搭:1(部署)→ 2(验证)→ 3(监控);
- 只关心“能不能同步、DDL 怎么办”:2 的 DML/DDL 清单 + 边界;
- 关心“主库挂了怎么办”:2 的 MGR 切换小节;
- 接手运行中的链路:3 的指标表 + 告警 Runbook;
- 多源接入:4 的 route/命名/资源规则。

## 配置与脚本

| 路径 | 用途 |
| --- | --- |
| [conf/mysql/zz-lab.cnf](conf/mysql/zz-lab.cnf) | 单机 MySQL 的 ROW binlog + GTID |
| [conf/mysql/app_schema.sql](conf/mysql/app_schema.sql) | 业务表与心跳表 DDL |
| [conf/mysql/multi_source_schema.sql](conf/mysql/multi_source_schema.sql) | 多库/多实例验证的最小 schema |
| [conf/mysql/zz-mgr.cnf](conf/mysql/zz-mgr.cnf) | MGR 节点模板(替换 server-id / IP) |
| [conf/doris/fe.conf.lab](conf/doris/fe.conf.lab) / [be.conf.lab](conf/doris/be.conf.lab) | Doris FE/BE 追加配置 |
| [conf/flink/config.yaml.lab](conf/flink/config.yaml.lab) | Flink checkpoint / slot / REST 绑定 |
| [conf/flink-cdc/](conf/flink-cdc/) | 四条 pipeline:single / mgr / multi-db / inst-b |
| [conf/prometheus/prometheus.yml](conf/prometheus/prometheus.yml) / [doris-sync-rules.yml](conf/prometheus/doris-sync-rules.yml) | 抓取配置与 10 条告警规则 |
| [conf/grafana/doris-sync-dashboard.json](conf/grafana/doris-sync-dashboard.json) | Grafana 仪表盘(9 个面板) |
| [conf/exporter/sync_exporter.py](conf/exporter/sync_exporter.py) | 同步状态 exporter(9273/metrics) |
| [conf/exporter/mgr_heartbeat.sh](conf/exporter/mgr_heartbeat.sh) | MGR 外部心跳写入(替代 MySQL EVENT) |
| [conf/systemd/](conf/systemd/) | exporter 与心跳 writer 的 systemd unit |

密码在模板里用 `<CDC_PASSWORD>` / `<MGR_ADMIN_PASSWORD>` / `<APP_PASSWORD>` 占位;exporter 从 `/etc/doris-sync-exporter.env` 读取。

## 与主系列的衔接

- Doris 的 FE/BE 架构、表模型、Stream Load、监控指标见主系列 [README](../README.md) 与 [第 3 篇](../3.数据导入与查询.md)、[第 5 篇](../5.监控与日常运维.md);
- MySQL 的账号最小权限、MGR 组网与 Router 概念见 [MySQL 账号与安全](../../mysql/3.账号与安全.md)、[MySQL MGR 集群](../../mysql/5.MGR集群.md)、[MySQL Router](../../mysql/6.MySQLRouter.md);
- 本子系列用原生 apt 包而非 docker,与主系列 mysql 篇的 docker 形态不同,相同的概念不再展开。
