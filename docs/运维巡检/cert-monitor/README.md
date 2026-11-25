# SSL 证书监控(cert-monitor)

> - **对象**:dev/test 五个 TLS 端点——`dev-nginx` / `dev-api` / `test-nginx` 的 443,`dev-mqtt` / `test-mqtt` 的 8883。
> - **节奏**:周一至周五 10:00 跑一轮,推一张按 dev / test 分组的飞书日报。
> - **基线(2026-11-25)**:`*.example.com` 通配证书 2026-12-27 到期;MQTT 服务端证书 2027-08 到期,私有 CA 链需单独挂根证书。
> - **自包含**:探测脚本、目标与 env 模板、systemd unit、阈值口径、排障都在本目录。
> - **脱敏**:文档里的域名统一写 `example.com`,IP 与主机名用 `<内网 IP>`、`<公网 IP>`、`<巡检机 IP>` 占位;实际值只在目标机配置里。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [SSL 证书监控方案](1.SSL证书监控方案.md) | 五维监控口径与分寿命阈值、脚本与 systemd 部署、飞书 webhook 与分组日报、续期侧检查、排障 | ✅ 已部署 |

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [cert_monitor.py](cert_monitor.py) | 单文件探测:TLS 握手、`openssl x509` 解析、链与主机名校验、阈值判定、飞书卡片 |
| [conf/cert-monitor.targets.example](conf/cert-monitor.targets.example) | 五个端点的目标清单;每目标可配 CA、SNI、warn/crit 阈值 |
| [conf/cert-monitor.env.example](conf/cert-monitor.env.example) | webhook、超时、默认阈值模板;真实 webhook 只放目标机 |
| [conf/cert-monitor.service](conf/cert-monitor.service) | oneshot unit,加载 env 后跑一轮 |
| [conf/cert-monitor.timer](conf/cert-monitor.timer) | 周一至周五 10:00 触发,日历带 `Asia/Shanghai` |

## 阅读路径

- **先看现状与阈值**:第 1 篇「五个端点的实测基线」与「监控维度与阈值」;
- **部署脚本**:第 1 篇 3.1–3.4;
- **测试飞书 webhook**:第 1 篇 3.3,先 `--dry-run`,再去掉 `--dry-run` 推一张;
- **证书续了但监控还报旧日期**:第 1 篇「续期侧也要监控」与「排障」;
- **调阈值或加端点**:改 `/etc/cert-monitor/targets.conf`,不用动脚本。

## 这个目录在讲什么

证书到期是慢变量,但故障是突发的:握手失败,HTTP 探针只会报连接错误,MQTT 侧表现为设备整批掉线。这个目录把五个对外 TLS 端点固化成一轮探测——握手、叶子到期、信任链、主机名、续期链路,五个维度一起看。根 CA 30 年、中间 CA 到 2036,不单独监控 CA 到期。

落地方式是本目录的脚本配 systemd timer,告警直接走飞书群机器人的 webhook,不依赖监控平台。阈值口径:90 天证书剩 28 天警告,一年期私有证书剩 30 天警告;工作日 10:00 推一张日报,正常绿卡、有异常橙卡或红卡。
