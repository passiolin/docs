# jmqtt 部署与运维(jmqtt-broker + jmqtt-admin)

> **版本基线**:jmqtt-broker / jmqtt-admin `main@028f439`(0.1.0-SNAPSHOT,自建镜像;上游 Spring Boot 2.7.18 + Java 21)。**本项目是我们自己的开源作品**,本文档集是第一份基于真机实测的运维手册。
> **实测环境**:PVE 虚机 jmqtt-1/2(10.10.12.137/138,4C/8G,`--net=host`),依赖复用本实验场的 Redis(137 本机)与 Kafka(10.10.12.128),2026-10-01。**实测覆盖**:镜像构建、单机部署、MQTT 协议功能矩阵(QoS1/2、保留、遗嘱、WS、认证)、双节点集群(跨节点路由经 Kafka 总线)、节点宕机自治、admin 构建部署与 **UI 真浏览器验证**。
> **重要**:实测发现两个上游缺陷(QoS2 下行断连、deploy 镜像 logs 目录权限),详见第 3 篇与第 1 篇——**在修复发布前,本文对应的部署命令均带绕过措施**。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机](1.安装部署-单机.md) | 构建、镜像、依赖、启动(含 logs 坑) | ✅ 已实测 |
| 2 | [参数与配置](2.参数与配置.md) | 六组参数、env 占位与覆盖机制 | ✅ 已实测 |
| 3 | [MQTT功能矩阵实测](3.MQTT功能矩阵实测.md) | QoS/保留/遗嘱/WS/认证 + QoS2 缺陷 | ✅ 已实测 |
| 4 | [集群部署](4.集群部署.md) | 双节点、跨节点路由、宕机自治 | ✅ 已实测 |
| 5 | [jmqtt-admin](5.jmqtt-admin.md) | 构建、Redis 覆盖、UI 验证 | ✅ 已实测 |
| 6 | [日常运维与排障](6.日常运维与排障.md) | 排障字典(实测错误集) | ✅ 已实测 |

## 阅读路径

从零搭:1 → 2 → 3(单机闭环)→ 4(集群)→ 5(管理台);接手在跑的:6 → 5。

## 资料索引(上游)

- jmqtt-broker:https://github.com/passiolin/jmqtt-broker(README + docs/configuration.md)
- jmqtt-admin:https://github.com/passiolin/jmqtt-admin
- 压测工具 jmqtt-bench:仓库内 `jmqtt-bench/`(未实测)
- MQTT 3.1.1 / 5.0 规范:http://docs.oasis-open.org/mqtt/mqtt/v3.1.1/

## 关联

- 依赖组件的部署与排障 → [../redis/](../redis/README.md)、[../kafka/](../kafka/README.md);
- EMQX(生产在用的商用同位替代)巡检见 inspect-middleware;jmqtt 的巡检项积累期后按新增组件流程补 references/jmqtt.md。

## 新增与修改

同 MySQL 篇纪律:先实测再入文;上游代码变更后本文要跟版复核(尤其 QoS2 缺陷修复后,第 3 篇矩阵需重跑)。
