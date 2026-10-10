# ZooKeeper 巡检(inspect-middleware / zookeeper)

## 定位与依赖

- 2181 客户端端口在巡检机可达;四字命令经 bash 内置 /dev/tcp 直发,无需 nc / zkCli 等额外客户端;
- **主路径 = 四字命令**。HTTP admin server(8080 `/commands/mntr`)**在 Ubuntu 打包版上未开放(实测空响应)**——需 zoo.cfg 显式 `admin.enableServer=true` 才有,巡检不得依赖;
- **Ubuntu zookeeperd 包默认放行 ruok/mntr/srvr 等四字命令(实测)**;自装或其他发行版若命令返回空,先查 zoo.cfg 的 `4lw.commands.whitelist`(官方默认仅放行 srvr);
- 指标面可选:mntr 字段可经 zookeeper exporter 转 Prometheus(接入后 Z03/Z04/Z08 可换算 PromQL);
- 集群巡检需持有全部节点清单,逐节点执行同一命令再汇总比对;
- 实例标识遵循总入口约定:单机用连接串(IP:2181),集群用"集群名+节点",多实例按节点分节出报告;
- 部署(单机 standalone / 三节点 ensemble)细节见 [部署相关/zookeeper](../../../../部署相关/zookeeper/README.md)。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| Z01 | 存活(ruok→imok) | 命令 | 非 imok 或连接失败即 P0 | 查进程/端口与服务日志 |
| Z02 | 服务器状态与拓扑 | 命令 | 角色/数量与预期拓扑不符 P1;集群无 leader P0 | 逐节点 mntr 核对 |
| Z03 | 平均延迟 | 命令 | zk_avg_latency >50ms / >200ms | 定位慢客户端与磁盘 IO |
| Z04 | outstanding 请求 | 命令 | >10 持续 P1;持续堆积且 >100 P0 | 看是否单点处理不过来 |
| Z05 | 活连接数 | 命令 | 较基线突增 P2 / —(泄漏线索) | ss 找来源 IP 对齐业务 |
| Z06 | znode 数量趋势 | 命令 | 突增 P2 / —(节点泄漏线索) | 排查未清理节点 |
| Z07 | ephemeral 数 | 命令 | 骤降/归零 P1(应有业务会话时)/ — | 核对客户端是否全断 |
| Z08 | 文件描述符 | 命令 | open/max ≥80% / ≥95% | LimitNOFILE 调整走变更 |
| Z09 | Watch 数(可选) | 命令 | 突增 P2 / —(watch 风暴线索) | 排查重复注册客户端 |
| Z10 | 集群同步(zxid 对比) | 命令 | follower 落后 leader P1;持续不追平 P0 | 集群适用,单机无此项 |

## 检查命令明细

**Z01 存活(ruok)**

```bash
echo ruok | timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/2181; cat >&3; timeout 2 head -c4 <&3'
# 实测输出:imok;其余输出/空响应/超时均为异常,2181 连不上直接 P0
```

**mntr / srvr 统一入口**(同为四字命令,mntr 出全量指标、srvr 出概要)

```bash
zk4lw() { echo "$1" | timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/2181; cat >&3; timeout 2 cat <&3'; }
zk4lw mntr
zk4lw srvr
```

实测 mntr 关键字段(制表符分隔的 key/value):

```text
zk_version      3.9.5-...
zk_server_state standalone        # standalone/leader/follower/observer → Z02
zk_avg_latency  0                 # → Z03
zk_outstanding_requests 0         # → Z04
zk_znode_count  5                 # → Z06
zk_watch_count  0                 # → Z09(3.5+ 提供)
zk_ephemerals_count 0             # → Z07
zk_num_alive_connections 1        # → Z05
zk_packets_sent / zk_packets_received  …   # 流量增速,佐证 Z03 定位方向
zk_open_file_descriptor_count / zk_max_file_descriptor_count  …   # → Z08
```

srvr 实测输出(单机):

```text
Zookeeper version: 3.9.5-…
Latency min/avg/max: 0/0.0/0
Received: …
Sent: …
Connections: 1
Outstanding: 0
Zxid: 0x…        # → Z10:高 32 位 epoch + 低 32 位事务计数
Mode: standalone # → Z02:leader/follower/observer/standalone
Node count: 5
```

srvr 集群实测输出(三节点 ensemble,2026-10-01;逐节点执行取 Mode 汇总):

