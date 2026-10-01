# Elasticsearch 部署与运维(elasticsearch)

> **版本基线**:Elasticsearch 8.19(实测 8.19.14,官方镜像 `elasticsearch:8.19.14`)。与巡检文档(8.19.14 deb)同版本;老 README 的 7.17 私有镜像形态已被本文取代。
> **实测环境**:PVE 虚机 es-1/2/3(10.10.12.134/135/136,4C/8G,`--net=host`),2026-10-01。**三节点集群全链路实测**:部署(含 config 全量提取的坑)、索引/检索/分片分布、快照备份、杀节点容灾、集群健康 API。**安全基线:内网明文(xpack.security.enabled=false)**——开启 TLS/认证的路径与代价见第 2 篇"安全形态"。
> **参数基线**:[conf/](conf/)(`elasticsearch.yml` 共享 + `gen-node-yml.sh` 注入 node.name + `jvm.options`)。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机](1.安装部署-单机.md) | 安全默认开形态、三个实测坑 | ✅ 已实测 |
| 2 | [安装部署-三节点](2.安装部署-三节点.md) | 前置、config 提取、安全形态 | ✅ 已实测 |
| 3 | [参数基线与配置模板](3.参数基线与配置模板.md) | yml 决策项、JVM | ✅ 已实测 |
| 4 | [索引与检索](4.索引与检索.md) | 建索引/写入/查询/分片分布 | ✅ 已实测 |
| 5 | [快照备份](5.快照备份.md) | path.repo 前置、快照实测 | ✅ 已实测 |
| 6 | [容灾实测](6.容灾实测.md) | 杀节点 yellow/检索可用/回归 | ✅ 已实测 |
| 7 | [日常运维与排障](7.日常运维与排障.md) | 排障字典(实测错误集) | ✅ 已实测 |
| 8 | [监控与告警](8.监控与告警.md) | 集群健康 API 面板 | ✅ 已实测 |

## 阅读路径

从零搭:1(单机)→ 3 → 4 → 5(单机闭环)→ 2 → 6(集群);接手在跑的:7 → 8 → 5(快照是否在做)。

## 资料索引

- 官方文档(8.19):https://www.elastic.co/guide/en/elasticsearch/reference/8.19/
- 快照恢复:https://www.elastic.co/guide/en/elasticsearch/reference/8.19/snapshot-restore.html
- 安全(TLS/认证):https://www.elastic.co/guide/en/elasticsearch/reference/8.19/security.html

## 关联

- **巡检**:ES01~ES12 命令面/阈值见 [skills/巡检/inspect-middleware/references/elasticsearch.md](../../skills/巡检/inspect-middleware/references/elasticsearch.md)(其"版本差异与已知坑"一节在 deb 环境实测,与 docker 形态互补);第 8 篇以 ES 编号对齐;
- ELK 链路的日志侧(Filebeat)→ [../filebeat/](../filebeat/README.md)。

## 新增与修改

同 MySQL 篇纪律:先实测再入文;参数唯一真源是 [conf/](conf/) 模板。
