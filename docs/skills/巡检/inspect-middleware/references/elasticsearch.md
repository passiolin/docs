# Elasticsearch 巡检(inspect-middleware / elasticsearch)

> **实测状态**:✅ 已实测 —— Elasticsearch 8.19.14(deb 包,Ubuntu 26.04,2026-09-30);部署坑已在实验机实测复现(见"版本差异与已知坑",本文档最有价值部分);API 探针(_cluster/health / _cat/nodes / _cat/indices)在启动完成后补测通过,实测输出见命令明细注释。
> **docker 集群实测补充(2026-10-01,三节点 + 单机安全形态)**:三节点集群的 health/_cat/nodes 输出、杀节点 yellow 语义(ES01)、快照 SUCCESS 实测(ES11)、单机认证形态(401 口径)均已回填,部署细节见 [部署相关/elasticsearch](../../../部署相关/elasticsearch/README.md)。

## 定位与依赖

- 本机 `curl` 可达 9200 端口;**8.x deb 包默认开启 xpack.security(HTTP 层 TLS + 认证)**,探测需 `https://localhost:9200 -k -u <user>:<pass>`(或 `--cacert /etc/elasticsearch/certs/http_ca.crt`);本实验机已显式关闭安全,明文 `localhost:9200` 即可——两种形态的切换代价见"版本差异与已知坑"第 1 条;
- 巡检账号具备集群只读权限(builtin monitor 角色);无凭据时标注数据缺失,不允许静默跳过;
- **API 探针一律 `timeout 10` 包装**:高负载/恢复中的 ES 会限流或挂起 HTTP,**超时 ≠ 宕机**,判 P0 前必须复核 9200 端口与 `systemctl status elasticsearch` 进程;
- 指标面可选:elasticsearch_exporter 接入后 ES01/ES04/ES05/ES06 可换算 PromQL,命令面用于落地核查与无 exporter 场景;
- 多集群/多实例按"集群名+节点"分节报告(同 SKILL.md 报告要求);主机层(CPU/磁盘/网络)联动 inspect-server,本域只管组件自身。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| ES01 | 集群状态 | 命令 | status=yellow P1 / red P0 | red 先查分片分配原因,勿先重启节点 |
| ES02 | 节点数 | 命令 | number_of_nodes 与预期拓扑不符 P0 | 核失联节点主机层与进程 |
| ES03 | 未分配分片 | 命令 | unassigned_shards>0 持续 P1 | allocation/explain 定位原因 |
| ES04 | JVM 堆使用 | 命令/指标 | heap ≥75% P1 / ≥90% P0 | 查大聚合/深分页,防 OOM |
| ES05 | 磁盘水位 | 命令 | disk.used_percent ≥80% P1 / ≥90% P0 | 清索引/调 ILM;超 watermark 触发分片迁移 |
| ES06 | 拒绝与熔断 | 命令 | thread_pool rejected 新增 P1;breaker tripped>0 P1 | 限流客户端,评估扩容 |
| ES07 | 慢查询/慢聚合 | 命令 | slowlog 有新增 P2;**未配置 slowlog 记 P2** | 先开 slowlog 再谈治理 |
| ES08 | 只读锁(flood-stage) | 命令 | 出现 index.read_only_allow_delete 标记 P1 | 降磁盘水位后显式解除 |
| ES09 | 版本一致性 | 命令 | _cat/nodes 的 version 不齐 P2 | 滚动升级尽快收尾 |
| ES10 | 单节点 shard 数 | 命令 | ≥800 P2 / 达默认上限 1000 P1 | 控索引/分片数,配 ILM |
| ES11 | 快照 | 命令 | 近 7 天无 SUCCESS 快照 P1(状态对账类) | 查快照仓库与任务报错 |
| ES12 | 证书有效期 | 命令 | security 开启时到期 <30 天 P1 | 走证书更新流程 |

## 检查命令明细(8.x 标准接口,已在 8.19.14 实测)

**ES01/ES02/ES03 集群健康**

```bash
timeout 10 curl -s "localhost:9200/_cluster/health?pretty"
# 安全开启时:timeout 10 curl -s -k -u elastic:<pass> "https://localhost:9200/_cluster/health?pretty"
# 关注:status(green/yellow/red)、number_of_nodes(与预期拓扑比对,缺 node 即 ES02)、
#       unassigned_shards、active_shards_percent_as_number
# yellow/red 时追因:
timeout 10 curl -s "localhost:9200/_cluster/allocation/explain?pretty"   # 仅存在未分配分片时可调用
```

