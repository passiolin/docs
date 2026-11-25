# 云成本监控(cost-job)

> - **覆盖对象**:阿里云 BSS OpenAPI 与 AWS Cost Explorer 的账单拉取,日报、月报、突增告警三类报表。
> - **技术栈**:Python 3 标准库(零第三方依赖)、Linux cron、飞书群机器人卡片;运行在内网一台 Linux 机器。
> - **文档性质**:设计说明与部署记录;腾讯云、观测云尚未接入。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [多云成本监控设计与部署](1.多云成本监控设计与部署.md) | 目录与职责、两云数据源与权限、突增判定、缓存与调度、部署步骤、排障记录 | 已落地 |

## 代码与配置

| 文件 | 说明 |
| --- | --- |
| [conf/aliyun.env.example](conf/aliyun.env.example) | 阿里云凭据模板,占位值 |
| [conf/aws.env.example](conf/aws.env.example) | AWS 凭据模板,占位值 |
| [script/cost_common.py](script/cost_common.py) | 公共层:HTTP、重试、月份窗口、飞书发送 |
| [script/aliyun_cost.py](script/aliyun_cost.py) / [script/aliyun_report.py](script/aliyun_report.py) | 阿里云取数与日报/月报/突增 |
| [script/aws_cost.py](script/aws_cost.py) / [script/aws_report.py](script/aws_report.py) | AWS 取数与日报/月报/突增 |
| [script/cron-run.sh](script/cron-run.sh) / [script/cron-install.sh](script/cron-install.sh) | cron 调度壳与安装 |

以上为脱敏快照:不含 AccessKey、Secret、webhook 与账号标识,项目名以 `cost-job` 代称。文件为文档展示平铺,还原运行时:`script/cost_common.py` → `common/`,四个取数与报表脚本 → `<云>/script/`,两个 cron 脚本 → 仓库根;凭据模板 `conf/aliyun.env.example` → `aliyun/conf/env.example`、`conf/aws.env.example` → `aws/conf/env.example`。`tests/` 未随快照提供。

## 阅读路径

- 接手这套任务:1 的「目录与职责」→「部署步骤」,先跑通 dry-run 再动 cron;
- 要改突增规则:1 的「突增判定」与「配置项」,阈值与单位随云不同;
- 遇到接口报错:先看文末的「排障记录」,常见坑都在表里。

## 这个目录在讲什么

日报、月报、红色突增卡是这套任务的三个产出,分别回答昨天花了多少、这个月累计多少、哪一项突然涨了。阿里云与 AWS 各一套适配器,目录同构,新增第四朵云只需要加一个目录;两云的接口口径、突增判定与 cron 部署在第 1 篇。
