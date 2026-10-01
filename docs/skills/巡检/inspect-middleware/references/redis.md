# Redis 巡检(inspect-middleware / redis)

> **实测状态**:✅ 已实测 —— Redis 8.0.5(Ubuntu 26.04 打包版,2026-09-30),单机模式;INFO 各段输出已验证可解析。两条实测教训已写入阈值:**碎片率必须加 used_memory 前置条件**(小实例碎片率必然虚高)、**默认 maxmemory=0 且 noeviction**。
> **哨兵/集群实测补充(2026-10-01,docker 三节点)**:R11 的哨兵口径(含 num-other-sentinels 验收)、集群形态输出、exporter 实测指标名均已回填,部署细节见 [部署相关/redis](../../../部署相关/redis/README.md)。

## 定位与依赖

- `redis-cli` 可达,巡检账号通过 ACL 或只读从库执行(`INFO`/`CONFIG GET` 为只读);
- 指标面可选:redis_exporter 接入后各 INFO 项均有对应指标(实测 v1.66.0,451 个指标:redis_up / redis_memory_used_bytes / redis_memory_max_bytes / redis_connected_clients / redis_blocked_clients / redis_slowlog_length / redis_rdb_last_bgsave_status / redis_evicted_keys_total / redis_keyspace_hits_total / redis_master_link_up 等,与 R 系列一一对应);
- 集群模式额外查 `CLUSTER INFO`(cluster_state:ok)与 `CLUSTER NODES` 的 fail 状态;哨兵形态核 `SENTINEL master` 的 num-slaves / num-other-sentinels(见 R11)。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| R01 | 存活 | 命令 | PING 非 PONG 即 P0 | 查进程与服务日志 |
| R02 | 内存上限配置 | 命令 | **maxmemory=0 记 P2**(未设限) | 设定 maxmemory + 淘汰策略 |
| R03 | 内存利用率 | 命令 | ≥80% / ≥95%(对 maxmemory) | 先看是否大 key 增长 |
| R04 | 淘汰策略 | 命令 | noeviction 且贴近上限 P1 | 按业务语义选 volatile/allkeys-lru |
| R05 | 内存碎片率 | 命令 | >2 / >3(**used_memory ≥100MB 才判**);<1 提示 swap | 小实例跳过;碎片高择机重启回收 |
| R06 | 命中率 | 命令 | <90% P2 / <80% P1(流量样本充足时) | 甄别 key 设计与过期策略 |
| R07 | 持久化状态 | 命令 | bgsave 失败 P1;AOF 写失败 P0 | 见处置手册 |
| R08 | 拒绝连接 | 命令 | rejected_connections 增长 P1 | maxclients 与主机句柄(联动 S32/句柄) |
| R09 | 键淘汰 | 命令 | evicted_keys 增长 P1 | 内存已满开始丢数据,立即扩容或清大 key |
| R10 | 阻塞客户端 | 命令 | blocked_clients 持续 >0 P1 | 慢命令(BLPOP 等待/大 key 操作) |
| R11 | 复制状态 | 命令 | 从库 master_link_status=down P1 | 网络或主库视角排查 |
| R12 | 慢命令 | 命令 | SLOWLOG 有新条目 P2;>10ms 条目多 P1 | 治理大 key/禁用危险命令 |
| R13 | ops 与键数趋势 | 命令 | 超基线 2 倍 P2 | 结合业务变更判断 |

## 检查命令明细

**R01/R02/R03/R05 存活与内存**(实测输出:`used_memory_human:740.97K`、`maxmemory_human:0B`、`mem_fragmentation_ratio:20.85`——小实例碎片率虚高的实证)

```bash
redis-cli ping
redis-cli INFO memory | grep -E "^used_memory_human|^used_memory_rss_human|^mem_fragmentation_ratio|^maxmemory_human|^maxmemory_policy"
```