实测输出(8.19.14 单节点,2026-09-30):

```
"cluster_name" : "mw-lab"    "number" : "8.19.14"
"status" : "green"           "number_of_nodes" : 1
"unassigned_shards" : 0      "active_shards_percent_as_number" : 100.0
```

三节点集群实测输出(docker,2026-10-01):

```
"status" : "green"  "number_of_nodes" : 3
_cat/nodes?h=name,heap.percent,node.role,master:
es-3  7 cdfhilmrstw *
es-1  5 cdfhilmrstw -
```

**杀节点实测语义(ES01 定级依据)**:停掉一个持有主分片的节点后,集群 15 秒内转为 `"status":"yellow"` + `number_of_nodes:2`,**检索与写入均正常**(副本自动顶替成主)——**yellow = 副本未凑齐但数据完整可服务,不是故障态**;节点回归约 2~3 分钟后自动回 green。red 才是"有主分片彻底不在"(P0)。

**ES03/ES08 分片与索引视图**

```bash
timeout 10 curl -s "localhost:9200/_cat/shards?v"      # UNASSIGNED 行即异常;node 列汇总支撑 ES10
timeout 10 curl -s "localhost:9200/_cat/indices?v"     # health / status / docs.count / store.size
timeout 10 curl -s "localhost:9200/_all/_settings" | grep -c '"read_only_allow_delete":"true"'
# 计数 >0 即有索引处于 flood-stage 只读锁(命中 ES08)
```

**ES04/ES05/ES09 节点资源与版本**

```bash
timeout 10 curl -s "localhost:9200/_cat/nodes?v&h=name,version,heap.percent,ram.percent,cpu,disk.used_percent"
# heap.percent ≥75/90 判 ES04;disk.used_percent ≥80/90 判 ES05;version 不齐判 ES09
# 实测输出(8.19.14,启动初期 cpu 会短暂偏高):
# name       heap.percent ram.percent cpu disk.used_percent
# mw-lab-es           28          76  97             36.13
timeout 10 curl -s "localhost:9200/_nodes/stats/jvm?filter_path=nodes.*.jvm.mem.heap_used_percent"
# ES04 备用口径(节点粒度堆使用百分比)
```

**ES06 拒绝与熔断**(三项均为累计值,两次巡检差值 = 新增)

```bash
timeout 10 curl -s "localhost:9200/_cat/thread_pool/write,search?v&h=node_name,name,queue,active,rejected"
timeout 10 curl -s "localhost:9200/_nodes/stats/breaker?filter_path=nodes.*.breakers.*.tripped"
timeout 10 curl -s "localhost:9200/_nodes/stats/indices?filter_path=nodes.*.indices.indexing.index_failed"
# 关注 write/search 的 rejected 增长与各 breaker 的 tripped>0(熔断过即计数)
```

**ES07 慢日志**(未配置 slowlog 的索引无法统计——把"未开启"本身作为巡检结果,同 mysql M04 思路)

```bash
timeout 10 curl -s "localhost:9200/_settings?filter_path=*.settings.index.search.slowlog,*.settings.index.indexing.slowlog"
ls -l /var/log/elasticsearch/*slowlog* 2>/dev/null    # *_index_search_slowlog.json / *_index_indexing_slowlog.json
wc -l /var/log/elasticsearch/*slowlog* 2>/dev/null    # 与上次巡检留存值比对 = 新增条数
```

**ES10 单节点 shard 数**(默认上限 cluster.max_shards_per_node=1000,触顶后新分片无法分配)

```bash
timeout 10 curl -s "localhost:9200/_cat/shards?h=node" | sort | uniq -c
timeout 10 curl -s "localhost:9200/_cluster/settings?flat_settings=true" | grep max_shards || echo "未设,默认 1000"
```

**ES11 快照对账**

```bash
timeout 10 curl -s "localhost:9200/_snapshot/_all?pretty"   # 无仓库即无法备份,直接 P1
timeout 10 curl -s "localhost:9200/_snapshot/_all/_all?filter_path=snapshots.*.snapshot,snapshots.*.state,snapshots.*.end_time"
# 取 end_time 最近且 state=SUCCESS 的记录,距今 >7 天即 P1;state=FAILED/IN_PROGRESS 长挂亦列出
```

快照实测补充(2026-10-01,docker 三节点):fs 仓库 + 全量快照实测 `"state":"SUCCESS"`;**前提是 elasticsearch.yml 配了 `path.repo` 白名单**(不配则注册仓库直接报 repository_exception,巡检发现"配了仓库却快照失败"时先查这项)。

