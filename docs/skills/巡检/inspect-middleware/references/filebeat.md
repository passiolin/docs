# Filebeat 巡检(inspect-middleware / filebeat)

> **实测状态**:✅ 已实测 —— Filebeat 8.19.14(deb 包,Ubuntu 26.04,2026-09-30);`filebeat version` 与 `filebeat test config` 输出已验证:
> - `filebeat version` → `filebeat version 8.19.14 (amd64), libbeat 8.19.14 ... built 2026-04-02`
> - `filebeat test config -c /etc/filebeat/filebeat.yml` → `Config OK`
> - deb 装完默认 systemd 服务未激活(`fb_service=inactive` 属预期,按环境启用)——FB01 判读时先区分"未启用"与"该跑没跑"

## 定位与依赖

- 本机 `filebeat` CLI 可用(/usr/bin/filebeat);配置在 /etc/filebeat/filebeat.yml,数据目录(含 registry)在 /var/lib/filebeat/;
- 巡检账号对上述路径可读,可执行 `filebeat test config` / `filebeat test output`(后者会向输出端发**真实探测**,须具备网络与凭据);
- 输出端自身健康归对应组件域(ES 见 [elasticsearch.md](elasticsearch.md)、Kafka 见 [kafka.md](kafka.md)),本域只判 Filebeat 侧;漏采日志是**静默故障**,FB08 是唯一防线;
- `filebeat` 命令一律 `timeout` 包装(60s 起步,test output 含网络探测);
- 指标面可选:配置 `http.enabled: true` 后可从 `localhost:5066/stats` 取 registrar 与 libbeat 输出指标(默认未开启,未开启不判异常)。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| FB01 | 服务状态 | 命令 | 环境应运行而 is-active ≠ active 即 P1 | 查 unit 状态与 journal,勿盲 start |
| FB02 | 配置有效性 | 命令 | test config 输出非 `Config OK` 即 P1 | 先改配置,再谈重启 |
| FB03 | 输出端连通 | 命令 | test output 失败即 P1 | 网络/凭据排查,联动输出端域 |
| FB04 | 注册表体积 | 命令 | registry 环比激增或 >100MB P2 | 判断采集追不上,勿随手删 |
| FB05 | 采集积压 | 命令 | offset 与文件大小差值大 / 落后 mtime >30min P2 | 查 harvester 与输出瓶颈 |
| FB06 | 发送错误率 | 命令 | journal 有 ERROR 重试 P2;持续 >30min 升 P1 | 定位输出端与网络 |
| FB07 | 版本兼容 | 命令 | 与输出端大版本不一致 P2 | 对齐 8.x 大版本 |
| FB08 | 采集清单变更 | 命令 | inputs 路径与基线 diff 出差异 P2 | 回归基线走变更流程 |

## 检查命令明细

**FB01 服务状态**(deb 默认 inactive 属预期,先核该环境是否应运行)

```bash
systemctl is-active filebeat      # inactive ≠ 故障:结合 is-enabled 与环境用途判断
systemctl is-enabled filebeat
journalctl -u filebeat -n 30 --no-pager
```

**FB02 配置有效性**(实测输出:`Config OK`)

```bash
timeout 60 filebeat test config -c /etc/filebeat/filebeat.yml
# 非 "Config OK" 即配置损坏/语法错误,直接 P1;禁止绕过本步重启服务
```

**FB03 输出端连通**(发真实探测;报错可区分 DNS/TLS/认证三类)

```bash
timeout 60 filebeat test output -c /etc/filebeat/filebeat.yml
# ES 输出期望(8.x 文档口径,待本环境补测):talk to server ... OK / parse version response ... OK
# kafka 输出期望:connection to <broker> ... OK
```

**FB04 注册表体积**(registry 为 boltDB 二进制,不能直接 cat,巡检取体积与环比)

