# 你好，我是 passio 🐱

> **Java 后端 · IoT 平台 · 基础设施与运维**，也写点东西。

五年后端加运维：从 Netty/MQTT 接入层写到 StarRocks 实时数仓，从写代码到搭机房、谈云折扣。信条是——**能用开源解决的，绝不掏钱给厂商**。

## 撑过的规模

| 维度 | 数字                  |
| --- |---------------------|
| 设备并发接入 | 100 万+ 长连接（MQTT）    |
| 服务用户 | 200 万+，98% 在海外      |
| 实时数仓 | StarRocks 日均 200 亿条 |
| 单机长连接调优 | 100 万级（Nginx）       |
| 系统可用性 | 99.999%             |

## 专业技能

**后端**

- Java 并发编程与 JVM 原理，生产环境监控、调优、排障；优化过 JVM 内存配置，把微服务内存占用和服务器成本一起压下来
- Spring / Spring Cloud 微服务：注册中心、服务网关、断路器的选型与落地
- NIO / Netty：基于 Netty 实现 HTTP 与 MQTT 服务器，自研 [jmqtt-broker](https://github.com/passiolin/jmqtt-broker)
- 网络协议：TCP / UDP / HTTP / MQTT，GB28181 视频平台对接与推流排障

**存储与中间件**

- MySQL：开发、部署、原理，慢 SQL 周检与调优，数据库负载降 30%
- Redis：生产部署与代码优化，读过 Redisson 源码；大 key 分片、延时队列
- Kafka：开发、部署、原理，读到 Java 客户端源码级，生产全链路调优
- Elasticsearch：日志采集与大数据量优化，索引合并 + Reindex 降存储碎片
- StarRocks：日均 200 亿条生产集群的部署与调优——RoutineLoad 接入、冷热分层、备份、FE/BE
- Zookeeper / RocketMQ：分布式锁、Watcher；订单事件通知

**运维与 DevOps**

- 运维体系从零搭建：机房网络、VPN、私有云（PVE）、Kubernetes、CI/CD、监控告警、多云纳管，云成本年降 30%
- Jenkins 单 Master 多 Slave、GitLab、Nexus 自建镜像仓库；嵌入式固件的自动构建也进了流水线
- Prometheus 告警体系 + Grafana 面板定制，常态化巡检机制
- 腾讯云 / AWS：VPC、EC2、块存储、EKS，多云内网互通；域名备案与 SSL 证书部署流程
- Linux / Docker：数据库备份、自动打包、自动部署脚本

**业务**

- 支付：Apple Pay / Google Pay / 微信支付 / 支付宝 / IAP 对接，处理过 IAP 证书过期导致的回调故障
- 电商 SaaS：LDAP + JWT 账号权限体系、SPU/SKU、订单、促销、购物车、终端管理

## 干过的事

- **IoT 云平台（从零到一）**：账号、设备指令、支付、饮水算法等核心模块；Kafka 消费调优、实时统计分层存储，支撑上表全部数字
- **DevOps 体系（从零搭建）**：多云（腾讯云 / AWS）统一纳管、内网互通与故障自愈；处理过 AWS 块存储损坏（Redis 主从切换 + SLA 索赔）和云 LB 故障（Nginx TCP 代理迁移数十万长连接）
- **音视频质量分析平台**：Kafka + StarRocks 采集 App 与链路服务器的抖动率、丢包率，把复现不了的瞬时音视频异常变成可查的指标
- **电商 SaaS**：把线下电话销售搬上线的代理商平台，从架构选型到订单、支付全链路开发
- **视频安防平台**：多厂商雷达数据接入与目标跟踪，修内存泄漏、推流时间戳错乱等一串遗留缺陷并完成交付

## HomeLab

下班后的基础设施，博客大半文章来自这里：

- 🖥 **虚拟化**：[PVE](部署相关/pve/README.md)，显卡直通（IOMMU/VFIO 全流程踩坑）、ZFS raidz1
- 🌐 **软路由**：iKuai 虚拟机部署 + NAT/桥接组网、DNS 劫持
- 🔗 **组网**：Tailscale 不走官方服务——自托管 [headscale + derper](部署相关/tailscale/README.md)；OpenVPN、WireGuard 异地组网
- 📦 **自托管中间件**：[MySQL](部署相关/mysql/README.md)、[Redis/Valkey](部署相关/redis/README.md)、[Kafka](部署相关/kafka/README.md)、[Zookeeper](部署相关/zookeeper/README.md)、[Elasticsearch](部署相关/elasticsearch/README.md)，部署、备份、监控写成系列
- 💾 **统一账号**：OpenLDAP 直通 NAS 登录
- 🤖 **本地大模型**：[llamacpp](部署相关/llamacpp/README.md) 部署笔记
- 💻 **自研**：[jmqtt-broker](https://github.com/passiolin/jmqtt-broker)（[部署与调优笔记](部署相关/jmqtt/README.md)）
