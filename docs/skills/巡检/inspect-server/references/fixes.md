# inspect-server 处置手册(fixes)

与 [SKILL.md](../SKILL.md) 的巡检项编号一一对应。报告里的一句话处置建议用于速览与汇报;**动手修复前读本文对应条目**,按"定位 → 处置 → 验证 → 风险"执行。

## 定位与授权(先读)

- **本手册是初步参考,不是权威操作规程**:内容基于通用 Ubuntu 22.04/24.04 实践编写,未经本环境逐一验证。执行前必须核对目标机器的实际系统版本、云环境与团队变更规范;与现场情况冲突时,以现场和团队规范为准。
- **授权红线**:本手册回答的是"获得指示之后怎么做",**不构成"可以动手"的授权**。巡检(包括 AI)只产出报告与处置建议;任何处置动作,必须在运维负责人明确指示后才启动。AI 引用本手册仅用于给建议,不得自行执行其中任何命令。

## 生产操作守则(先读)

1. 所有生产操作:知晓变更窗口、可回滚、关键操作双人复核(以团队变更管理约定为准);
2. **先取证据后动手**:导出相关时间窗口的 journal、计数器、配置快照,再开始处置——处置动作本身会破坏现场;
3. Kubernetes 节点上的任何变更(升级、重启网络、换内核)先 `kubectl cordon` + 按 PDB `drain`,确认业务 Pod 迁离后再执行,完成后验证再 `uncordon`;
4. 命令以 Ubuntu 22.04~26.04 为准;2026-09-30 已在 Ubuntu 26.04 实测一轮巡检命令(shell 类巡检项全部跑通,据此修订过 S16/S24/S25/S28/S31 的取数命令),但**处置步骤本身未经演练**;不确定的参数先查手册再执行;
5. 涉及跨域的处置(换盘、机房环境、云工单)同时参考对应域 skill(inspect-datacenter / inspect-cloud)。

## A. 资源水位

### S01/S02 CPU 使用率 / 每核负载超阈值

**定位**

```bash
top -b -n1 | head -30                    # 谁在吃 CPU
pidstat -u 5 3                            # 采样确认,排除瞬时毛刺
ps -eo pid,stat,cmd | awk '$2 ~ /^D/'     # D 状态进程数(不可中断 IO 等待)
```

**处置**

1. 单进程异常偏高:先确认业务身份再决定限流(`cpulimit`/cgroup 限速)或重启;容器内先 `kubectl top pod` 归属到 Pod;
2. 整体容量型(多进程均匀偏高、持续数日):按 7 天趋势评估扩容或 HPA;
3. 负载高但 CPU 使用率不高:是 IO 等待主导,转 S07 路径处理。

**验证**:CPU 回落至阈值下;负载/核数 < 0.7 持续 1 小时以上。

**风险**:kill 前务必确认不是系统/集群关键进程(kubelet、sshd、DB);优先限流与滚动重启,直接 kill 易引发连锁故障。

### S03/S04 内存 / Swap 超阈值

**定位**

```bash
free -m
ps aux --sort=-rss | head -15             # RSS 排序
grep -E 'Slab|SReclaimable' /proc/meminfo  # 内核可回收 slab 占比
```

**处置**

1. `MemAvailable` 充足而"使用率"高:是 page cache,属正常,误报——巡检应以 MemAvailable 判定;
2. 单进程 RSS 持续爬升不见回落:泄漏特征,滚动重启止血,保留重启前 PID 与 heap/core 供排查;
3. Swap 持续使用:确认是内存吃紧(加配/限流)而非 swappiness 过激;调低 `vm.swappiness` 是缓解不是治疗。

**验证**:MemAvailable 回升;Swap 占比回落。

**风险**:**不要**把 `echo 3 > /proc/sys/vm/drop_caches` 当处置手段——只会造成短暂 IO 风暴;重启泄漏进程前先摘流量。

### S05/S06 磁盘 / inode 使用率超阈值

**定位**

```bash
df -h; df -i
du -x -m --max-depth=2 / 2>/dev/null | sort -rn | head -20
# inode 型(空间够但文件写不进):按目录数文件
for d in /*; do echo "$(find "$d" -xdev 2>/dev/null | wc -l) $d"; done | sort -rn | head
```

**处置**

1. 日志类暴涨:优先 `logrotate -f /etc/logrotate.d/<规则>`;**正在被写入的活动日志不要 `rm`**(空间不释放且 fd 悬空),用 `truncate -s 0 <文件>`;journald 用 `journalctl --vacuum-size=2G`;
2. 已删除但仍占空间:`lsof +L1` 找到持有 deleted 文件的进程,重启该进程释放;
3. 容器环境:`docker system prune`(注意 `-a` 会删除未运行镜像,先确认);
4. inode 型:定位海量小文件目录(常见:session 文件、邮件队列、`core` 堆积),确认后归档或删除;
5. 清理后复核增长趋势,仍不乐观则提扩容。

