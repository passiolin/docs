# 无线 AP 守护(ap-guard)

> - **对象**:集客 AC 控制台(管理口 `http://<AC_IP>:8080`),下挂 3 台 N3000 AP(ap-1 / ap-2 / ap-3),模板 `gsjh`(id `default`)。
> - **上线**:2026-11-23 起 systemd 常驻,300 秒一轮;AP 终端数为 0 或离线即推飞书卡片,恢复后自动解除。
> - **自包含**:接口逆向、巡检项与阈值、巡检脚本、守护脚本、systemd 部署、巡检记录全在本目录。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [集客AC零终端与离线守护](1.集客AC零终端与离线守护.md) | 两套告警状态机、每日状态推送、脚本参数、systemd 部署、实测与边界 | ✅ 已部署 |
| 2 | [接口与巡检项](2.接口与巡检项.md) | 登录链路与字段表、AC01–AC05 阈值、处置红线、已知坑、2026-11-23 巡检记录 | ✅ 实测 |

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [ac_ap_guard.py](ac_ap_guard.py) | 单文件三种用法:常驻守护(300 秒一轮,零终端重推 + 飞书告警,离线单独告警)、`--inspect` 一次性巡检(退出码 0/1/2,`--fix` 授权后重推)、`--report` 每日状态推送 |
| [conf/ac-ap-guard.service](conf/ac-ap-guard.service) | 守护 unit,含 `TZ=Asia/Shanghai` 与状态文件路径 |
| [conf/ac-ap-guard-report.service](conf/ac-ap-guard-report.service) | 状态推送的 oneshot unit |
| [conf/ac-ap-guard-report.timer](conf/ac-ap-guard-report.timer) | 每日 09:00 / 14:00(日历带 `Asia/Shanghai`)触发 |
| [conf/ac-ap-guard.env.example](conf/ac-ap-guard.env.example) | 环境变量模板;真实值只放目标机 `/etc/ac-ap-guard/`,权限 600 |

## 阅读路径

- **确认守护在不在跑**:`systemctl status ac-ap-guard`,再看 `journalctl -u ac-ap-guard -n 20` 最近的终端数;
- **跑一次体检**:`AC_BASE_URL=... AC_PASSWORD=... ./ac_ap_guard.py --inspect`,退出码 0/1/2;
- **改状态推送时间**:改 `conf/ac-ap-guard-report.timer` 的 `OnCalendar`,装到 `/etc/systemd/system/` 后 `systemctl daemon-reload && systemctl restart ac-ap-guard-report.timer`;
- **调阈值或告警节奏**:第 1 篇的参数表,改 `/etc/ac-ap-guard/ac-ap-guard.env` 或 unit 里的 `--interval`,然后 `systemctl restart`;
- **查接口字段或新固件对不上**:第 2 篇「接口链路」与「已知坑」;
- **判断某次异常要不要处置**:第 2 篇「巡检项与阈值」「处置手册与授权红线」。

## 这个目录在讲什么

AC 页面上的"终端数"是无线网最直接的健康指标,但它没有 exporter,Prometheus 采不到,只能从管理 API 拉。

`ac_ap_guard.py` 用三种用法覆盖它:5 分钟一轮的常驻守护盯异常并自动重推,`--inspect` 做一次 AC01–AC05 体检,`--report` 每天 09:00 / 14:00 推一张在线状态与终端数的卡片。三者共用一套接口链路、登录会话与判定口径。