判读:
- `maxmemory_human:0B` = 未设上限(R02),配合主机内存巡检兜底;
- 碎片率判读顺序:先看 `used_memory` ≥100MB 才有意义(实测 741KB 实例碎片率 20.85 属正常物理页开销);`<1` 且持续下降提示内存被换出到 swap(联动 inspect-server S04)。

**R06 命中率**(无流量时除零,输出 n/a 跳过——实测验证)

```bash
redis-cli INFO stats | awk -F, '/^keyspace_hits/{for(i=1;i<=NF;i++){split($i,a,"=");h[a[1]]=a[2]}}
END{if(h["keyspace_hits"]+h["keyspace_misses"]>0) printf "%.1f", 100*h["keyspace_hits"]/(h["keyspace_hits"]+h["keyspace_misses"]); else print "n/a"}'
```

**R07 持久化**

```bash
redis-cli INFO persistence | grep -E "^rdb_last_bgsave_status|^rdb_changes_since_last_save|^aof_enabled|^aof_last_write_status"
# aof_enabled:0 时 R07 的 AOF 部分按业务约定标注(纯缓存可豁免)
```

**R08/R09/R10/R13 运行指标**

```bash
redis-cli INFO stats | grep -E "^connected_clients|^blocked_clients|^rejected_connections|^evicted_keys|^instantaneous_ops_per_sec"
```

**R11 复制**(role:master 时 connected_slaves=0 正常;role:slave 才看 master_link_status)

```bash
redis-cli INFO replication | grep -E "^role|^connected_slaves|^master_link_status"
```

**R11 哨兵形态实测补充(2026-10-01,三哨兵)**:

```bash
redis-cli -p 26379 SENTINEL master mymaster | sed -n '/num-slaves/{n;p};/num-other-sentinels/{n;p}'
# 实测:2  2   ← num-slaves 与 num-other-sentinels,两者与拓扑不符即哨兵组不健康
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster   # 当前主地址(应用侧同款)
```

**判"哨兵失效"的实测教训**:`num-other-sentinels=0` 时哨兵各自为战,quorum 永远凑不齐——**主挂了也不会切换**,而 SENTINEL master 的其他字段看起来正常。哨兵形态巡检必须核这一项;根因常见为 announce-ip 配置错误。

**集群形态实测输出(三主三从,2026-10-01)**:

```bash
redis-cli -p 7001 CLUSTER INFO | grep -E "^cluster_state|^cluster_size"
# cluster_state:ok / cluster_size:3
redis-cli -p 7001 CLUSTER KEYSLOT user:1001   # (integer) 5712(键→槽定位)
# 普通客户端打到非归属节点:(error) MOVED 15495 10.10.12.127:7005(集群模式客户端应自动跟随,巡检 CLI 探活看到 MOVED 不是故障)
```

**R12 慢日志**

```bash
redis-cli SLOWLOG LEN
redis-cli SLOWLOG GET 5    # 最近 5 条,含微秒耗时与命令
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **R05 碎片率高**:确认 used_memory 足够大后仍 >2,择机 `activedefrag`(若开启)或低峰重启实例回收;**不要**在业务高峰操作;
- **R07 bgsave 失败**:常见 fork 内存不足(overcommit)或磁盘满(联动 S05);修复后手动 `BGSAVE` 验证;AOF 写失败多为磁盘故障,P0 路径;
- **R09 evicted_keys 增长**:内存淘汰已发生——若业务语义是"缓存"可接受并观察,是"存储"则 P0 扩容;先 `redis-cli --bigkeys`(只读采样)定位大户;
- **R10 blocked 持续**:定位 BLPOP 类等待(业务正常)还是 MULTI 阻塞(异常);配合 `INFO commandstats` 看哪类命令耗时高;
- **R12 慢命令治理**:KEYS/FLUSHALL 等危险命令通过 ACL/`rename-command` 禁用属变更,走变更流程。
