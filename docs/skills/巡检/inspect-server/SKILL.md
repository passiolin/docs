---
name: inspect-server
description: Use when 巡检或体检 Linux 服务器(物理机/虚机/容器宿主机)的健康状态:CPU、内存、磁盘、网络水位,系统健康(重启/时钟/OOM/温度/失败服务),以及登录安全、SSH 基线、证书到期、cron 变更等盲区项;也为编写或维护服务器巡检脚本、巡检报告解读与异常处置提供权威的巡检项清单、PromQL、shell 命令与阈值。用户提到"服务器巡检""主机巡检""机器体检""资源水位""磁盘快满了排查清单"等均适用。数据来源以 Prometheus node_exporter 指标为主,指标覆盖不到的盲区用 shell 命令补查。
---

# 服务器巡检(inspect-server)

## 定位与范围

- **覆盖对象**:Linux 物理机、虚机、容器宿主机,以 Prometheus 的 `instance` 标签唯一标识;
- **不覆盖**:容器内部状态与业务接口(归 inspect-service)、MySQL/Kafka 等中间件自身健康(归 inspect-middleware)、机房环境(归 inspect-datacenter);
- **数据来源分两类**:
  - **指标面**:node_exporter 采集,巡检只查 Prometheus API 做判断,不重复造采集;
  - **盲区面**(S17、S19~S28):日志、登录记录、本机证书文件、定时任务等指标采不到的,登录目标机(或跳板机)用 shell 补查。

## 依赖与前置条件

执行巡检前确认,不满足时在报告中显式标注"数据缺失",不允许静默跳过:

1. 目标机器已部署 node_exporter 且被 Prometheus 抓取(`up{job=~"node.*"} == 1`);
2. Prometheus API 可达:
   ```bash
   curl -sG http://<prometheus>:9090/api/v1/query --data-urlencode 'query=up' | head
   ```
3. 盲区命令需 root 或 sudo(`lastb`、`smartctl`、全量 `journalctl` 均需特权);
4. S26(cron 审计)、S27(日志增长)依赖上次巡检快照做 diff,首次巡检只建基线不出异常;
5. node_exporter 版本差异(2026-09 在 Ubuntu 26.04 打包版 v1.10.2 实测):`node_netstat_*` 默认暴露,但 `node_procs_zombies` **已被移除**(S16 因此降级为 shell 项)。部署前先验证指标存在性:`curl -s localhost:9100/metrics | grep -c ^node_netstat_Tcp_CurrEstab`,为 0 时开启 `--collector.netstat`;缺失指标对应的巡检项按明细中的降级路径执行。

## 使用方式

- **AI 直接执行巡检**:按"巡检项清单"逐项查询/执行,按"报告要求"产出 Markdown 报告;先跑指标面(一条 curl 可批量查),再跑盲区面(逐机执行);
- **AI 编写/维护巡检脚本**:巡检项、PromQL、命令、阈值、分级**一律以本文件为准**,不得自行发明阈值或绕过清单加项;新增巡检项先补写进本文件(编号顺延 S 序列),再同步脚本;
- **执行健壮性**:盲区命令逐条用 `timeout` 包装(实测 `apt list --upgradable` 在 dpkg 锁竞争时会挂起,拖死整轮巡检);单机整轮建议 120s 上限,超时项标记"数据缺失",不算健康也不算异常;
- **异常处置**:报告里的一句话处置建议用于速览与汇报;动手修复前,读 [references/fixes.md](references/fixes.md) 中对应编号的条目(定位 → 处置 → 验证 → 风险四段式),并遵守该手册开头的生产操作守则(该手册为初步参考,执行前核对现场);
- **豁免约定**:单机豁免(如内网测试机允许密码登录)须在报告中逐条标注对象与原因,不允许全局关闭某个巡检项。

## 巡检项清单

阈值格式为 `P1 / P0`,均为通用默认基线,特殊角色机器(数据库、接入层)可覆盖,覆盖需注明原因。来源列:指标 = Prometheus 查询;shell = 目标机命令。

