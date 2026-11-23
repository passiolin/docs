# 无线 AP 守护(ap-guard)

> - **对象**:集客 AC 控制台(管理口 `http://<AC_IP>:8080`),下挂 3 台 N3000 AP(ap-1 / ap-2 / ap-3),模板 `gsjh`(id `default`)。
> - **上线**:2026-11-23,巡检机 systemd 常驻,300 秒一轮;AP 终端数为 0 或离线即推飞书卡片,恢复后自动解除。
> - **分工**:巡检项定义与接口逆向记录在 [skills/巡检/inspect-ac](../../skills/巡检/inspect-ac/README.md),本目录是实施层的补盲脚本。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [集客AC零终端与离线守护](1.集客AC零终端与离线守护.md) | 两套告警状态机、接口链路、脚本参数、systemd 部署、实测记录与已知坑 | ✅ 已部署实测 |

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [ac_ap_guard.py](ac_ap_guard.py) | 守护主体:单文件、只用标准库,`--list` / `--once` / `--dry-run` / `--test-card` 可直接手跑 |
| [conf/ac-ap-guard.service](conf/ac-ap-guard.service) | systemd unit,含 `TZ=Asia/Shanghai` 与状态文件路径 |
| [conf/ac-ap-guard.env.example](conf/ac-ap-guard.env.example) | 环境变量模板;真实值只放目标机 `/etc/ac-ap-guard/`,权限 600 |

## 阅读路径

- **确认守护在不在跑**:`systemctl status ac-ap-guard`,再看 `journalctl -u ac-ap-guard -n 20` 最近的终端数;
- **调阈值或告警节奏**:第 1 篇的参数表,改 `/etc/ac-ap-guard/ac-ap-guard.env` 或 unit 里的 `--interval`,然后 `systemctl restart`;
- **查接口字段或新固件对不上**:第 1 篇「接口」一节,细节在 [inspect-ac/references/api.md](../../skills/巡检/inspect-ac/references/api.md)。

## 这个目录在讲什么

AC 页面上的"终端数"是无线网最直接的健康指标,但它没有 exporter,Prometheus 采不到,只能从管理 API 拉。只读巡检解决"定期体检",解决不了"昨晚 23 点某台 AP 掉到 0 台终端,今早才被人发现"。

`ac_ap_guard.py` 就补这一段:5 分钟一轮,发现异常立刻重推配置并推卡片,然后持续盯着,直到终端数回到 3 台以上或 AP 重新上线。它只管这一件事,不承担整网巡检——巡检项、阈值分级、报告格式仍然以 [inspect-ac skill](../../skills/巡检/inspect-ac/SKILL.md) 为准。
