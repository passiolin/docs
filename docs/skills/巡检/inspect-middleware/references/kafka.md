# Kafka 巡检(inspect-middleware / kafka)

> **实测状态**:✅ 已实测 —— Kafka 4.3.1 KRaft 单机(standalone 格式化),Ubuntu 26.04 上的 JDK,2026-09-30;topic/lag/quorum 命令输出已验证。多 broker 相关项(ISR 收缩、分区分布、quorum 多数派)在单机上的输出形态已在条目内标注,集群环境按同口径推演。
> **集群实测补充(2026-10-01,docker 三节点 KRaft)**:K04/K05/K06 的集群输出、ISR 收缩实测、K02 的 exporter 指标名均在 docker 集群(2 broker 在线形态)实测回填,部署细节见 [部署相关/kafka](../../../部署相关/kafka/README.md)。

## 定位与依赖

- 本机 Kafka CLI 可用:`kafka-topics.sh`、`kafka-consumer-groups.sh`、`kafka-metadata-quorum.sh`、`kafka-log-dirs.sh`、`kafka-broker-api-versions.sh`、`kafka-configs.sh`;巡检只需只读;
- **CLI 一律 `timeout 60` 包装**:CLI 是 JVM 进程,高负载机器上启动可能超 20 秒(见"版本差异与已知坑"),timeout 给不足会把慢启动误报成 broker 不可达;
- 指标面可选:Kafka Exporter(`kafka_consumergroup_lag` 等)接入后 K02/K03/K08/K09 换算 PromQL;**生产环境 lag 监控走 exporter**,CLI 用于落地核查与无 exporter 场景;
- K06 区分 KRaft(4.x 默认)与老 ZooKeeper 模式(3.x 及以前),命令分别给出;
- 多集群/多实例按 bootstrap-server 连接串分节报告(同 SKILL.md 报告要求)。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| K01 | broker 存活 | 命令 | 任一 broker 不可达即 P0 | 查进程、JVM 日志与主机层 |
| K02 | 消费 lag | 命令/指标 | 绝对值 >100万 P1;持续增长且 >1h P0(按业务 SLA 覆盖) | **同时看增速**,按"lag 突增"处置 |
| K03 | 消费组活跃 | 命令 | 关键组 no active members 且业务应有消费 P1 | 查消费者进程与下游连接 |
| K04 | ISR 收缩 | 命令 | Isr 数 < Replicas 数即 P0 | 查网络/磁盘/IO,恢复后观察追平 |
| K05 | 无 leader 分区 | 命令 | `Leader: none` 即 P0 | 按"无 leader 分区"处置 |
| K06 | quorum/控制器状态 | 命令 | 无 leader 或多数派失联 P0 | KRaft 看 quorum describe --status |
| K07 | 磁盘水位 | 命令 | log.dirs 使用率 ≥80% / ≥90%(联动 server 域 S05) | 调 retention/清 segment,勿手工删文件 |
| K08 | 消息进出速率 | 指标 | 突降为 0 且业务应在跑 P1 | 先查生产方与上游链路 |
| K09 | 请求处理延迟 | 指标 | 持续走高 P2;伴随 K02/K04 异常升 P1 | 结合磁盘 IO 与网络定位 |
| K10 | 分区分布不均 | 命令 | 各 broker leader 数失衡 P2 | 评估分区重分配 |
| K11 | 消息堆积时间 | 命令/指标 | 追平耗时(lag÷消费速率)>24h P2 | 评估扩消费者/分区 |
| K12 | topic 配置漂移 | 命令 | retention / min.insync.replicas 与基线 diff P2 | 回归基线,走变更流程 |

## 检查命令明细

**K01 broker 存活**

```bash
timeout 60 kafka-broker-api-versions.sh --bootstrap-server localhost:9092 | head -2
# 能列出 broker 与协议版本即存活;Connection refused / 超时即异常,再核进程与端口
# 集群:逐节点核验(或以 quorum describe --status 的 Voters 清单为全员在线依据)
```

**K02/K03 消费 lag 与消费组活跃**

```bash
timeout 60 kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list
timeout 60 kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group inspect-group
```

实测输出:

```
Consumer group 'inspect-group' has no active members.
GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID HOST CLIENT-ID
inspect-group inspect-test 0 0 196 196 - - -
```

- 附注:**无活跃成员时 CLI 会先提示 "Consumer group 'inspect-group' has no active members." 再出表——这是正常输出,不是错误**;
- CONSUMER-ID / HOST / CLIENT-ID 为 `-` 即无活跃成员(K03 判据);lag = LOG-END-OFFSET − CURRENT-OFFSET,按 (group, topic, partition) 逐项判阈;
- 两次巡检的 lag 差值即增速,**突增比绝对值更能定位事件**(K02 核心);指标面 `kafka_consumergroup_lag` 与之同源。

**K04/K05/K10 ISR、leader 与分区分布**

```bash
timeout 60 kafka-topics.sh --bootstrap-server localhost:9092 --describe
```

实测输出:

```
Topic: inspect-test  TopicId: ZeqJuZwWRfGOrrC_cp2ZZg  PartitionCount: 1  ReplicationFactor: 1  Configs: min.insync.replicas=1,segment.bytes=1073741824
	Topic: inspect-test  Partition: 0  Leader: 1  Replicas: 1  Isr: 1  Elr:  LastKnownElr: 
```

- 关注 **Leader / Replicas / Isr**:`Leader: none` 命中 K05(P0);Isr 个数 < Replicas 个数命中 K04(P0);
- 4.x 输出多了 **Elr / LastKnownElr** 列(停写副本集合),老脚本按列位置解析会错位——按列名解析;
- K10:汇总各 broker 的 Leader 计数,失衡记 P2(单机全部落在 broker 1,属正常形态)。