### A. 资源水位

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| S01 | CPU 使用率 | 指标 | ≥80% / ≥90%(持续 15m) | top/pidstat 定位进程;区分容量问题与异常进程 |
| S02 | 每核负载(load5) | 指标 | ≥0.7 / ≥1.0 | 结合 CPU、IO 判断是算力不足还是 IO 等待 |
| S03 | 内存使用率 | 指标 | ≥85% / ≥95% | 确认是否缓存占比高;排查泄漏进程,考虑扩容 |
| S04 | Swap 使用率 | 指标 | ≥30% / ≥60% | 定位换入换出进程;评估内存是否吃紧 |
| S05 | 磁盘使用率(按挂载点) | 指标 | ≥80% / ≥90% | du 定位大目录;清理或扩容;防日志打满根分区 |
| S06 | inode 使用率 | 指标 | ≥80% / ≥90% | 通常是海量小文件,find 按数量定位目录 |
| S07 | 磁盘 IO 利用率 | 指标 | ≥70% / ≥90%(持续 15m) | iostat -x 看 await 与队列;定位高 IO 进程 |
| S08 | 网卡带宽利用率 | 指标 | ≥70% / ≥90%(需带宽标注) | 确认是否业务高峰;评估升配或分流 |

### B. 网络质量

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| S09 | 网络丢包 | 指标 | 5m 新增 >100 包 | 结合错包排查网卡/线缆/环路;云上查宿主机 |
| S10 | 网络错包 | 指标 | 5m 新增 >0 | errs 持续增长多为硬件层问题,换线/换口 |
| S11 | TCP 连接数与 TIME_WAIT | 指标 | 超基线 2 倍 / —(P2) | 高 TIME_WAIT 结合短连接业务评估,勿盲调内核 |
| S12 | 跨机连通性(ICMP) | 指标 | — / 拨测失败 | 失败先分层:本机网络→交换机→目标机 |
| S32 | conntrack 使用率 | 指标 | ≥80% / ≥90% | 应急调大 max,根治查连接泄漏 |

### C. 系统健康

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| S13 | 意外重启 | 指标 | 24h 内有重启 | 核对维护记录;查 last reboot 与上一启动周期日志 |
| S14 | 时钟偏移 | 指标 | >0.1s / >1s | 漂移会引发证书校验、判主异常;修 NTP 源 |
| S15 | 时间同步服务状态 | shell | 非 active(P0) | 恢复 chronyd/ntpd;确认 NTP 白名单连通 |
| S16 | 僵尸进程 | shell | >10 | 找到父进程重启或修代码;少量可观察 |
| S17 | 内核错误与 OOM | shell | err 日志有(P1)/ OOM kill(P0) | OOM 定位牺牲进程与其内存曲线;err 需逐条甄别 |
| S18 | 硬件温度 | 指标 | >70°C / >80°C | 查风扇/机房环境;云虚机无此指标则跳过 |
| S19 | systemd 失败单元 | shell | 有失败(P1)/ 核心服务失败(P0) | systemctl reset-failed 前先留日志定位原因 |
| S31 | 关键系统服务重启 | shell | networkd 等被重启(P1) | 保留现场日志,关联节点上 Pod 与业务影响 |

### D. 安全与合规(盲区)

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| S20 | SSH 登录失败暴增 | shell | >5000 次/24h / 针对性爆破 | 装/查 fail2ban;核对是否有合法发布机频繁重试 |
| S21 | 异常登录复核 | shell | 非白名单 IP 登录(P1) | 人工核实操作人与时间;必要时踢会话改密钥 |
| S22 | SSH 配置基线 | shell | — / PermitRootLogin+密码双开(P2) | 按最小权限收紧;内网豁免需标注 |
| S23 | 防火墙状态 | shell | 与角色基线不符(P1) | 云机器依赖安全组时本机可关,按角色判断 |
| S24 | 磁盘只读/挂载异常 | shell | — / 非预期只读 | 根分区变 ro 多为磁盘故障前兆,立即迁移 |