**验证**:`df -h`/`df -i` 回落;`predict_linear` 外推不再命中。

**风险**:`rm` 前双重确认路径;`find ... -delete` 绝不允许以 `/` 为起点;删除前问一句"这个目录谁在用"。

### S07 磁盘 IO 利用率超阈值

**定位**

```bash
iostat -x 1 5          # 关注 r_await/w_await/%util
iotop -o               # 哪个进程在读写
pidstat -d 5 3         # 无 iotop 时的替代
```

**处置**

1. 单进程读写爆量:确认业务(备份、索引重建、ETL)后错峰或限速(如 `ionice`);
2. 数据库机器:区分刷脏页(正常回收)与真实负载;云盘突发余额耗尽(gp3 burst / 云监控 IO 曲线掉底)需等待恢复或升配;
3. await 高但 %util 不高:怀疑云盘/阵列层问题,收集计数器报云工单。

**验证**:%util 回落、await 恢复该盘常态水位。

**风险**:勿在业务高峰触发大量 fsync 的操作(如手动 `sync`)加剧拥塞。

### S08 网卡带宽超阈值

**定位**:`iftop` / `nethogs` 找流量源头;云机器对照云监控出网带宽图。

**处置**:确认属业务高峰(扩带宽/CDN/压缩)还是异常外发(被入侵对外扫探、备份挤占——异常外发转 S26 安全路径);临时可用 tc 限速保业务端口。

**验证**:利用率回落阈值下,业务延迟正常。

## B. 网络质量

### S09/S10 丢包 / 错包增长

**定位**

```bash
ethtool -S ens5 | grep -iE 'err|drop' | grep -vE ': 0$'   # 网卡计数器分层定位
ip -s link show ens5
```

**处置**

1. 物理机:错包集中在 PHY 层换线/换端口;递进排查交换机端口错包计数;
2. 云机器:自身计数器干净但丢包存在→宿主机/虚拟化层问题,附计数器截图报云工单;
3. `vxlan.calico` 错包:关联该节点 calico 状态与 MTU 配置(见 SKILL 之外补充:MTU 错配表现为大包丢小包通)。

**验证**:计数器增速归零,业务重传率回落。

### S11 TCP 连接 / TIME_WAIT 异常

**定位**:`ss -s`;`ss -tan state time-wait | wc -l`;按对端聚合 `ss -tan | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head`。

**处置**:根因多在应用侧短连接风暴——改连接池/长连接是根治;确认是客户端主动断开场景后,方可评估开启 `net.ipv4.tcp_tw_reuse`(记录变更)。

**风险**:**先应用后内核**,盲调内核参数常引入新问题;TIME_WAIT 高但不丢连接、不耗尽端口时,可以先观察。

### S12 跨机连通性拨测失败

**定位**(分层法,逐层收敛):本机 `ip addr`/`ip route` → `ping` 网关 → `ping` 同网段其他机器 → 目标机反向 → 中间安全组/防火墙。

**处置**:按命中的层修复——网卡 down(no-carrier)转网络栈;路由缺失补路由并固化到 netplan;安全组/防火墙规则改正;整批机器同时失联优先怀疑交换机/机房(转 inspect-datacenter)。

### S32 conntrack 使用率超阈值

**定位**

```bash
cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max
conntrack -S 2>/dev/null | head    # find/insert/drop 计数,drop 增长即已伤流量
```

**处置**

1. 应急:调大上限并持久化——`sysctl -w net.netfilter.nf_conntrack_max=<更大值>`(每条约 320B 内存,评估余量)并写入 `/etc/sysctl.d/`;
2. 根治:定位连接泄漏(短连接未关、 TIME_WAIT 堆积来源进程);K8s 节点确认 kube-proxy 模式与 calico 配置是否有已知放大问题;
3. 使用率 >90% 且伴随 drop 计数增长:按 P0 处理,流量已在被丢弃。

**验证**:使用率 <70%,drop 计数不再增长。

## C. 系统健康

### S13 意外重启

**定位**

```bash
who -b; last reboot | head
journalctl -b -1 -e --no-pager | tail -50    # 上一个启动周期的结尾(崩溃前最后日志)
```

**处置**:按证据分型——内核 panic(结尾有 panic/oops)→按调用栈排查驱动/内核;日志戛然而止无 panic → 疑似硬件/宿主机断电,云上查实例系统事件(维护迁移),物理机查 IPMI SEL;规律性重启查电源与温度(联动 S18、inspect-datacenter)。

**验证**:查明原因并有对应整改项;无因的"偶发"重启要持续观察,复发即升级。

### S14/S15 时钟偏移 / NTP 服务异常