**ES12 证书有效期**(security 关闭的环境记"不适用")

```bash
timeout 10 curl -s -k -u elastic:<pass> "https://localhost:9200/_ssl/certificates?pretty"
# 关注每张证书的 expiry_time,距今 <30 天即 P1(http 层与 transport 层都要看)
timeout 10 openssl s_client -connect localhost:9200 </dev/null 2>/dev/null | openssl x509 -noout -enddate
# 备用:直接探测 HTTP 层证书到期日
```

## 版本差异与已知坑(部署实测,2026-09-30 实验机复现)

- **deb 自动安全配置与手动关安全的冲突(实测复现,新装最易踩)**:Ubuntu/Debian 官方 8.x deb 包安装时会自动生成 TLS 证书,并把 `xpack.security.transport.ssl.keystore/truststore` 的 secure_password 写进 elasticsearch keystore。若只在 elasticsearch.yml 设 `xpack.security.enabled: false` 而不清 keystore,**启动直接 fatal**:

  ```
  ElasticsearchSecurityException: invalid configuration for xpack.security.transport.ssl -
  [xpack.security.transport.ssl.enabled] is not set, but the following settings have been
  configured ... secure_password
  ```

  处置:`elasticsearch-keystore remove xpack.security.transport.ssl.keystore.secure_password`(truststore 同名项、http 层两项同理),清完再启动;另一条路是保留默认安全开启,采集凭据后统一走 https 访问(生产推荐);
- **vm.max_map_count 基线不足(实测复现)**:新装内核默认 `vm.max_map_count=65530`,ES bootstrap 检查要求 ≥262144,不满足直接拒启。处置:`sysctl -w vm.max_map_count=262144` 并写入 /etc/sysctl.d/ 持久化;**该项同时是 inspect-server sysctl 基线的应采集项**,巡检 ES 主机时一并核对;
- **高负载下 API 不响应 ≠ 宕机**:本实验机即因负载未在超时窗口完成采样(本文"待补实测"的直接原因);ES 对探测有并发限流,判 ES01 P0 前先复核端口与 systemd 进程,避免把"慢"误报成"死";
- **docker 形态补充(2026-10-01 实测)**:① 只挂 yml 会因缺 log4j2.properties 等 crash(须全量提取 config 再覆盖);② 安全开启 + 绑非回环地址会被 TLS bootstrap check 卡死(单机快速形态绑 127.0.0.1 + ELASTIC_PASSWORD);③ **"正确密码也 401"= 密码源不是你以为的那个**——陈旧 keystore 藏在挂载的 config 目录里,清 data 不解决,要连 `config/elasticsearch.keystore` 一起清;④ 单机/新节点首启 2~3 分钟无响应属正常。

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **ES01 red**:先 `_cluster/allocation/explain` 拿未分配原因(磁盘水位/节点失联/分片损坏),按因处置;**勿先重启节点**——重启会放大恢复流量,多数 red 是单节点失联或磁盘水位问题,不是 ES 进程问题;
- **ES03 未分配分片**:常见因:磁盘超 low watermark(联动 ES05)、副本数 > 节点数(单机索引带 1 副本属典型形态,临时降 number_of_replicas 须业务确认)、disk 过滤器分配规则;explain 输出会直接给 reason;
- **ES04 堆打满**:定位大聚合/深分页/fielddata 类查询;堆大小调整(deb 默认自动配为物理内存 50%)走 jvm.options 变更,遵循 ≤31GB 且 ≤物理内存一半的惯例;
- **ES05/ES08 磁盘水位与只读锁**:磁盘超 flood_stage watermark(默认 95%)时 ES 给索引打 `index.read_only_allow_delete` 只读锁,写入全部失败;**先清磁盘/调 ILM 把水位降回 85% 以下,再显式解锁**:`curl -X PUT "localhost:9200/<index>/_settings" -H 'Content-Type: application/json' -d '{"index.blocks.read_only_allow_delete": null}'`;**勿删 data 目录下任何文件**;
- **ES06 拒绝/熔断**:rejected 是累计值,先用两次巡检差值确认"正在发生"再动手;处置顺序:客户端限流(bulk 并发/搜索频率)→ 查磁盘 IO 与 CPU(联动 inspect-server)→ 最后才谈扩容;
- **ES11 无成功快照**:先核仓库后端可达(fs 仓库要求 elasticsearch.yml 预配 path.repo),再看 `_snapshot/_status` 有无卡死任务;未核对仓库配置前勿手工触发全量快照。