### E. 证书与变更审计(盲区)

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| S25 | 本机 TLS 证书过期 | shell | <30 天 / <7 天 | 提前走换证流程;对外业务证书归 service 域 |
| S26 | cron 变更审计 | shell | 与快照有差异(P2) | 逐条确认;不明下载类定时任务按 P0 处置 |
| S27 | 日志目录异常增长 | shell | 日增 >5G(P1) | 查日志轮转(logrotate)是否失效 |
| S28 | SMART 磁盘健康 | shell | — / 非 PASSED | 立即备份数据并换盘;虚机跳过 |
| S29 | systemd timer 审计 | shell | 高危 timer 启用(P1) | 生产禁自动更新类 timer,改走受控变更 |
| S30 | 包变更审计 | shell | 自动安装(P1)/ 涉 libc·systemd·网络栈(P0) | 与变更台账对账,评估影响面 |

## 指标与命令明细

### A. 资源水位

**S01 CPU 使用率**

```promql
# 即时值
100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))
# 持续 15 分钟均超阈值才判异常(防抖,巡检引擎采用)
min_over_time(
  100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))[15m:1m]
)
```

**S02 每核负载**(分母即核数,load5 归一化后跨机型可比)

```promql
node_load5 / count by (instance) (node_cpu_seconds_total{mode="idle"})
```

**S03 内存使用率**(用 MemAvailable 而非 MemFree,含可回收缓存)

```promql
100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
```

**S04 Swap 使用率**(`node_memory_SwapTotal_bytes > 0` 才判,无 swap 机器跳过)

```promql
100 * (1 - node_memory_SwapFree_bytes / node_memory_SwapTotal_bytes)
```

**S05 磁盘使用率**(排除容器/虚拟文件系统,逐挂载点判)

```promql
100 * (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|fuse.*"}
  / node_filesystem_size_bytes{fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|fuse.*"})
```

**S06 inode 使用率**(磁盘有空间但写不进文件时优先怀疑它)

```promql
100 * (1 - node_filesystem_files_free{fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660"}
  / node_filesystem_files{fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660"})
```

**S07 磁盘 IO 利用率**(io_time 单位是秒,rate 后即利用率;防抖同 S01 用 min_over_time 包装)

```promql
100 * rate(node_disk_io_time_seconds_total{device!~"^(loop|ram|fd|sr|dm-).*"}[5m])
```

**S08 网卡带宽利用率**(值为收发合计 bps;需按端口带宽做除法,带宽通过采集侧标注或维护 instance→带宽对照表;未标注时报告原始值人工判断)

```promql
8 * (rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[5m])
   + rate(node_network_transmit_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[5m]))
```

### B. 网络质量

**S09 丢包 / S10 错包**(5 分钟窗口的新增量)

```promql
increase(node_network_receive_drop_total{device!~"lo|veth.*"}[5m])
  + increase(node_network_transmit_drop_total{device!~"lo|veth.*"}[5m])

increase(node_network_receive_errs_total{device!~"lo|veth.*"}[5m])
  + increase(node_network_transmit_errs_total{device!~"lo|veth.*"}[5m])
```

**S11 TCP 连接与 TIME_WAIT**(注:`node_netstat_*` 在 Ubuntu 打包版 v1.10.2 实测默认暴露,自编译或其他发行版部署需确认 `--collector.netstat`;`node_sockstat_*` 均默认开启。无固定阈值:超该机 7 天基线 2 倍记 P2,TIME_WAIT >30000 记 P2)

```promql
node_netstat_Tcp_CurrEstab
node_sockstat_TCP_tw
```

**S12 跨机连通性**(blackbox_exporter 的 icmp prober;未部署时降级为 shell `ping -c3 -W1 <host>`)

```promql
probe_success{job=~"blackbox-icmp.*"}          # 0 即 P0
probe_duration_seconds{job=~"blackbox-icmp.*"} # >1s 记 P2
```

**S32 conntrack 使用率**(K8s/网关类节点重点;打满表现为静默丢包,内核日志仅一句 `nf_conntrack: table full, dropping packet`)

```promql
node_nf_conntrack_entries / node_nf_conntrack_entries_limit
```

### C. 系统健康

**S13 意外重启**(命中即 P1;计划内维护需在报告豁免标注)