**K06 quorum/控制器状态**

```bash
timeout 60 kafka-metadata-quorum.sh --bootstrap-server localhost:9092 describe --status
# 关注:CurrentLeader(LeaderId 是否在 Voters 内)、HighWatermark 是否推进、
#       Followers 各节点 LastCaughtUpTimestamp 是否跟上
# 老 ZK 模式(3.x):查 /controller 临时节点,确认 controller 所在 broker 与 epoch
```

集群实测输出(3 节点拓扑,2 台在线):

```
LeaderId:               2
LeaderEpoch:            1
HighWatermark:          731
CurrentVoters:          [{"id": 1, "endpoints": ["CONTROLLER://10.10.12.128:9093"]}, {"id": 2, ...}, {"id": 3, ...}]
CurrentObservers:       []
```

- 判定:LeaderId ∈ Voters 且 HighWatermark 两次巡检有推进;**Voters 清单同时是 K01 的全员在线依据**(逐台可达即全员在)。

**K04/K05 集群实测形态(杀一台 broker,12 秒后从幸存者视角)**:

```
Topic: device-events  Partition: 0  Leader: 2  Replicas: 1,2  Isr: 2
```

- Leader 自动迁移、ISR 从 [1,2] 收缩为 [2]——**K04 命中形态即此**(Isr 个数 < Replicas);节点回归后 ISR 自动回满,无需人工干预;

**K07 磁盘水位**

```bash
timeout 60 kafka-log-dirs.sh --bootstrap-server localhost:9092 --describe   # JSON:取各 broker 各 logDir 的 bytes 求和
df -h <server.properties 中各 log.dirs 路径>                                # 与 server 域 S05 同口径判水位
```

- kafka-log-dirs 输出为单行 JSON(含各分区 sizes),取 logDir 级汇总即可;`error` 非空的 logDir 单独列出。

**K08/K09 消息速率与请求延迟(exporter 路径)**

```promql
sum by (topic) (rate(kafka_topic_partition_current_offset[5m]))      # 写入速率(分区位移增速)
sum by (consumergroup, topic) (kafka_consumergroup_lag)              # lag 总量,与 K02 同源
```

- K08:写入速率突降为 0 且业务应在跑 → 先查生产方与上游链路;消费速率可取两次巡检 CURRENT-OFFSET 差值;
- K09:broker 端 fetch/purgatory 类指标(JMX exporter)持续走高记 P2,伴随 K02/K04 异常时升 P1。

**K11/K12 堆积时间与配置漂移**

```bash
# K11:追平耗时 = lag ÷ 消费速率(消费速率取两次巡检 CURRENT-OFFSET 差值),>24h 记 P2
timeout 60 kafka-configs.sh --bootstrap-server localhost:9092 --describe --entity-type topics --entity-name inspect-test
# K12:retention.ms / min.insync.replicas / segment.bytes 与基线 diff(未设动态配置时输出为空,按 server.properties 默认值比对)
```

## 版本差异与已知坑

- **4.x 格式化必须显式单机/多机模式**:config/server.properties 模板不再自带 `controller.quorum.voters`,`kafka-storage.sh format` 必须显式 `--standalone`(单机)或 `--initial-controllers`(多机),否则报 `you must specify one of the following: --standalone, --initial-controllers, or --no-initial-controllers`;
- **lag 突增要关联节点网络与变更**(内部真实事故,2026-07-28):unattended-upgrade 升级 libc6 → systemd-networkd 重启 → Pod 下游连接重置 → mqtt_command 组 lag 102 万→640 万,14:39 自愈;排查用 `sum by (consumergroup, topic) (kafka_consumergroup_lag{topic="mqtt_command"})` 对齐时间线,并核对当日 apt/网络变更记录;
- **CLI 的 JVM 启动在高负载机器上可能超过 20 秒**,巡检脚本必须给足 timeout(实测 60s 稳);
- **docker 部署的收发探活必须 `docker exec -i`**(2026-10-01 集群实测):管道喂 console-producer 时缺 `-i` 会**静默丢数据**(连接正常、无报错、offset 不涨);console-consumer 的 stdout 在官方镜像可能无输出,**数据链路对账以 consumer-groups describe 的 offset/lag 为准**;
- exporter 实测指标名(v1.8.0,385 指标):`kafka_brokers`、`kafka_consumergroup_lag{...}`/`_sum`、`kafka_topic_partition_current_offset`——K01/K02/K08 的指标面直接可用。

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **K02 lag 突增**:**先查消费端**——消费者进程是否存活、下游连接是否被重置、消费线程是否卡死;再查节点网络与当日变更(参考 2026-07-28 事故链:升级引发网络组件重启 → 下游连接重置 → lag 数倍突增后自愈);**不要先重启 broker**——broker 本身正常时,重启只会放大抖动;
- **K04 ISR 收缩**:定位失同步副本所在节点的网络/磁盘/IO(联动 inspect-server);恢复后观察 ISR 追平即可,**勿急扩副本**、勿调小 min.insync.replicas 掩盖问题;
- **K07 磁盘高**:调 `log.retention.hours` / `retention.ms` 与 segment 清理策略让删除自然生效,配合清理废弃 topic;**勿手工删 log.dirs 下数据文件**——与集群元数据不同步会损坏副本;
- **K05 无 leader 分区**:**优先恢复 ISR 内副本或触发选主**(拉起失联 broker / 修复存储);同步评估 min.insync.replicas 影响的可用性——acks=all 的生产者会因 ISR 不足写入失败,降配须业务侧确认。
