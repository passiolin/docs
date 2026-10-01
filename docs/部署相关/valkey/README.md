# Valkey 部署与选型(valkey)

> **版本基线**:Valkey 8.1(实测 8.1.10,官方 docker 镜像 `valkey/valkey:8.1`;其对外协议兼容层报告 `redis_version:7.2.4`)。
> **实测环境**:PVE 虚机 valkey-1/2/3(10.10.12.121/122/123,2C/4G,Ubuntu 26.04,docker),2026-09-30,另借 redis-1(118,Redis 8.0.6)做跨软件互操作。
> **定位**:Valkey 是 Redis 2024 年改许可证后由 Linux 基金会托管的开源(BSD)分叉。本文档集**不重复 Redis 通用运维**(单机/哨兵/Cluster/监控全套见 [../redis/](../redis/README.md)),只覆盖:部署差异、**与 Redis 的兼容性实测矩阵**、以及**迁移路径实测**。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-docker](1.安装部署-docker.md) | valkey-server/valkey-cli、与 redis 容器的差异 | ✅ 已实测 |
| 2 | [与Redis兼容性实测](2.与Redis兼容性实测.md) | 客户端/ACL/RESP3/RDB 互导矩阵 | ✅ 已实测 |
| 3 | [主从复制与迁移路径](3.主从复制与迁移路径.md) | 自身复制、跨软件复制实测、迁移三路线 | ✅ 已实测 |

## 一分钟选型结论(实测依据见第 2/3 篇)

- **新缓存/队列场景**:Redis 8 已回归开源(AGPLv3)且 Valkey 协议兼容 Redis 7.2——两者运维心智一致,按团队偏好选;要纯 BSD 与社区治理选 Valkey,要 Redis 8 新特性(新 hash、客户端缓存增强等)选 Redis;
- **存量 Redis 8 想换 Valkey**:**没有搬运工捷径**——实测 RDB 快照与主从复制两条"物理"通道都被 RDB v12 挡死,只能走逻辑迁移(第 3 篇);
- **存量 Redis ≤7.4 换 Valkey**:物理通道可用(RDB v11 及以下),复制法/快照法均可。

## 关联

- Redis 通用运维全套(参数/ACL/持久化/哨兵/Cluster/监控)→ [../redis/](../redis/README.md),Valkey 同样适用;
- 巡检:巡检 redis.md 的 R01~R13 命令面(valkey-cli 同语法)对 Valkey 直接可用,`INFO` 字段一致(实测)。

## 资料索引

- Valkey 官方文档:https://valkey.io/topics/overview/
- Valkey 镜像:https://hub.docker.com/r/valkey/valkey
- Valkey 与 Redis 兼容性官方说明:https://valkey.io/topics/compatibility/