```promql
(time() - node_boot_time_seconds) / 3600 < 24
```

**S14 时钟偏移**(漂移会引发证书校验失败、Kafka/ES 判主异常)

```promql
abs(node_timex_offset_seconds)
```

**S15 时间同步服务**(三者其一 active 即通过;`chronyc tracking` 的 Stratum 异常也算)

```bash
for s in chronyd ntpd systemd-timesyncd; do
  systemctl is-active --quiet "$s" && echo "active: $s" && exit 0
done
echo "no time sync service active"   # → P0
chronyc tracking 2>/dev/null | grep -E 'Stratum|Last offset'
```

**S16 僵尸进程**(node_exporter ≥1.10 已移除 `node_procs_zombies` 指标,本项走 shell)

```bash
ps -eo stat | grep -c '^Z'    # >10 记 P1
```

**S17 内核错误与 OOM**(OOM kill 是 P0;其余 err 逐条甄别,存储/文件系统报错优先升级)

```bash
journalctl -k --since "-24h" -p err --no-pager | tail -50
journalctl --since "-24h" --no-pager | grep -iE 'out of memory|oom-killer|killed process' | tail -20
```

**S18 硬件温度**(hwmon collector 默认开启;云虚机通常无此指标,跳过并在报告注明)

```promql
max by (instance) (node_hwmon_temp_celsius)
```

**S19 systemd 失败单元**(命中核心服务清单——按机器角色维护的 nginx/kafka 等列表——升 P0)

```bash
systemctl list-units --state=failed --no-legend --no-pager
```

**S31 关键系统服务重启**(networkd/resolved/journald 被重启会瞬时重建 Pod 网络、断开既有 TCP 连接;多由包升级或配置变更触发,判读时与 S29/S30 关联。注意:只能统计"停止"事件——启动窗口和日常运维会产生大量含 start 的日志行,实测按宽匹配一台正常机器能计出 30 条误报)

```bash
journalctl -u systemd-networkd -u systemd-resolved -u systemd-journald \
  --since "-24h" --no-pager | grep -E 'systemd\[1\]: .*(Deactivated|Stopped|Stopping)'
```

### D. 安全与合规(盲区)

**S20 SSH 登录失败暴增**(需 root;>500 次/24h 记 P2,>5000 或集中针对 root 记 P1)

```bash
lastb -i --since "-24h" 2>/dev/null | wc -l
journalctl -u sshd --since "-24h" --no-pager | grep -ci 'failed password'
```

**S21 异常登录复核**(输出最近 30 次登录;非白名单 IP、非维护窗口登录记 P1,人工核实)

```bash
last -i -n 30
```

**S22 SSH 配置基线**(PermitRootLogin 与 PasswordAuthentication 同时开启记 P2;内网机器豁免需标注)

```bash
sshd -T 2>/dev/null | grep -iE '^(permitrootlogin|passwordauthentication|maxauthtries)'
```

**S23 防火墙状态**(与机器角色基线对比;云上机器依赖安全组时本机可为关闭状态)

```bash
systemctl is-active firewalld 2>/dev/null
ufw status 2>/dev/null
iptables -S 2>/dev/null | head -20
```

**S24 磁盘只读/挂载异常**(根分区变 ro 是磁盘/文件系统故障前兆,P0。注意:`/run/credentials/*` 是 systemd 正常的只读挂载,必须排除——实测不排除则每台机器必误报)

```bash
findmnt -rn -o TARGET,OPTIONS | awk 'index($2,"ro") && !index($2,"rw")' | grep -v '^/run/'
```

### E. 证书与变更(盲区)

**S25 本机 TLS 证书过期**(<30 天 P1,<7 天 P0;只管本机文件证书,对外业务证书归 inspect-service。注意:排除系统 CA 目录 `/etc/ssl/certs`(否则全是根证书噪音),且须跳过无到期日的证书——chrony 的 NTS 证书有效期 9999 年,`date -d` 解析会报错)

