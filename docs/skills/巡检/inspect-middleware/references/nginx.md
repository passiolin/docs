# Nginx 巡检(inspect-middleware / nginx)

## 定位与依赖

- 本机 `nginx` CLI 可用,巡检账号可读 `/etc/nginx/` 配置与 `/var/log/nginx/` 日志;
- stub_status 已启用(实测监听 127.0.0.1:8090);未启用时 NG02/NG03/NG10 无数据,记"数据缺失"并建议开启,不允许静默跳过;
- **实测踩坑**:Ubuntu 26.04 打包版 nginx 只 include `/etc/nginx/conf.d/*.conf` 与 `/etc/nginx/sites-enabled/*`,**没有 conf-enabled**;配置放进不被 include 的目录(或文件后缀不对)时 **`nginx -t` 不报错但不生效**——任何配置改动必须"`nginx -t` → reload → curl stub_status 验证"三步收尾;
- upstream 健康以后端清单为准,清单从配置里的 proxy_pass 提取,不靠猜;后端侧存活与接口质量联动 inspect-service;
- 对外 443 域名清单从 sites-enabled 的 `listen ... ssl` + `server_name` 提取,逐域名查证书;
- 指标面可选:nginx-prometheus-exporter(基于 stub_status)接入后 NG02~NG07 可换算 PromQL。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| NG01 | 进程与配置 | 命令 | `nginx -t` 失败或非 active P0 | 回滚最近配置变更后再 reload |
| NG02 | 活跃连接数 | 命令 | 较基线突增 2 倍 P2 | 看 Reading/Writing/Waiting 结构判性质 |
| NG03 | 请求速率(QPS) | 命令 | 较基线异常波动 P2(结合业务时段) | 与 NG04/NG09 联合判读 |
| NG04 | 5xx 比例 | 命令 | 近 1h >1% / >5% | 按状态码分流,见处置手册 |
| NG05 | 4xx 比例 | 命令 | 近 1h >10% P2 | 甄别扫描流量或配置错误 |
| NG06 | upstream 响应时间 | 命令 | 平均 >1s P2 / >3s P1;**log_format 未含 $upstream_response_time 记 P2 提示开启** | 定位慢后端,联动 service 域 |
| NG07 | upstream 失败 | 命令 | 502/504 持续上升且后端不可达 P1 | 逐个核对 proxy_pass 后端存活 |
| NG08 | 证书有效期 | 命令 | <30 天 / <7 天 | 换发后 reload 前先 `nginx -t` |
| NG09 | error.log 新增错误 | 命令 | 连接耗尽 / upstream timed out 类 P1 | tail 逐条甄别,见处置手册 |
| NG10 | worker 连接水位 | 命令 | 用量 ≥80% P1 / ≥95% P0 | 调整需评估 fd 上限,走变更 |

## 检查命令明细

**NG01 进程与配置**(版本信息走 stderr,不加 2>&1 会拿不到版本串)

```bash
systemctl is-active nginx    # active
nginx -v 2>&1                # nginx version: nginx/1.28.3 (Ubuntu)
nginx -t                     # syntax is ok / test is successful
```

**stub_status 启用**(实测可用;注意上面的 include 目录陷阱,配置必须放 conf.d 或 sites-enabled)

```nginx
# /etc/nginx/conf.d/stub_status.conf
server {
    listen 127.0.0.1:8090;
    location = /stub_status { stub_status; allow 127.0.0.1; deny all; }
}
```

**NG02/NG03/NG10 连接、速率与水位**

```bash
curl -s http://127.0.0.1:8090/stub_status
# 实测输出:
#   Active connections: 1
#   server accepts handled requests
#    2 2 2 
#   Reading: 0 Writing: 1 Waiting: 0
# 解析:第二组数字为 accepts / handled / requests,均为自启动累计值(行首有空格,按列取值不受影响);
#   QPS = 两次巡检的 requests 差值 ÷ 间隔秒——单次快照算不出速率,首轮只建基线;
#   活跃连接基线对比取 Active connections;Waiting 占比高说明大量空闲 keepalive,属正常
grep -E 'worker_(processes|connections)' /etc/nginx/nginx.conf
# 水位 = Active connections(= Reading+Writing+Waiting)÷(worker_processes × worker_connections)
```

**NG04/NG05 状态码比例**(combined 格式状态码为第 9 列)

```bash
awk '{print $9}' /var/log/nginx/access.log | sort | uniq -c | sort -rn
# 实测输出:1 200(单条 200 访问)
# 近 1h 窗口先过滤再统计:awk -v d="$(date +%d/%b/%Y:%H)" '$4 ~ d {print $9}' /var/log/nginx/access.log
# 5xx 比例 = 5xx 计数 ÷ 总计数,4xx 同理;窗口样本 <100 条时如实标注样本量,不判阈值
```

**NG06/NG07 upstream 响应时间与失败**(后端清单从配置取)

```bash
grep -rh proxy_pass /etc/nginx/sites-enabled/ /etc/nginx/conf.d/   # 实测判读入口:后端清单
# 响应时间:需 access_log 的 log_format 显式配置 $upstream_response_time(默认 combined 没有);
#   未配置 → 记 P2 提示开启(改 log_format 走变更);已配置 → 按窗口求平均/分位对比阈值
# 失败信号:access.log 中 502/504 趋势 + error.log 的 connect() failed / no live upstreams 计数;
#   同时核对 proxy_next_upstream 语义:grep -r proxy_next_upstream /etc/nginx/
```

**NG08 证书有效期**(对外 443 逐域名;实测命令形式)

```bash
echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null \
  | openssl x509 -noout -dates     # notBefore/notAfter;剩余天数 = notAfter − 今天
```

**NG09 error.log 甄别**

```bash
tail -100 /var/log/nginx/error.log
# 重点行:worker_connections are not enough(连接耗尽,P1,联动 NG10)
#        upstream timed out / connect() failed(后端问题,P1,联动 service 域)
#        no live upstreams(某 upstream 后端全挂,P1);SSL 证书类报错(联动 NG08)
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **5xx 激增**:先分清状态码再动手——502 = 后端拒连/挂(按 NG07 清单逐个核对后端存活,联动 service 域);504 = 后端超时(联动 NG06 响应时间定位慢后端);500 = 应用自身错误(nginx 只是转发方,转交应用侧处理);未分清前不动 nginx 配置;
- **worker 连接耗尽**:确认 worker_processes × worker_connections 现值,扩容前评估进程 fd 上限(`ulimit -n`、systemd 单元 LimitNOFILE),评估后走变更流程;改完 `nginx -t` 通过再 reload;
- **证书换发**:新证书落盘 → **先 `nginx -t` 验证证书路径可加载** → reload → 用 NG08 命令复验 notAfter,三步缺一不可;
- **error.log 见 upstream timed out**:后端慢是根因,**nginx 侧勿先动**(调大 proxy_read_timeout 只是掩盖问题),联动 service 域定位慢接口/慢 SQL;
- **4xx 比例高**:UA 与路径集中、大量 404/403 → 扫描流量,联动 inspect-server 网络面;正常业务路径稳定 404 → 疑似配置变更引入,核对最近变更记录。
