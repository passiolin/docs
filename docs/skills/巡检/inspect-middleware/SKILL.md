---
name: inspect-middleware
description: Use when 巡检中间件健康状况:MySQL、Redis、Kafka、ZooKeeper、Elasticsearch、Nginx、Nacos、EMQX、Filebeat、Jenkins 的存活、连接与容量、复制/副本、持久化、消费滞后、集群健康等;也为编写中间件巡检脚本、解读中间件异常指标、给出处置建议提供权威的巡检项、命令、PromQL 与阈值。用户提到"中间件巡检""Kafka lag""Redis 内存""MySQL 主从延迟""ES 集群健康"等均适用。指标面走各组件 exporter(可选),命令面走组件原生 CLI/API——每个中间件一份独立文档,见 references/。
---

# 中间件巡检(inspect-middleware)

## 定位与范围

- **覆盖 10 类中间件**:MySQL、Redis、Kafka、ZooKeeper、Elasticsearch、Nginx、Nacos、EMQX、Filebeat、Jenkins;
- **边界**:中间件所在操作系统层面(CPU/磁盘/网络/日志轮转)归 inspect-server,本域只管**组件自身**的健康;业务接口拨测归 inspect-service;容器化部署时进程编排状态(K8s)归 inspect-service;
- **每个中间件一份独立文档**(不堆在一起),本文件是总入口:通用约定 + 索引。

## 依赖与前置

1. 各组件管理端口/CLI 在巡检机可达,巡检账号具备只读查询权限(无则标注数据缺失,不允许静默跳过);
2. 命令面优先(组件原生 CLI/API,零额外部署);指标面可选(接入对应 exporter 后,命令项可换算为 PromQL);
3. 版本敏感:各文档头部标注**实测版本**;命令在别的版本上执行前先核对该文档"版本差异"小节;
4. 盲区命令一律 `timeout` 包装(与 inspect-server 同一条纪律)。

## 巡检项总览

| 组件 | 实测版本(2026-09-30,Ubuntu 26.04) | 巡检项 | 文档 |
| --- | --- | --- | --- |
| MySQL | 8.4.11 | 11 项 | [references/mysql.md](references/mysql.md) |
| Redis | 8.0.5 | 13 项 | [references/redis.md](references/redis.md) |
| Kafka | 4.3.1(KRaft) | 12 项 | [references/kafka.md](references/kafka.md) |
| ZooKeeper | 3.9.5 | 10 项 | [references/zookeeper.md](references/zookeeper.md) |
| Elasticsearch | 8.19.14 | 12 项 | [references/elasticsearch.md](references/elasticsearch.md) |
| Nginx | 1.28.3 | 10 项 | [references/nginx.md](references/nginx.md) |
| Nacos | 2.5.1 | 8 项 | [references/nacos.md](references/nacos.md) |
| EMQX | 5.8.6 | 8 项 | [references/emqx.md](references/emqx.md) |
| Filebeat | 8.19.14 | 8 项 | [references/filebeat.md](references/filebeat.md) |
| Jenkins | 2.5xx LTS | 8 项 | [references/jenkins.md](references/jenkins.md) |

合计 **100 项**;各组件已知坑见各文档"版本差异与已知坑"小节。

## 使用方式

- **AI 直接执行巡检**:按各组件文档的"巡检项清单"逐项执行,按下方"报告要求"产出报告;
- **AI 编写/维护巡检脚本**:巡检项、命令、阈值、分级**一律以各组件文档为准**,不得自行发明;新增项先补文档再同步脚本;
- **处置纪律**:各文档的处置内容为**初步参考**,未经本环境演练;任何处置动作必须等运维负责人明确指示后才启动(授权红线同 [inspect-server/references/fixes.md](../inspect-server/references/fixes.md) 开头);
- **阈值覆盖**:默认阈值为通用基线,核心生产实例可覆盖,须注明原因与生效范围。

## 报告要求

- 沿用 inspect-server 的报告约定:异常按级别置顶、附当前值/命中阈值/处置建议、豁免单独列表、`inspect-middleware-YYYYMMDD.md` 命名;
- 编号使用各组件前缀(M/R/K/Z/E/NG/NC/MQ/FB/J + 两位序号),跨组件去重不必,域内唯一即可;
- 同一组件多实例时按实例分节(实例标识 = 连接串或集群名+节点)。

## 新增与修改

1. 先改对应组件文档(清单表、命令明细、版本差异),再同步实施层规则;
2. 新增项编号在对应前缀内顺延,不复用废弃编号;
3. 新增组件 = 新建 `references/<组件>.md` + 本文件索引表加行。