```bash
find /etc /opt /data \( -name '*.pem' -o -name '*.crt' \) 2>/dev/null ! -path '/etc/ssl/certs/*' |
while read -r f; do
  end=$(openssl x509 -enddate -noout -in "$f" 2>/dev/null | cut -d= -f2)
  [ -n "$end" ] || continue
  end_epoch=$(date -d "$end" +%s 2>/dev/null) || continue
  days=$(( (end_epoch - $(date +%s)) / 86400 ))
  printf '%5d 天  %s\n' "$days" "$f"
done | sort -n | awk '$1 < 3650' | head -20   # 过滤 10 年以上的长效 CA(fwupd/chrony 等),只留需要关注的
```

**S26 cron 变更审计**(与上次巡检快照 diff;挖矿木马最常见入口,不明下载类任务按 P0 处置)

```bash
crontab -l 2>/dev/null | grep -v '^#'
ls -la /etc/cron.d/ /etc/cron.daily/ 2>/dev/null
for u in $(cut -d: -f1 /etc/passwd); do
  crontab -u "$u" -l 2>/dev/null | grep -q . && echo "== $u ==" && crontab -u "$u" -l
done
```

**S27 日志目录异常增长**(/var/log 总量 >10G 记 P2;较上次快照日增 >5G 记 P1,多为轮转失效)

```bash
du -x -m --max-depth=1 /var/log 2>/dev/null | sort -rn | head -10
```

**S28 SMART 磁盘健康**(物理机专用,非 PASSED 记 P0 并立即安排换盘;虚机跳过。Ubuntu 的 prometheus-node-exporter 包自带 smartmon textfile 采集器,虚机盘可用指标 `smartmon_device_smart_available{disk=...} = 0` 识别)

```bash
if command -v smartctl >/dev/null; then
  for d in /dev/sd[a-z]; do
    [ -b "$d" ] || continue
    printf '%s ' "$d"; smartctl -H "$d" 2>/dev/null | grep -i result
  done
else
  echo "smartctl 未安装,跳过(虚机属预期)"
fi
```

**S29 systemd timer 审计**(与 cron 同级的自启入口,但常被遗漏;生产 worker 上 `apt-daily-upgrade.timer` 处于启用即记 P1——对应"unattended-upgrade 业务时段自动升级 libc 导致断网"事故的整改验收项)

```bash
systemctl list-timers --all --no-pager          # 全量清单与基线 diff
systemctl is-enabled apt-daily-upgrade.timer    # 生产 worker 应为 disabled
grep -r Unattended /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null
```

**S30 包变更审计**(24h 窗口内出现过 unattended 自动安装记 P1;涉及 libc6/systemd/网络栈的包记 P0——这类包的 post-install 会重启系统服务)

```bash
grep -B1 -A8 "Start-Date: $(date -d yesterday +%F)" /var/log/apt/history.log
grep "$(date -d yesterday +%F)" /var/log/dpkg.log | grep -E 'install|upgrade'
ls -lt /var/log/unattended-upgrades/ | head
```

## 报告要求

- 文件头写明:巡检时间、机器范围(instance 清单)、数据来源与快照时刻、数据缺失项;
- 异常项置顶、按级别排序,逐条包含**当前值、命中的阈值、处置建议**;格式:

```markdown
| 编号 | 对象 | 巡检项 | 当前值 | 阈值 | 级别 | 处置建议 |
| --- | --- | --- | --- | --- | --- | --- |
| S05 | 10.0.1.21 | 磁盘使用率 /data | 93% | P1≥80% / P0≥90% | P0 | du 定位大目录,清理或扩容 |
```

- 豁免项单独成表:对象、编号、豁免原因、生效期;
- 报告命名 `inspect-server-YYYYMMDD.md`,归档到实施层 `reports/`;
- 巡检结论退出码:0 全部健康 / 1 存在 P0 或 P1 / 2 巡检自身故障(如 Prometheus 不可达),供 cron 与上层系统判断。

## 巡检项的新增与修改

1. 先修改本文件:清单表加行、明细补表达式/命令,阈值变动写明原因;
2. 再同步实施层规则文件(`docs/运维巡检/runner/rules/`),保持两处一致;
3. 新增项编号顺延(S33、S34…),不复用已废弃编号,避免历史报告歧义。