```text
zk-1: Mode: follower     # 132 停机前为 leader;杀 leader 后 133 接管(秒级),132 回归自动降为 follower
zk-2: Mode: follower     # → Z02 判定:3 节点应为恰 1 个 leader + 2 个 follower
zk-3: Mode: leader
```

**Z05 连接来源定位**(按来源 IP 聚合,与 `zk_num_alive_connections` 对账;处置手册同款)

```bash
ss -tn '( dport = :2181 or sport = :2181 )' | awk 'NR>1{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn
```

**逐项判定**

- **Z02**:汇总各节点 mntr 的 `zk_server_state` 与预期拓扑比对(如 3 节点应为 1 leader + 2 follower);leader 数 ≠1 即无有效 leader,按 P0;单机应为 standalone;
- **Z03**:以 mntr 的 `zk_avg_latency` 为准、srvr 的 avg 佐证;持续偏高才判——同轮巡检间隔 10~30s 复测一次,两次均超阈值再定级(单次抖动忽略);
- **Z04**:`zk_outstanding_requests` 为瞬时值,>10 时同样复测确认持续;持续增长比绝对值更能说明"处理不过来";
- **Z05/Z06/Z07/Z09**:绝对值意义不大,**与该实例历史基线比对**(两次巡检差值);ephemeral 归零且业务方确认应有会话时,按 P1 上报;
- **Z08**:fd 利用率 = `zk_open_file_descriptor_count / zk_max_file_descriptor_count`;
- **Z10**:对比各节点 srvr 的 `Zxid`(十六进制)——epoch(高 32 位)不一致说明经历过选主,follower 事务计数落后且差值持续扩大即同步滞后;单机(Mode: standalone)无此项。

## 版本差异与已知坑

- `zk_watch_count` 为 3.5+ 字段;3.4 的 mntr 无此项,Z09 应判"数据缺失"而非当作 0;
- `4lw.commands.whitelist` 在 3.5 起默认收紧(仅 srvr);Ubuntu 打包版已放行(实测),**官方 docker 镜像未放行(2026-10-01 实测:ruok/mntr 空响应,zoo.cfg 显式放行后正常)**——docker 形态巡检前先核对挂载的 zoo.cfg;
- admin server(8080)为 3.5+ 能力且需 zoo.cfg 显式 `admin.enableServer=true`;**Ubuntu 打包版实测未开放,但官方 docker 镜像实测默认开放**(`http://<host>:8080/commands/mntr` 返回 JSON)——两种形态主路径都是四字命令,docker 形态多一条 HTTP 通道且注意 8080 撞名;
- 集群杀 leader 实测(2026-10-01):切换秒级完成,旧 leader 回归自动降为 follower、zxid 自动追平——Z02/Z10 的处置验证依据;
- 本文档命令仅在 3.9.5 实测,跨大版本执行前先回核本节。

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **Z03 延迟高**:先分清方向——avg 高但 outstanding 低,多为慢客户端/网络问题(看 zk_packets 增速、连接来源 IP);avg 与 outstanding 同升,多为服务端处理不过来,联动 inspect-server 查磁盘 IO(事务日志/快照盘)与 CPU;同时核对 `zk_watch_count` 是否突增(watch 风暴);
- **Z02 集群无 leader**:先确认半数以上节点存活、节点间网络互通(逐台 ruok/mntr),只处理故障节点;**不要急着重启全部节点**(全体重启只会延长不可用窗口);恢复后确认 leader 选出、各 follower zxid 追平;
- **Z08 fd 耗尽**:先看 `zk_num_alive_connections` 是否同步异常上涨(连接泄漏会推高 fd,先按 Z05 治理);根治走变更:systemd 单元 LimitNOFILE(或 /etc/security/limits.conf)上调后重启生效,巡检侧只报告不擅动;
- **Z05 连接泄漏**:`ss -tn '( dport = :2181 or sport = :2181 )'` 按来源 IP 聚合,与业务方核对连接池/会话使用方式(未复用、断连未清理);`zk_num_alive_connections` 与各业务侧会话数对账;
- **Z06/Z07 znode 与 ephemeral 异常**:ephemeral 依赖会话存活、会话断开自动清理——归零先确认是否客户端全断(联动业务方);`zk_znode_count` 持续增长排查持久节点无人清理、临时节点靠长连接硬撑的情况;
- **通用**:任何异常先看服务日志(journalctl -u zookeeper 或部署日志目录)再动手;ZooKeeper 重启会丢失 ephemeral 节点,**重启前必须与业务方确认影响**。
