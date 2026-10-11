# MetalLB

LoadBalancer 型 Service 的地址应答器:给 `type: LoadBalancer` 的 Service 分配池内 IP 并做 L2 ARP 公告。RKE2 v1.35 已移除 ServiceLB(实测集群无 svclb),**要 LoadBalancer 就必须装它**——2026-10-11 在三节点集群实测。

## 安装(helm,实测 chart 0.16.1)

```bash
helm repo add metallb https://metallb.github.io/metallb
helm install metallb metallb/metallb -n metallb-system --create-namespace
```

Pod 形态(实测):controller ×1 + speaker ×3(每节点) + frr-k8s ×3(BGP 支撑组件,chart 默认携带;纯 L2 场景暂留默认,关闭参数待实测)。镜像经 mirror 约 3-5 分钟就绪。

## 配置:地址池 + L2 公告(实测)

```yaml
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: lab-pool
  namespace: metallb-system
spec:
  addresses:
  - 10.10.12.240-10.10.12.249
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: lab-l2
  namespace: metallb-system
spec:
  ipAddressPools: [lab-pool]
```

只建 IPAddressPool 不建 Advertisement,池不会生效——旧笔记的示例就缺这一半。规划约束:池要避开 DHCP 段与 kube-vip 的 API 地址(10.10.12.140),分配前先 ping 扫一遍确认空闲。

## 验证(实测)

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: web-demo-lb
spec:
  type: LoadBalancer
  selector:
    app: web-demo
  ports:
  - port: 80
    targetPort: 80
EOF

kubectl get svc web-demo-lb    # EXTERNAL-IP 实测 5 秒内分到 10.10.12.240
curl http://10.10.12.240/      # 从集群外任意机器直连,实测命中后端
```

Grafana 的 LoadBalancer(见 [第 9 篇](../../9.监控与告警.md))同样由此分到 10.10.12.241。

## 运维

```bash
kubectl -n metallb-system get IPAddressPool,L2Advertisement   # 池与公告
kubectl -n metallb-system logs deploy/metallb-controller --tail=50
helm uninstall metallb -n metallb-system && kubectl delete ns metallb-system   # 已分配的 EXTERNAL-IP 会全部回收
```

## TCP 压测实录(2026-10-11):10 万连接下 MetalLB 会怎样

先给结论:**MetalLB 不在数据面上,压不垮**。L2 模式只做控制面的一件事——应答 ARP("这个 IP 在我这"),之后的每个 TCP 包都走"客户端 → 节点内核 → kube-proxy(iptables)→ Pod",MetalLB 进程零参与。实测 7 万并发连接持续期间,speaker Pod CPU 只有 12-41m(基本空闲)。

### "一个 IP 只有 65535 连接"的真相

65535 限制的是 **TCP 四元组**:(源IP, 源端口, 目标IP, 目标端口)。同一个客户端 IP 打同一个 LB IP:80,源端口只有约 6.4 万个,这就是单源 ~64K 上限的来源;换一个源 IP,上限翻倍。它是内核 TCP 栈的约束,与 MetalLB 无关——**加客户端来源、或多开几个 LB 端口,上限就是 65535 的倍数**。

### 压测环境与实测数据

| 项 | 配置/结果 |
| --- | --- |
| 服务端 | nginx ×2 副本(worker_connections 100000),LoadBalancer IP 10.10.12.242 |
| 客户端 | 3 个 Pod 分钉三节点(3 个独立源 IP),经 LB 建 TCP keepalive 长连接 |
| 目标 | 25000 + 25000 + 70000 = 12 万(含单源 64K 上限实证) |
| 实测建立 | **稳定保持 ~7 万并发**(12267+9309+48739),nginx 两副本合计 61432 active |
| conntrack | 三节点合计 **16.4 万条**(每条连接在途经节点各占一条:客户端节点+转发节点+后端节点) |
| speaker CPU | 12-41m —— 控制面无感 |
| 失败率 | 无地址耗尽;约 10% 长连接被 RST(根因候选:SYN 突发期的 conntrack 竞争、L2 单 leader 承压,见下) |

### 真正的瓶颈链(按先后顺序)

1. **conntrack 表**(第一个撞线的):默认 `nf_conntrack_max=131072`,10 万连接分摊到途经节点就逼近;打满后 `table full, dropping packet` 新建连接直接被丢
2. **SNAT 源端口**:`externalTrafficPolicy: Cluster` 时跨节点流量做 SNAT,占用节点 IP 的 ~6.4 万临时端口;`Local` 模式不做 SNAT 但只调度到本节点 Pod
3. **L2 单 leader**:同一 LB IP 的全部流量走一个 leader 节点的内核栈——节点 4 核时,kube-proxy iptables + conntrack 就是单 IP 的吞吐天花板
4. **单源 64K**:客户端侧四元组限制,与集群无关

### 10 万连接配置清单(全部实测生效)

**节点内核**(每台,/etc/sysctl.d/99-tcp-scale.conf):

```ini
net.netfilter.nf_conntrack_max = 1048576          # 默认 131072,第一个撞线点
net.core.somaxconn = 65535                         # 默认 4096
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 65535
fs.file-max = 2097152
```

```bash
echo 262144 > /sys/module/nf_conntrack/parameters/hashsize    # 运行时
echo "options nf_conntrack hashsize=262144" > /etc/modprobe.d/nf_conntrack.conf   # 持久
```

**kube-proxy**:大规模 Service/连接数切 IPVS 模式(`kube-proxy-arg: proxy-mode=ipvs`),且 **IPVS 必须配 `strictARP: true`**(MetalLB L2 模式的官方要求,防 ARP 干扰);iptables 模式在 10 万并发下可用,但 IPVS 的 O(1) 查找和连接管理更抗量。

**应用侧**:ingress/nginx 的 `worker_rlimit_nofile`/`worker_connections` 同步拉到 10 万级,Pod 的 nofile 限额确认(容器默认 1M 通常够)。

**MetalLB 侧**:
- 单 IP 打满单节点后,横向方案是**多 LB IP 分散 leader**,或上 **BGP 模式**(三节点等价多路径 ECMP,流量摊到所有节点)——L2 模式的单 leader 是架构性上限
- 池地址避开 DHCP 段与 kube-vip 的 API 地址

**压测方法论坑(实测踩过)**:客户端连上不发请求,nginx `client_header_timeout` 60 秒就关连接,客户端却以为还握着——压测工具必须连上即发请求并周期心跳;epoll 水平触发下已连接 socket 会持续报 EPOLLOUT,事件分支写错会把活连接当失败全杀。

### 压测环境清理

```bash
kubectl delete svc hp-lb deploy nginx-hp cm nginx-hp-conf hp-client-script
kubectl delete pod hp-client-01 hp-client-02 hp-client-03 --force --grace-period=0
```
