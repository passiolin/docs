# sub2api 网关巡检(sub2api)

> - **对象**:sub2api 网关的 admin API,部署形态见 [部署相关/sub2api](../../部署相关/sub2api/README.md);巡检机 `<巡检机 IP>`(`<主机名>`)。
> - **节奏**:每天 09:00–18:00 每小时一轮,飞书卡片推送;三项内容——活跃账号额度、余额低于阈值的用户、最近一小时的报错。额度任一窗口 ≥99% 自动停用,回落到 90% 以下自动恢复,动作写进同一张卡片。
> - **自包含**:巡检口径、admin API 字段、巡检脚本、systemd 定时、飞书机器人配置、admin skill 都在本目录。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [巡检项与接口](1.巡检项与接口.md) | 三项巡检的判定口径、admin API 路径与字段、卡片结构、已知坑、接口实测记录 | ✅ 实测 |
| 2 | [部署与飞书通知](2.部署与飞书通知.md) | skill 安装、脚本部署、env 与权限、systemd timer、飞书机器人、排障 | ✅ 已部署 |

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [sub2api_patrol.py](sub2api_patrol.py) | 单文件巡检:按平台采集账号额度(5h/7d/月 已用)、用户余额、近一小时报错 → 飞书卡片;`--dry-run` 只打印,`--json` 输出卡片原文 |
| [conf/sub2api-patrol.env.example](conf/sub2api-patrol.env.example) | 环境变量模板;真实 Key 与 webhook 只放目标机 `/etc/sub2api-patrol/`,权限 600 |
| [conf/sub2api-patrol.service](conf/sub2api-patrol.service) | oneshot unit,`EnvironmentFile` 指向 env |
| [conf/sub2api-patrol.timer](conf/sub2api-patrol.timer) | 09:00–18:00 每小时,日历带 `Asia/Shanghai` |
| [skills/sub2api-admin](skills/sub2api-admin/SKILL.md) | admin API 的 CLI 封装,供人工与 AI 做交互式排查;巡检脚本不依赖它 |

## 阅读路径

- **首次部署**:第 2 篇的「安装 admin skill」→「部署脚本」→「飞书机器人」→「定时」四节;
- **手动跑一轮**:第 2 篇「部署脚本」第 3 步的 `--dry-run` 命令;
- **改口径或加指标**:第 1 篇「三项巡检」与「接口清单」;
- **卡片没到 / 采集失败**:第 2 篇「排障」表;
- **对不上字段**:第 1 篇「接口实测记录」与「已知坑」。

## 这个目录在讲什么

sub2api 后台有三个页面:账号管理、用户管理、运维监控。每天真正要看的是三件事——哪些上游账号在跑、各自 5h/7d/月 还剩多少额度;谁快没钱了;过去一小时有没有报错。网页看要人守着点,这里把它固化成一轮采集、一张卡片:脚本按 admin API 拉三份数据,按固定口径算好,推到飞书群里。

口径与脚本分开写:第 1 篇只讲"怎么算"(活跃账号、低额度、报错聚合的判定和字段),第 2 篇只讲"怎么跑"(skill 安装、env、systemd、机器人)。改阈值改 env,改算法改脚本,两件事互不牵连。
