# Nacos 巡检(inspect-middleware / nacos)

## 定位与依赖

- 本机 curl 可达 8848(open API/控制台);2.x 客户端注册走 gRPC 9848(集群间 9849),连接类巡检依赖该端口;
- 命令面为主(open API + 配置/日志文件核对),盲区命令一律 `timeout` 包装;指标面可选:`/nacos/actuator/prometheus` 直接 curl 返回为空——2.x 默认鉴权,需 basic auth(nacos/nacos)或改配置暴露,未配置前按"数据缺失"记录;
- 部署形态敏感:standalone 可巡全部项;集群模式 NC01/NC02/NC08 需逐节点执行,并核对 `conf/cluster.conf` 成员清单;
- 存储形态敏感:内嵌 Derby(默认)看 data 目录与日志;外置 MySQL 时 DB 侧异常联动 [mysql.md](mysql.md) 的 M01/M06;
- console/config 类 API(NC04/NC05)2.x 默认要鉴权,无凭据记"数据缺失",不允许静默跳过。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| NC01 | 就绪探针 | 命令 | 非 200 即 P0 | 查进程/端口与 nacos.log |
| NC02 | 运行状态(status) | 命令 | status≠UP 即 P0 | 先看日志定位,DB 连不上联动 NC08 |
| NC03 | 服务数/实例数趋势 | 命令 | 较基线突降 P1 | 注册方批量掉线,联动业务/网络域 |
| NC04 | 不健康实例比例 | 命令 | >10% P1 | 逐服务核对心跳失败实例归属 |
| NC05 | 配置数与发布审计 | 命令 | 近期变更与台账对不上 P2 | history 对账,误发布走回滚 |
| NC06 | GC/堆 | 命令+指标 | 老年代连续 ≥80% P2 | 调 JVM 参数,核对服务/配置规模 |
| NC07 | gRPC 连接数(9848) | 命令 | 较基线突降 P1 | 先网络后服务端,再查客户端重连 |
| NC08 | 持久化状态 | 命令 | DB 连接失败即 P0;Derby 目录异常增长 P2 | 外置 MySQL 联动 mysql 域 |

## 检查命令明细

**NC01 就绪探针**(实测返回 200)

```bash
timeout 5 curl -s -o /dev/null -w "%{http_code}" http://localhost:8848/nacos/v1/console/health/readiness
# 200 = 就绪;连接拒绝/5xx 均 P0;401/403 先核对鉴权配置再判
```

**NC02 运行状态**(实测返回 `{"status":"UP"}`)

```bash
timeout 5 curl -s http://localhost:8848/nacos/v1/ns/operator/metrics
# 关注 status 字段,非 UP 即 P0;集群模式下逐节点各查一次
```

**NC03 服务数与实例数趋势**(同一 metrics 响应的计数字段;字段集随版本有差异,以实际响应为准)

```bash
timeout 5 curl -s http://localhost:8848/nacos/v1/ns/operator/metrics | tr ',' '\n' | grep -Ei 'service|ip|count'
# 快照无静态阈值,判定靠两次巡检差值(基线对比):服务数/实例数骤降即 P1——典型是注册方批量掉线
```

**NC04 不健康实例比例**(console API,需鉴权;无凭据记数据缺失)

```bash
timeout 5 curl -s "http://localhost:8848/nacos/v1/ns/instance/list?serviceName=<svc>&groupName=DEFAULT_GROUP&healthyOnly=false"
# 统计 hosts[] 中 healthy=false 的占比,>10% 命中 P1;服务清单按台账逐个或抽样
```

**NC05 配置数与发布审计**(config console API,需鉴权)

```bash
timeout 5 curl -s "http://localhost:8848/nacos/v1/cs/configs?search=accurate&dataId=&group=&tenant=&pageNo=1&pageSize=1"
# 取配置总数;发布审计用 history 接口拉近期变更,与变更台账对账,对不上记 P2
```

**NC06 GC/堆**(tar 包按进程号取;exporter 路径受顶部实测坑限制,命令面优先)

```bash
jps -l | grep -i nacos            # 取 pid(OpenJDK 环境自带;无 jps 则 ps -ef | grep nacos)
jstat -gcutil <pid> 1000 3        # 看老年代(O)占用与 FGC 次数增量
```

**NC07 gRPC 连接数**(2.x 客户端主通道)

```bash
ss -tn state established '( sport = :9848 )' | tail -n +2 | wc -l
# 快照值与基线比,骤降(如 >30%)即 P1;standalone 也有客户端连接,长期为 0 先查端口与客户端
```

**NC08 持久化状态**

```bash
grep -E '^#?[[:space:]]*(db\.|spring\.datasource)' <nacos-home>/conf/application.properties
# db.url 被注释 = 内嵌 Derby(默认);已解注释 = 外置 MySQL
tail -n 200 <nacos-home>/logs/nacos.log | grep -Ei 'datasource|derby|connection refused|too many'
du -sh <nacos-home>/data          # Derby 数据目录,持续膨胀记 P2
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **NC01 非 200 / 进程不在**:tar 包先 `bin/shutdown.sh` 再 `bin/startup.sh -m standalone` 拉起;起不来看 `logs/start.out` 与 `logs/nacos.log`,OOM 最常见(调 `bin/startup.sh` 里 JVM_XMS/JVM_XMX);
- **NC02 DOWN 且日志报 DB 连接失败**:按 NC08 分流——外置 MySQL 先查 DB 侧(联动 [mysql.md](mysql.md) M01/M06);Derby 单机勿乱动 data 目录,先留现场再定恢复方案;
- **NC03 实例数骤降**:服务端往往无错,优先查注册方应用批量重启/心跳超时与网络(联动 inspect-service/inspect-server 域),服务端日志 grep `disconnect`;
- **NC04 不健康实例集中**:多为个别应用心跳失败,联系业务方确认;长期不健康实例的摘除由负责人决定;
- **NC05 变更对不上台账**:控制台"配置历史"核对操作人/dataId,误发布用历史版本回滚;回滚动作等指示;
- **NC06 老年代持续高位**:先核对堆配置(standalone 默认偏小),服务/配置规模大时上调 JVM 参数并连续观察,不在业务高峰调;
- **NC07 连接骤降**:先网络层(9848 可达性、防火墙、LB),再服务端 gRPC 线程与日志;客户端重连风暴按业务侧节奏恢复;
- **NC08 外置 MySQL 故障**:存储切换/迁移属高危变更,一律走变更流程,巡检侧只报告不操作。
