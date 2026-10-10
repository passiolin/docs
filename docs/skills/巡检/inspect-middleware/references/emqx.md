# EMQX 巡检(inspect-middleware / emqx)

## 定位与依赖

- 本机可执行 `emqx_ctl`(deb 包装在 /usr/bin),巡检用户具备 sudo 或 emqx 组权限;
- 命令面为主(emqx_ctl);REST/Dashboard 面可选:18083 HTTPS + api-key,接入后 MQ03/MQ06 可换算为接口轮询;
- 单机与集群均可巡;MQ08 集群项单机跳过(记"单机豁免",不算异常);
- **业务联动**:liko 设备业务经 MQTT,MQ03 连接数骤降需联动业务域(设备侧)与网络域一起查;
- 盲区命令一律 `timeout` 包装;`status` 类子命令注意顶部实测坑。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| MQ01 | 节点状态 | 命令 | 非 running 即 P0 | 查 systemd 服务与节点名坑 |
| MQ02 | 监听器 | 命令 | running≠true 即 P0 | 查端口占用/配置;1883/8883/8083 按需核对 |
| MQ03 | 连接数 | 命令 | 较基线突降 P1 | 设备批量掉线,联动业务/网络域 |
| MQ04 | 认证失败率 | 命令 | failure 增速突增 P1 | 排查爆破/证书过期/凭据轮换 |
| MQ05 | 授权拒绝 | 命令 | deny 突增 P2 | 排查 ACL/授权策略变更 |
| MQ06 | 消息丢弃/队列溢出 | 命令 | dropped/overflow 类 >0 且增长 P1 | 查慢订阅与不消费的会话 |
| MQ07 | 订阅数/会话数趋势 | 命令 | 较基线异动 P2 | 与连接数交叉核对 |
| MQ08 | 集群状态 | 命令 | 节点缺失/未运行 P1;单机跳过 | 联动网络域与被摘节点 |

## 检查命令明细

**MQ01 节点状态**(注意实测坑:直接跑会报多节点错)

```bash
EMQX_NODE__NAME=emqx@127.0.0.1 timeout 10 emqx_ctl status
# 期望:Node 'emqx@127.0.0.1' 5.8.6 is started
# 报 "More than one EMQX node found" 不代表宕机,先显式指定节点名再判
```

**MQ02 监听器**(实测可解析)

```bash
timeout 10 emqx_ctl listeners
# 实测节选:
# tcp:default
#   listen_on: 0.0.0.0:1883
#   acceptors: 16
#   running: true
#   current_conn: 0
#   max_conns: 1024
# 判定:任一在用监听器 running != true 即 P0;
#   1883(TCP)/8883(TLS)/8083(WS)按业务保留清单核对,不苛求全开
```

**MQ03 连接数**(MQTT 业务核心指标)

```bash
timeout 10 emqx_ctl listeners | grep -E 'current_conn|max_conns'
timeout 10 emqx_ctl broker metrics | grep -E '^(connections|live_connections)\.'   # 字段以实际输出为准
# 快照值与基线比,骤降(如 >30%)即 P1——优先怀疑设备批量掉线
```

**MQ04 认证失败**(metrics 为自启动累计值,两次巡检差值 = 增速)

```bash
timeout 10 emqx_ctl broker metrics | grep -E '^authentication\.(success|failure)'
# 实测节选:authentication.failure: 0 / authentication.success: 0
# failure 增速突增即 P1;同时段 success 正常 → 指向爆破/凭据/证书问题
```

**MQ05 授权拒绝**

```bash
timeout 10 emqx_ctl broker metrics | grep -E '^authorization\.(allow|deny)'
# 实测节选:authorization.allow: 0 / authorization.deny: 0;deny 突增记 P2,先查授权策略变更
```

**MQ06 消息丢弃/队列溢出**

```bash
timeout 10 emqx_ctl broker metrics | grep -Ei 'dropped|overflow|overload'
# dropped/overflow 类计数 >0 且在增长即 P1;记下具体子项(no_subscribers/inflight_full 等)便于分流
```

**MQ07 订阅数与会话数趋势**

```bash
timeout 10 emqx_ctl broker metrics | grep -E '^(subscriptions|sessions)\.'   # count/max 系列,字段以实际为准
# 与连接数交叉核对:会话涨而连接跌 = 大量设备掉线后留下持久会话
```

**MQ08 集群状态**(单机跳过;子命令未实测,执行前先核对 5.x 帮助)

```bash
EMQX_NODE__NAME=emqx@127.0.0.1 timeout 10 emqx_ctl cluster status
# 关注成员清单与各节点 running 状态;成员缺失/未运行 P1,联动网络域
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **MQ01 反复 not started**:先 `systemctl status emqx` 与 `journalctl -u emqx -n 100`,再核对 `/etc/emqx/emqx.conf` 的 `node.name` 与主机名解析(与顶部实测坑同源);
- **MQ02 监听器 down**:查端口占用(`ss -lntp | grep -E '1883|8883|8083'`)与配置 listener 段;改配置后重启 emqx 属变更动作,等指示;
- **MQ03 连接骤降**:先网络域(端口可达性/防火墙/LB),再设备侧(liko 设备业务,联动业务域查设备固件、电量、APN);Dashboard 掉线原因分布可辅助定位;
- **MQ04 认证失败突增**:排查爆破(同一来源 IP 高频失败)、TLS 证书过期(8883)、近期凭据轮换;封禁来源属网络域动作,不擅自执行;
- **MQ06 消息丢弃增长**:按丢弃子项分流——no_subscribers 查订阅关系丢失,inflight/mqueue 满查慢订阅与消费端;清理异常持久会话前先对账;
- **MQ07 会话堆积**:与业务方确认哪些是掉线遗留,再决定过期时间/清理策略,属变更;
- **MQ08 节点掉线/脑裂**:不要自动 rejoin,按集群恢复流程处理;被摘节点先隔离观察。
