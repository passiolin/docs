# Jenkins 巡检(inspect-middleware / jenkins)

## 定位与依赖

- deb 包部署:systemd 单元 `jenkins.service`,JENKINS_HOME=/var/lib/jenkins,默认端口 8080;
- 命令面为主(systemctl/journalctl/df/du);REST API 面需 **API token**(J05/J06/J07),无凭据记"数据缺失",不允许静默跳过;
- Java 版本敏感:Ubuntu 26.04 默认 Java 25 会拖垮 war 启动(见处置手册);巡检发现 restart 计数飙升先核对 JVM 再谈别的;
- J08 备份 recency 属状态对账类,依赖备份台账(备份介质/目录);
- 盲区命令一律 `timeout` 包装。

## 巡检项清单

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| J01 | 服务状态 | 命令 | 非 active(running) 即 P1 | 先 journal 看 JVM 版本/内存 |
| J02 | 版本与更新 | API | 2.x 有已知高危 CVE 未更 P2 | 升级走变更窗口 |
| J03 | 登录页探针 | 命令 | 非 200/302 即 P1(待补实测) | 区分未起完与真挂 |
| J04 | JENKINS_HOME 磁盘 | 命令 | ≥80% P1 / ≥90% P0 | 清 builds 历史与 workspace |
| J05 | 构建队列深度 | API | 持续 >10 P2 | 查卡住的构建与 agent |
| J06 | agent 在线数 | API | 较台账有节点掉线 P2 | 联动构建失败排查 |
| J07 | 插件更新与失败 | API | 更新可用堆积 P2;失败插件存在 P2 | 更新走变更窗口 |
| J08 | 备份 recency | 命令 | 近 7 天无备份 P1 | 核对备份任务,状态对账 |

## 检查命令明细

**J01 服务状态**

```bash
systemctl is-active jenkins
systemctl show jenkins -p NRestarts        # restart 计数飙升 → 先核对 Java 版本(见处置手册)
journalctl -u jenkins -n 50 --no-pager
```

**J02 版本与更新**(响应头法无需凭据)

```bash
timeout 5 curl -sI http://localhost:8080/login | grep -i '^x-jenkins'   # 如 X-Jenkins: 2.5xx
# 拿到版本号后对照 Jenkins 安全通告核对已知高危 CVE;命中未更记 P2,升级走变更窗口
```

**J03 登录页探针**(高负载首启慢,探针给足超时)

```bash
timeout 15 curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/login
# 期望 200(或跳转 302);503/超时先看 J01 与启动日志,区分"还在启动"与"起不来"
```

**J04 JENKINS_HOME 磁盘**(构建产物堆积最常见)

```bash
df -h /var/lib/jenkins
du -x --max-depth=1 -h /var/lib/jenkins 2>/dev/null | sort -rh | head
du -sh /var/lib/jenkins/jobs/*/builds 2>/dev/null | sort -rh | head   # 各 job 构建历史
```

**J05 构建队列深度**(需 API token)

```bash
timeout 5 curl -s -u <user>:<token> http://localhost:8080/queue/api/json | jq '.items | length'
# 持续 >10 记 P2;看 items[] 里卡住的构建在等什么(label/agent/并发额度)
```

**J06 agent 在线数**(需 API token)

```bash
timeout 5 curl -s -u <user>:<token> http://localhost:8080/computer/api/json \
  | jq '{total: (.computer|length), offline: ([.computer[]|select(.offline)]|length)}'
# offline 数较台账/基线增加记 P2;掉线 agent 与近期构建失败记录交叉核对
```

**J07 插件更新与失败**(需 API token)

```bash
timeout 10 curl -s -u <user>:<token> 'http://localhost:8080/pluginManager/api/json?depth=1' \
  | jq '[.plugins[] | select(.hasUpdate)] | length'      # 可更新数;失败/被禁用插件另行列出
# 更新可用堆积、失败插件存在均记 P2;批量更新前先出清单走变更窗口
```

**J08 备份 recency**(状态对账类,依赖备份台账)

```bash
find <备份目录> -type f -mtime -7 2>/dev/null | wc -l   # 0 = 近 7 天无备份,P1
# thinBackup 等插件备份看其输出目录;外部备份与台账对账,不能只看备份进程在不在
```

## 处置手册(初步参考,未经本环境演练;处置须运维负责人指示)

- **服务反复重启(先看 JVM 再谈别的)**:`journalctl -u jenkins` 见 JVM crash / UnsupportedClassVersionError 类报错时,先核对当前 JAVA_HOME——Ubuntu 26.04 默认 Java 25 会拖垮 war 启动;修复 = systemd override 指定 Java 21(`/etc/systemd/system/jenkins.service.d/java21.conf` 里 `Environment=JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64`),`daemon-reload` + restart;其次查磁盘满(J04)与插件崩溃循环;
- **磁盘满(J04)**:先 `du` 定位,通常是 jobs/*/builds 构建历史与 workspace;清理前按各 job 的 discard/保留天数出清单,删除动作等负责人确认;thinBackup/归档走既有流程,勿直接 rm 大目录;
- **队列堆积(J05)**:queue/api/json 看卡住 item 在等什么(agent/label/并发额度);长时间占 executor 的死锁构建先与业务方确认再 cancel;agent 不够则走扩容评估;
- **agent 掉线(J06)**:核对 agent 侧进程/凭据/网络(联动 inspect-server);恢复后与构建失败记录交叉验证;
- **插件更新(J07)**:一次只更一个高危项并验证,勿在发布高峰批量更新;失败插件先回滚到可用版本再排因;
- **登录页 503(J03)**:高负载首启慢属常见,等 journal 出现 "Jenkins is fully up and running" 再判;持续 503 按 J01 流程查。