```bash
sudo du -sh /var/lib/filebeat/registry 2>/dev/null    # 环比激增说明文件数暴涨或追不上
ls -lh /var/lib/filebeat/registry/filebeat/ 2>/dev/null
# 与上次巡检留存值比对;缓慢增长属正常(新增文件),陡增或超 100MB 记 P2
```

**FB05 采集积压**(offset 粗对账:registry offset vs 当前文件大小与 mtime)

```bash
sudo strings /var/lib/filebeat/registry/filebeat/log 2>/dev/null | grep -o '"o":[0-9]*' | sort -t: -k2 -n | tail
# "o": 为各文件已采 offset(粗对账口径);对照 inputs 各路径当前文件大小:
stat -c '%n %s %y' /var/log/<被采集路径> 2>/dev/null
# 判据:文件 size 持续增长、offset 不动、mtime 落后 >30min → 积压(FB05)
```

**FB06 发送错误率**

```bash
journalctl -u filebeat --since today --no-pager | grep -ciE 'error|retry|refused|backoff'
journalctl -u filebeat --since today --no-pager | grep -iE 'error|retry|refused|backoff' | tail -20
journalctl -u filebeat --since "-30min" --no-pager | grep -ciE 'error|retry|refused|backoff'
# 计数为当日累计,两次巡检差值 = 增速;近 30 分钟窗口计数支撑"持续"升 P1 判级;
# 关注 Connection refused / backoff(exc) / 429 限流关键字
```

**FB07 版本兼容**

```bash
filebeat version | head -1
# 输出端大版本按 elasticsearch.md ES09 / kafka.md K01 口径取;beat 8.x 对 7.x ES 兼容面有限,
# 大版本不一致记 P2(升级窗口对齐)
```

**FB08 采集清单变更**(漏采是静默的:无报错、无日志,只有基线 diff 能发现)

```bash
timeout 60 filebeat export config -c /etc/filebeat/filebeat.yml | grep -A6 'paths:' > /tmp/fb.paths.$$
diff <基线留存目录>/filebeat.paths /tmp/fb.paths.$$        # 无 diff = 清单未漂移
# 有差异(新增/删除路径都算变更)记 P2;确认授权后更新基线留存:
mkdir -p <基线留存目录> && cp /tmp/fb.paths.$$ <基线留存目录>/filebeat.paths && rm -f /tmp/fb.paths.$$
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **FB01 服务未运行**:先 `journalctl -u filebeat -n 50` 找上次退出原因(常见:配置错/OOM/升级残留),修复后再 `systemctl start filebeat`;禁止不看日志直接 start——起不来的原因往往就在 journal 里;
- **FB02 配置失败**:**先 `filebeat test config` 通过,再 `systemctl restart filebeat`**——盲重启会把坏配置灌进运行态,日志链路整体中断;改 paths 后同步核 FB08 基线;
- **FB03 输出不可达**:分层排查——DNS/网络(telnet 输出端端口)、TLS(证书/主机时间偏差)、认证(凭据轮换);输出端自身故障联动 elasticsearch.md / kafka.md 域处置,Filebeat 自动重试退避,输出端恢复即自愈,**勿急着重启 Filebeat**;
- **FB04/FB05 registry 膨胀与积压**:先确认磁盘(联动 inspect-server)与输出端吞吐(联动 FB03/FB06)——积压多因输出端慢或不可达;**勿随手删 /var/lib/filebeat/registry**:registry 记录各文件已采 offset,删除会导致全部日志全量重采,冲击输出端与存储配额;
- **FB06 错误持续**:与 FB03 同链路;429/限流类先调 Filebeat 批量与并发(batch_size/workers),再评估输出端扩容;
- **FB07 大版本不一致**:排期在统一升级窗口对齐 beat 与输出端大版本;对齐前的过渡期加强 FB03/FB06 观察;
- **FB08 采集清单漂移**:diff 出差异后先确认是否为授权变更,非授权则回归基线并走变更流程;新增采集路径须同步更新本巡检基线留存。
