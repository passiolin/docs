# ZooKeeper 部署与运维(zookeeper)

> **版本基线**:ZooKeeper 3.9(实测 3.9.5,官方 docker 镜像 `zookeeper:3.9.5`)。与巡检文档(3.9.5 打包版)同版本;3.5+ 的配置体系一致。
> **实测环境**:PVE 虚机 zk-1/2/3(10.10.12.131/132/133,2C/2G/40G,Ubuntu 26.04,docker),2026-09-30。**三节点 ensemble 全链路实测**:部署、四字命令、znode 语义、杀 leader 切换、节点回归、mntr 指标。
> **定位**:分布式协调(元数据/选主/分布式锁)。**Kafka 4.x KRaft 已不需要它**——新部署前先想清楚是否真的需要自建 ZK。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机](1.安装部署-单机.md) | standalone、admin server 实测 | ✅ 已实测 |
| 2 | [安装部署-集群](2.安装部署-集群.md) | ensemble 搭建、ZOO_MY_ID | ✅ 已实测 |
| 3 | [参数基线与配置模板](3.参数基线与配置模板.md) | zoo.cfg 决策项、四字命令白名单 | ✅ 已实测 |
| 4 | [数据与命令面](4.数据与命令面.md) | znode CRUD、ephemeral、一致性 | ✅ 已实测 |
| 5 | [容灾与监控](5.容灾与监控.md) | 杀 leader 实测、mntr、排障字典 | ✅ 已实测 |

## 阅读路径

从零搭:1(单机)→ 3 → 4 → 2(集群)→ 5;接手在跑的:5 → 3。

## 资料索引

- 官方管理员文档:https://zookeeper.apache.org/doc/current/zookeeperAdmin.html
- 四字命令参考:https://zookeeper.apache.org/doc/current/zookeeperAdmin.html#sc_zkCommands
- 官方镜像:https://hub.docker.com/_/zookeeper

## 关联

- **巡检**:命令面巡检项 Z01~Z10、阈值与处置见 [skills/巡检/inspect-middleware/references/zookeeper.md](../../skills/巡检/inspect-middleware/references/zookeeper.md);本文与其互补(部署/排障 vs 点状巡检);
- Kafka(不再依赖 ZK 的 KRaft 模式)→ [../kafka/](../kafka/README.md)。

## 新增与修改

同 MySQL 篇纪律:先实测再入文,头部标注实测版本与日期;参数基线唯一真源是 [conf/zoo.cfg](conf/zoo.cfg)。
