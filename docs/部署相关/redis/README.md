# Redis 部署与运维(redis)

> **版本基线**:Redis 8.0(实测 8.0.6,官方 docker 镜像 `redis:8.0`)。与巡检文档(实测 8.0.5 打包版)同一大版本;不覆盖 7.x 及以下差异。
> **实测环境**:PVE 虚机 redis-1/2/3(10.10.12.118/119/127,2C/4G/60G,Ubuntu 26.04,docker 29.1.3),2026-09-30。**所有命令与输出均来自实测**:单机、ACL、持久化崩溃恢复、主从+哨兵(故障切换 18 秒)、三主三从 Cluster(杀主接管)、redis_exporter。
> **参数基线**:以 [conf/](conf/) 分层模板管理(common + standalone/replica/sentinel/cluster 四种角色)。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机docker](1.安装部署-单机docker.md) | 容器启动、验证清单 | ✅ 已实测 |
| 2 | [参数基线与配置模板](2.参数基线与配置模板.md) | 六组参数、动态修改语义、漂移纪律 | ✅ 已实测 |
| 3 | [账号与安全(ACL)](3.账号与安全.md) | requirepass、ACL 用户、最小权限实测 | ✅ 已实测 |
| 4 | [持久化与备份恢复](4.持久化与备份恢复.md) | RDB/AOF、kill -9 恢复实测 | ✅ 已实测 |
| 5 | [主从与哨兵](5.主从与哨兵.md) | 复制、哨兵、故障切换计时 | ✅ 已实测 |
| 6 | [Cluster集群](6.Cluster集群.md) | 三主三从、MOVED、杀主接管 | ✅ 已实测 |
| 8 | [日常运维与排障](8.日常运维与排障.md) | 排障字典(实测错误集)、大 key 扫描 | ✅ 已实测 |
| 9 | [监控与告警](9.监控与告警.md) | redis_exporter、按巡检 R 编号对齐 | ✅ 已实测 |

## 阅读路径

- **从零搭**:1 → 2 → 3 → 4(单机闭环);
- **要高可用**:5(主从+哨兵,首选);**要水平扩展/多分片**:6;
- **接手在跑的库**:8 → 9。

## 架构选型(实测后的一句结论)

- 数据量单机可扛、写多读少:主从+哨兵(第 5 篇),部署与运维都最轻;
- 数据量/写入超过单机内存:Cluster(第 6 篇),代价是多 key 操作受限、客户端要支持集群协议;
- Valkey(Redis 的 BSD 开源分叉)的兼容性与迁移实测见 [../valkey/](../valkey/README.md)。

## 资料索引

- Redis 官方文档:https://redis.io/docs/latest/
- Redis 配置示例(redis.conf 全量注释版):https://redis.io/docs/latest/operate/oss_and_stack/management/config/
- 哨兵文档:https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/
- Cluster 规范:https://redis.io/docs/latest/operate/oss_and_stack/management/scaling/
- 离线包:Redis 无整本手册形态,按主题页归档即可。

## 关联

- **巡检**:命令面巡检项 R01~R13、阈值与处置见 [skills/巡检/inspect-middleware/references/redis.md](../../skills/巡检/inspect-middleware/references/redis.md);第 9 篇以 R 编号对齐指标面;
- 宿主机/容器层巡检归 [inspect-server](../../skills/巡检/inspect-server/SKILL.md)。

## 新增与修改

1. 命令、参数、输出先在实验机实测再入文,头部标注实测版本与日期;
2. 参数基线唯一真源是 [conf/](conf/) 模板与第 2 篇决策表;
3. 新增文章 = 本文件索引表加行 + 统一骨架(实测 blockquote → 场景 → 正文 → 已知坑 → 互链)。