**定位**:`chronyc tracking`;`chronyc sources -v`(源可达性与 stratum);`timedatectl`。

**处置**:换可用时间源(内网 NTP 白名单);`chronyc makestep` 立即步进对齐;服务挂了 `systemctl restart chronyd` 并查为何退出(联动 S19)。

**风险**:大步进对时会影响 TLS 校验、DB/ES 判主、定时任务触发——选低峰执行并提前通告;持续缓慢漂移说明 VM中断/宿主机问题,别只治标。

### S16 僵尸进程超阈值

**定位**:`ps -eo pid,ppid,stat,cmd | awk '$3 ~ /^Z/'`。

**处置**:僵尸的父进程不收尸——确认父进程业务身份后择机重启父进程使其释放;父进程是核心服务且不能重启的,提缺陷跟踪,少量僵尸本身不耗资源,不必强清。

### S17 内核错误 / OOM

**定位**

```bash
journalctl -k --since "-24h" | grep -iE 'oom-killer|out of memory|killed process'
# K8s 上区分两类 OOM:系统级(上式) vs cgroup 级(容器超 limit)
kubectl describe pod <pod> | grep -A3 Reason
```

**处置**

1. 系统级 OOM:看被牺牲进程的内存曲线——容量不足则加配/限流;持续爬升则泄漏,重启止血并留现场(core/heap);
2. cgroup 级 OOM:Pod 被 OOMKilled 是 limit 过紧或容器内泄漏,调整 limit 前先确认不是泄漏;
3. 其他内核 err(I/O error、EXT4/XFS error、MCE):**优先按硬件故障路径升级**,联动 S24/S28。

**验证**:OOM 不再复发;被牺牲服务恢复且稳定。

**风险**:靠加 swap/调 `overcommit_memory` 掩盖问题只会推迟爆炸;重启泄漏进程前摘流量。

### S18 硬件温度超阈值

**定位**:`sensors`;物理机 `ipmitool sensor | grep -iE 'temp|fan'`。

**处置**:风扇 0 转或单点高温→转机房检查(联动 inspect-datacenter);整体环境温度高→机房制冷工单;短期可降负载散热。

### S19/S31 systemd 失败单元 / 关键系统服务重启

**定位**

```bash
systemctl status <unit> --no-pager
journalctl -u <unit> -e --no-pager | tail -50
systemctl show <unit> -p NRestarts     # 重启计数,判断是否在静默循环
```

**处置(S19)**:先读日志定位失败原因,**`systemctl reset-failed` 只清状态不修问题**;反复 failed→Restart 掩盖崩溃,按崩溃处理。

**处置(S31,systemd-networkd 被重启剧本)**:已发生断网类故障时——

1. 立即保留现场:导出 `journalctl --utc` 事故窗口(systemd、networkd、apt 单元)与 `/var/log/apt/history.log`;
2. 评估影响:该节点 Pod 列表、下游连接重置记录、关联业务指标(如 Kafka lag);
3. 根因多为包升级(S30)或 timer 自动任务(S29),按其处置项整改;
4. 处置期间如需重现验证,先 drain 节点再操作。

### S31 补充:服务重启循环(同条目扩展场景)

`NRestarts` 持续增长说明 `Restart=always` 在掩盖崩溃:先 `journalctl -u <unit>` 看退出原因(OOM?配置错?依赖不可用),修根因后再观察;不要直接把 Restart 关掉了事。

## D. 安全与合规

### S20/S21 SSH 爆破 / 异常登录

**定位**

```bash
lastb -i | awk '{print $3}' | sort | uniq -c | sort -rn | head   # 失败来源聚合
last -i -n 30                                                    # 成功登录复核
```

**处置**

1. 爆破量大:部署/检查 fail2ban;根治是**安全组将 22 端口来源收紧到跳板机网段**;
2. 疑似爆破成功(失败后紧接成功、陌生 IP):按安全事件响应——立即 `ss -K` 踢会话、更换密钥、导出 `~/.bash_history` 与相关日志留证、逐项排查持久化点(联动 S26);
3. 封禁来源前核对白名单(发布机、监控拨测源),防止误伤自动化。

**风险**:踢会话、换密钥动作要确认不影响在跑的合法运维操作。

### S22 SSH 配置基线收紧

**处置**(防锁死三步,顺序不能错):

1. 确认密钥登录可用:**新开一个会话**用密钥登录成功(不要依赖当前会话);
2. 修改 `/etc/ssh/sshd_config.d/*.conf`:`PermitRootLogin prohibit-password`、`PasswordAuthentication no`;
3. `sshd -t` 校验语法通过后 `systemctl reload ssh`;**保持现有会话不退出**,再用新会话验证一次。

**风险**:云主机保留 console/VNC 后路;内网豁免机器走报告标注流程,不私下放宽。

