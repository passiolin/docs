# Doris 部署与运维(doris)

> **版本基线**:Apache Doris 2.1.7(官方镜像 `apache/doris:fe-2.1.7` / `be-2.1.7`,2.1 LTS 线)。
> **实测环境**:PVE 虚机 doris-1/2/3(10.10.12.124/125/126,4C/8G/100G,Ubuntu 26.04,docker),2026-09-30。**3 FE + 3 BE 全链路实测**:部署、三大宿主前置、建表、Stream Load(10 万行)、杀 BE 副本容灾、FE 多数派与选举、在线扩容、metrics。
> **定位**:实时 OLAP 数仓(MPP 架构,FE 元数据/规划 + BE 存储/计算),MySQL 协议接入。生产参考:我们 StarRocks 同源架构的实验田。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机](1.安装部署-单机.md) | 1FE+1BE 同机、RF=1、实测坑 | ✅ 已实测 |
| 2 | [架构与部署-docker](2.架构与部署-docker.md) | FE/BE 分工、宿主前置、建群 | ✅ 已实测 |
| 3 | [数据导入与查询](3.数据导入与查询.md) | 建表模型、Stream Load、验证 | ✅ 已实测 |
| 4 | [高可用与扩缩容](4.高可用与扩缩容.md) | 副本容灾、FE 多数派、选举与冷启 | ✅ 已实测 |
| 5 | [监控与日常运维](5.监控与日常运维.md) | metrics 端点、排障字典 | ✅ 已实测 |

## 阅读路径

从零搭:1(单机)→ 3(导入验证)→ 2 → 4(集群 HA);接手在跑的:5 → 4。

## 资料索引

- 官方文档:https://doris.apache.org/docs/2.1/
- 官方 docker 镜像说明:https://hub.docker.com/r/apache/doris
- Stream Load:https://doris.apache.org/docs/2.1/data-operate/import/import-way/stream-load-manual
- FE 参数 / BE 参数手册:见官方 docs 的 Config 页(链接为主,不整本入库,同 MySQL 篇纪律)。

## 关联

- MySQL 协议接入侧的账号纪律沿用 [../mysql/3.账号与安全.md](../mysql/3.账号与安全.md) 的思路(Doris 权限模型另见第 3 篇);
- 巡检:Doris 未纳入巡检 10 组件,积累期后按"新增组件"流程补 references/doris.md。

## 新增与修改

同 MySQL 篇纪律:先实测再入文,头部标注实测版本与日期。
