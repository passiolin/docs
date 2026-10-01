# Kafka 部署与运维(kafka)

> **版本基线**:Kafka 4.3(实测 4.3.1,官方镜像 `apache/kafka:4.3.1`,**KRaft 模式,无 ZooKeeper 依赖**)。与巡检文档(4.3.1)同版本。老 README 的 2.8.2 + ZooKeeper 形态已被本文取代。
> **实测环境**:PVE 虚机 kafka-1/2(10.10.12.128/129,4C/4G,`--net=host`),2026-10-01。**实测覆盖**:KRaft 仲裁、topic/副本/ISR、生产消费与 offset/lag、杀 broker(Leader 迁移 + ISR 收缩 + 单副本可写 + 回归)、kafka_exporter(385 指标)。第三台(130)配置同型,实测当日其宿主 apt 锁未释放未能并入——三节点拓扑的参数与流程均已就绪,并入即生效。
> **参数基线**:[conf/](conf/) 双层 env(`kafka-common.env` 三台一致 + `gen-node-env.sh` 注入节点差异)。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机](1.安装部署-单机.md) | 单节点 KRaft(自举仲裁、RF=1) | ✅ 已实测 |
| 2 | [安装部署-KRaft集群](2.安装部署-KRaft集群.md) | CLUSTER_ID、env 双文件、uid 坑 | ✅ 已实测 |
| 3 | [参数基线与配置模板](3.参数基线与配置模板.md) | 六组参数、副本与 ISR 语义 | ✅ 已实测 |
| 4 | [数据面:topic 与收发](4.数据面topic与收发.md) | 建 topic、生产消费、lag | ✅ 已实测 |
| 5 | [容灾实测](5.容灾实测.md) | 杀 broker 全流程 | ✅ 已实测 |
| 6 | [日常运维与排障](6.日常运维与排障.md) | 排障字典(实测错误集) | ✅ 已实测 |
| 7 | [监控与告警](7.监控与告警.md) | kafka_exporter、K 编号对齐 | ✅ 已实测 |

## 阅读路径

从零搭:1(单机)→ 3 → 4(单机闭环)→ 2 → 5(进集群);接手在跑的:6 → 7。

## 资料索引

- 官方文档:https://kafka.apache.org/documentation/
- KRaft 模式:https://kafka.apache.org/documentation/#kraft
- 官方镜像用法:https://hub.docker.com/r/apache/kafka
- 配置项大全:https://kafka.apache.org/documentation/#brokerconfigs(链接为主,不整本入库,同 MySQL 篇纪律)

## 关联

- **巡检**:K01~K12 命令面/阈值见 [skills/巡检/inspect-middleware/references/kafka.md](../../skills/巡检/inspect-middleware/references/kafka.md);第 7 篇以 K 编号对齐指标面;
- 老 ZooKeeper 模式组件仍需 ZK → [../zookeeper/](../zookeeper/README.md);
- kafdrop(旧 UI)可继续用,另见其目录。

## 新增与修改

同 MySQL 篇纪律:先实测再入文;参数唯一真源是 [conf/kafka-common.env](conf/kafka-common.env)。