### S23 防火墙状态与基线不符

**定位**:`iptables -S` / `firewall-cmd --list-all`,对照该机器角色的基线规则表;云机器同步核对安全组(inspect-cloud 域)。

**处置**:缺失规则按基线补齐;多余规则确认来源后清除;规则变更走变更单并在基线文件中登记——**基线与现场不一致本身就是债**。

### S24 磁盘变只读(P0)

**处置**

1. 立即停止向该盘写入的应用(防数据不一致扩大);
2. 云盘先打快照,物理机确认 RAID 层状态;
3. `umount` 后做文件系统检查:ext4 用 `fsck -y`;XFS 先 `xfs_repair -n`(只读预检),`xfs_repair -L` 是最后手段(清日志会丢未落盘数据);
4. 修复后 `mount -o rw` 重新挂载,应用恢复并校验数据。

**风险**:**绝对不要**对挂载中的盘执行 fsck;xfs_repair -L 前必须已有快照/备份;根分区只读无法 umount 的,直接进入换盘/重建流程。

## E. 证书与变更审计

### S25 TLS 证书临期

**定位**:确认证书路径与引用它的服务(`grep -rl <cert路径> /etc/nginx/ /etc/<服务>/`),避免换了一处漏一处。

**处置**:按签发渠道续期(内部 CA 重签 / ACME 自动续期)→ 替换文件(权限 600,属主正确)→ `nginx -t` 等配置校验后 reload 服务 → 旧证书移入带日期的备份目录而非直接删除;对外域名用 `openssl s_client` 复核线上生效的是新证书。

### S26 cron 变更审计命中 / 疑似恶意定时任务

**处置**

1. 已知正常变更:更新基线快照,登记台账;
2. **不明下载/挖矿特征任务(curl|wget 管道执行、陌生域名)按 P0 安全事件**:
   - 先隔离(安全组收紧到仅运维入口),**不要先删除**——保留 `crontab -l`、定时文件本体(留 hash)、相关进程与文件样本;
   - 排查全部持久化点:`/etc/cron*`、systemd unit/drop-in、`rc.local`、root 及应用账号的 `authorized_keys`、`/tmp` 与 `/dev/shm` 可执行文件;
   - 处置后更换凭据,按团队安全事件流程上报。

**风险**:贸然杀进程删文件会丢失溯源线索,且可能触发攻击者的备用持久化。

### S27 日志目录异常增长

**定位**:`du` 找到具体文件;`lsof <文件>` 确认写入者;`logrotate -d /etc/logrotate.conf` 干跑看该规则是否覆盖、语法是否有效。

**处置**:修复或补建 logrotate 规则(注意 `copytruncate` 适用场景——进程不重开文件句柄时)后 `logrotate -f` 生效;journald 超限则设 `SystemMaxUse=` 后 `systemctl restart systemd-journald`;应用自身日志框架(logback 等)的滚动配置在应用侧修。

### S28 SMART 非 PASSED(P0)

**处置**:立即安排数据备份/迁移;云盘报工单换盘或迁移实例;物理机确认 RAID 冗余状态后热插更换(更换期间阵列处于降级,避免该时段额外磁盘压力)。**不要**等坏盘完全失效——SMART 报错后失效窗口不可预测。

### S29 systemd timer 整改(禁用自动更新类 timer)

**处置**(K8s worker,参照 unattended-upgrade 事故整改):

```bash
# 1. 停用 apt 自动更新 timer(先 cordon/drain,见生产操作守则)
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer

# 2. 关闭 unattended-upgrades 自动安装
sed -i 's/APT::Periodic::Unattended-Upgrade "1"/APT::Periodic::Unattended-Upgrade "0"/' \
  /etc/apt/apt.conf.d/20auto-upgrades

# 3. 同类治理:snap 自动刷新窗口挪到业务低峰
snap set system refresh.timer=4:00-6:00/2   # 示例,按维护窗口定
```

**风险**:**禁用自动更新 ≠ 不打补丁**——必须同时建立"补丁进基础镜像 → 预发布验证 → 滚动替换节点"的受控流程,否则只是把"业务时段随机断网"换成"安全补丁长期缺位";snap refresh 同样会重启服务,窗口治理与 apt 一并落地。

### S30 包变更审计命中(事后处置)

**处置**

1. 核对变更影响:该机承载的服务、其下游(连接是否已恢复)、是否需要滚动重启进程以加载新库(**libc 升级后长寿命进程仍运行旧库,重启才彻底生效**);
2. 与变更台账对账:无对应工单的安装即违规变更,追责并补流程;
3. 涉及 libc/systemd/网络栈的历史升级:按 S31 剧本核查网络服务与连接面是否已恢复。

**验证**:受影响服务重启完毕、指标恢复正常;台账补录完整。
