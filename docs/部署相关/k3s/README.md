# K3S 验证与部署

RKE2 之外的第二个发行版实测系列。背景与选型结论:**重状态中间件生产环境独立部署**(见 [kubernetes/11](../kubernetes/11.管理面板-Rancher.md) 的架构讨论),k8s 集群承载微服务——微服务场景下 k3s + Calico 已满足需求,本系列用实测数据支撑这个判断。

- **版本基线**:k3s v1.36.5+k3s1(containerd 2.3.4-k3s1.36),Calico v3.32.0(tigera-operator),kube-vip v1.2.4,Ubuntu 26.04 LTS
- **实测环境**:PVE 虚机 k3s-server-01/02/03(10.10.12.144-146,2C/4G/40G),与 RKE2 集群同构三节点,便于硬对照;VIP 10.10.12.150
- **测试集群**:144-146 独立于 RKE2 集群(141-143),两套并存互不影响

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [安装部署-单节点](1.安装部署-单节点.md) | 默认形态一条命令起集群、组件盘点、卸载实测 | ✅ 已实测 |
| 2 | [安装部署-HA集群](2.安装部署-HA集群.md) | cluster-init 内嵌 etcd、kube-vip、加入流程 | ✅ 已实测 |
| 3 | [Calico安装与网络验证](3.Calico安装与网络验证.md) | tigera-operator on k3s、NetworkPolicy 实测、封装与带宽 | ✅ 已实测(跨节点带宽待重测) |
| 4 | [与RKE2对比](4.与RKE2对比.md) | 安装耗时/资源/组件差异/已知限制硬对照 | ✅ 已实测 |
| 5 | [什么是ServiceLB](5.什么是ServiceLB.md) | svclb DaemonSet 机制、EXTERNAL-IP 来历、端口互斥根因、换 MetalLB 的时机 | ✅ 已实测(lbpool 待验证) |

## 实测先行结论(对比详见第 4 篇)

- 安装 **20 秒**(RKE2 同流程数分钟),节点 Ready 零等待;卸载 5 秒全清
- 默认全家桶与 RKE2 v1.35 的三个镜像差异:**local-path 默认 SC**(RKE2 已移除)、**ServiceLB 内置**(RKE2 已移除)、**traefik 默认 ingress**(RKE2 用 nginx)
- 内嵌 etcd 无独立进程(对照 RKE2 的静态 Pod 形态),体检走 `k3s etcd-snapshot` + 节点状态
- 已知限制实测确认:ServiceLB 不支持同端口多服务,traefik 默认占用 hostPort 80/443,第二个 80 端口 LoadBalancer Service 永远 Pending
