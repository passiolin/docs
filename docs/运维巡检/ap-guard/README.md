# 无线 AP 守护(ap-guard)

> - **对象**:集客 AC 控制台(管理口 `http://<AC_IP>:8080`),下挂 3 台 N3000 AP(ap-1 / ap-2 / ap-3),模板 `gsjh`(id `default`)。
> - **上线**:2026-11-23 起 systemd 常驻,300 秒一轮;AP 终端数为 0 或离线即推飞书卡片,恢复后自动解除。
> - **自包含**:接口逆向、巡检项与阈值、巡检脚本、守护脚本、systemd 部署、巡检记录全在本目录。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [集客AC零终端与离线守护](1.集客AC零终端与离线守护.md) | 两套告警状态机、守护脚本参数、systemd 部署、实测与边界 | ✅ 已部署 |
| 2 | [接口与巡检项](2.接口与巡检项.md) | 登录链路与字段表、AC01–AC05 阈值、处置红线、已知坑、2026-11-23 巡检记录 | ✅ 实测 |

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [ac_ap_guard.py](ac_ap_guard.py) | 守护主体:300 秒一轮,零终端重推 + 飞书告警,离线单独告警 |
| [ac-inspect.sh](ac-inspect.sh) | 只读巡检(退出码 0 健康 / 1 有 P0·P1 / 2 自身故障),`--fix` 为授权后的重推入口 |
| [conf/ac-ap-guard.service](conf/ac-ap-guard.service) | systemd unit,含 `TZ=Asia/Shanghai` 与状态文件路径 |
| [conf/ac-ap-guard.env.example](conf/ac-ap-guard.env.example) | 环境变量模板;真实值只放目标机 `/etc/ac-ap-guard/`,权限 600 |

## 阅读路径

- **确认守护在不在跑**:`systemctl status ac-ap-guard`,再看 `journalctl -u ac-ap-guard -n 20` 最近的终端数;
- **调阈值或告警节奏**:第 1 篇的参数表,改 `/etc/ac-ap-guard/ac-ap-guard.env` 或 unit 里的 `--interval`,然后 `systemctl restart`;
- **查接口字段或新固件对不上**:第 2 篇「接口链路」与「已知坑」;
- **判断某次异常要不要处置**:第 2 篇「巡检项与阈值」「处置手册与授权红线」。

## 这个目录在讲什么

AC 页面上的"终端数"是无线网最直接的健康指标,但它没有 exporter,Prometheus 采不到,只能从管理 API 拉。只读巡检解决"定期体检",解决不了"昨晚 23 点某台 AP 掉到 0 台终端,今早才被人发现"。

`ac_ap_guard.py` 就补这一段:5 分钟一轮,发现异常立刻重推配置并推卡片,然后持续盯着,直到终端数回到 3 台以上或 AP 重新上线。`ac-inspect.sh` 承担一次性的体检与授权处置,两个脚本共用同一套接口链路和判定口径。
