# MySQL 部署与运维(mysql)

> **版本基线**:MySQL 8.4(实测 8.4.11,官方 docker 镜像 `mysql:8.4`)。**其他版本(5.7 / 8.0 / 9.x)一律不覆盖**,不做版本差异表,不写跨大版本升级。
> **实测环境**:PVE 虚机 3 台(4C / 8G / 100G 盘),Ubuntu 26.04 LTS,docker 29.1.3(Ubuntu 仓库 `docker.io` 包),2026-09-30。**所有命令与输出均来自该环境实测**,未经实测的内容会明确标注。
> **部署形态**:docker 单机 → 三台 docker 组 MGR → MySQL Router 访问层。参数以 [conf/](conf/) 三层模板管理(`common` + `role-standalone` / `role-mgr`)。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单机docker](1.安装部署-单机docker.md) | docker 安装与镜像加速、目录规划、启动命令与验证 | ✅ 已实测 |
| 2 | [参数基线与配置模板](2.参数基线与配置模板.md) | 六组参数、30 个决策参数、conf/ 三层模板、配置漂移纪律 | 待实测 |
| 3 | [账号与安全](3.账号与安全.md) | 认证插件、账号规划、最小权限(巡检/监控/备份) | ✅ 已实测 |
| 4 | [备份与恢复](4.备份与恢复.md) | 物理全备 + binlog 点恢复、恢复演练 | ✅ 已实测 |
| 5 | [MGR集群](5.MGR集群.md) | 三台 docker 组网、bootstrap 纪律、故障与仲裁 | ✅ 已实测 |
| 6 | [MySQLRouter](6.MySQLRouter.md) | 双 Router、读写口、故障切换行为 | 待实测 |
| 8 | [日常运维与排障](8.日常运维与排障.md) | 慢查询治理、大表 DDL、排障字典(实测错误集) | ✅ 已实测 |
| 9 | [监控与告警](9.监控与告警.md) | mysqld_exporter、按巡检项编号对齐的 PromQL 与告警 | ✅ 已实测 |

## 阅读路径

- **新环境从零搭**:1 → 2 → 3 → 4(单机闭环)→ 5 → 6(进集群);
- **只管单机**:1 → 2 → 3 → 4 → 8;
- **接手在跑的库**:8 → 4 → 2(对照基线找漂移)。

## 资料索引(官方与工具文档)

原则:**链接为主、摘录为辅、整本离线包不进仓库**。写文章需要引用官方内容时直接摘录进正文,注明"摘自 8.4 手册 ×节"+链接;如需离线手册(隔离网段),到下面入口下载 8.4 的 HTML/EPUB/PDF 归档到对象存储,这里记归档路径即可。

- MySQL 8.4 参考手册(在线):https://dev.mysql.com/doc/refman/8.4/en/
- 手册离线版下载入口(按版本):https://dev.mysql.com/doc/
- 高频深链:
  - 系统变量(默认值/动态性):https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html
  - Group Replication:https://dev.mysql.com/doc/refman/8.4/en/group-replication.html
  - 错误码检索:https://dev.mysql.com/doc/mysql-errors/8.4/en/
  - MySQL Router:https://dev.mysql.com/doc/mysql-router/8.4/en/
  - Percona XtraBackup 8.4:https://docs.percona.com/percona-xtrabackup/8.4/
  - Percona Toolkit(pt-query-digest 等):https://docs.percona.com/percona-toolkit/
- 离线包归档:(暂无,需要时补充对象存储路径)

## 关联

- **巡检**:命令面巡检项、阈值与处置见 [skills/巡检/inspect-middleware/references/mysql.md](../../skills/巡检/inspect-middleware/references/mysql.md)(M01~M11)。本文档集与巡检互补:巡检管"点状健康检查",部署文档管"怎么搭、怎么修";
- **监控**:第 9 篇以巡检项编号为对齐键,同一套阈值两种表达(命令面 / PromQL);
- 服务器层面(CPU/内存/磁盘/容器运行时)的巡检归 [inspect-server](../../skills/巡检/inspect-server/SKILL.md) 域。

## 新增与修改

1. 命令、参数、输出先在实验机(mysql-1/2/3,PVE 115-117)实测,再入文;文档头部标注实测版本与日期;
2. 参数基线的唯一真源是 [conf/](conf/) 模板与第 2 篇的决策参数表,巡检脚本、监控告警引用它们,不自行发明;
3. 新增文章 = 本文件索引表加行 + 按统一骨架写(头部实测 blockquote → 适用场景 → 正文 → 已知坑 → 互链)。
