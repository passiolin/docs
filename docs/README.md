# 你好，我是 passio 🐱

> **Java 后端 · IoT 基础设施 · 运维**，也写点东西。

五年后端 + 运维一把梭：从 Spring 微服务到百万级设备接入，从写代码到谈判云折扣，信条是——**能用开源解决的，绝不掏钱给厂商**。

## 我靠什么吃饭

主业是 IoT 云平台与基础设施，后端和运维一把梭，几个拿得出手的数字：

| 维度 | 规模 |
| --- | --- |
| 设备并发接入 | 100 万+ 长连接（MQTT） |
| 服务用户 | 200 万+，98% 在海外 |
| 实时数仓 | StarRocks 日均 200 亿条 |

### 技术栈

- **接入层**：EMQX 集群调优与瓶颈分析、自研 emqx-kafka 插件、且自己写了个Java的mqtt-broker实现 [jmqtt-broker](https://github.com/passiolin/jmqtt-broker)
- **存储计算**：MySQL MGR 集群、Redis、Elasticsearch 全套 ELK、StarRocks / Doris、MongoDB
- **容器与编排**：RKE2 自建 K8s、Rancher、Cilium/Calico 选型、Longhorn 存储
- **CI/CD**：Jenkins 多 Slave、GitLab、Nexus、Trivy + AI 漏洞分析、连嵌入式固件（ESP32/STM32）的 CI 都标准化了
- **云**：AWS/腾讯/阿里深度使用，VPC、EC2、块存储、EKS等。

## 我在折腾什么（HomeLab）

下班后的基础设施才是纯爱好：

- 🌐 **软路由**：iKuai 虚拟机部署 + NAT/桥接组网、DNS 劫持，写成了系列教程
- 🖥 **虚拟化**：PVE，显卡直通（RTX 5060 Ti 的 IOMMU/VFIO 全流程踩坑记录）
- 💾 **存储**：ZFS（zpool raidz1 实操）、OpenLDAP 统一账号直通 NAS 登录
- 🔗 **组网**：Tailscale 不用官方服务——自托管 headscale + derper 中继；OpenVPN、WireGuard 异地组网
- 📦 **自托管全家桶**：Gitea、Halo、NginxWebUI、JumpServer、Nexus、Prometheus + Grafana……

