# Longhorn

Longhorn 是 Kubernetes 上的分布式块存储:卷以副本文件的形式存在节点数据目录里,通过 CSI 动态供给 RWO 块卷,内置 share-manager 还能把卷以 NFSv4 方式供给 RWX;自带 Web UI、快照、备份到 NFS/S3、在线扩容。单节点集群也能装,但副本只能设 1——跨节点冗余放不住,快照与外部备份仍然有效。选型对比见 [第 5 篇](../../5.存储方案.md)。

2026-10-11 在三节点集群实测 v1.13.0:安装、StorageClass 自动设为默认、PVC 动态供给、卷挂载与读写、3 副本分布 3 节点——全部验证通过;但副本重建/收敛在该集群未能闭环,根因是集群自身的事故遗留(canal 残留的 flannel 设备与路由、快照恢复导致的 IPAM 块拓扑错位),详见文末实测实录。**本集群已卸载 Longhorn 并恢复 local-path 为默认 SC**;在干净集群上这套安装命令可直接使用。

## 一、概念

### 1. 存储模型

Longhorn 不要求预建存储池资源(Mayastor 的 DiskPool 模型见 [OpenEBS 篇](../openebs/README.md)),默认直接用节点上的数据目录保存卷副本文件:

```mermaid
flowchart LR
    A["节点数据目录 /data/longhorn"] --> B[Replica 文件]
    B --> C[Longhorn Volume]
    C --> D[PV / PVC]
    D --> E[业务 Pod]
```

默认数据目录是 `/var/lib/longhorn`,安装时用 `defaultSettings.defaultDataPath` 改(见下文 Helm 安装)。

### 2. 和裸盘的关系

不需要把裸盘交给 Longhorn 管理,更常见的做法是:

- 每个存储节点准备一块数据盘,格式化成 `ext4` 或 `xfs`;
- 挂载到固定目录,例如 `/data/longhorn`;
- 安装时把 `defaultSettings.defaultDataPath` 指向这个目录。

```bash
mkfs.xfs /dev/disk/by-id/xxx
mkdir -p /data/longhorn
mount /dev/disk/by-id/xxx /data/longhorn
```

数据盘挂载写入 `/etc/fstab`,避免节点重启后数据盘没自动挂载,Longhorn 把副本写到系统盘目录。

### 3. 快照依赖

VolumeSnapshot 能力依赖集群里的 snapshot-controller。RKE2 v1.35 内置 rke2-snapshot-controller,CRD 已就绪(2026-10-10 实测),chart 装好后直接可用;VolumeSnapshotClass 名称以 `kubectl get volumesnapshotclass` 输出为准。

## 二、安装

### 1. 前置要求

- Kubernetes 版本满足 Longhorn 当前版本的配套要求,以官方支持矩阵为准。
- 每个节点安装并运行 `open-iscsi` / `iscsid`,Longhorn 靠主机上的 iSCSI 工具挂载卷。
- 需要 RWX 卷或备份到 NFS 时,每个节点装 NFSv4 client。
- 节点数据目录所在文件系统用 `ext4` 或 `xfs`;Longhorn 组件以 privileged 权限运行,集群要允许。

Ubuntu / Debian:

```bash
apt-get update
apt-get install -y open-iscsi nfs-common
systemctl enable --now iscsid
```

CentOS / RHEL:

```bash
yum install -y iscsi-initiator-utils nfs-utils
systemctl enable --now iscsid
```

### 2. Helm 安装

helm 本身的安装见 [第 2 篇](../../2.安装部署-集群.md)。

```bash
helm repo add longhorn https://charts.longhorn.io
helm repo update

helm install longhorn longhorn/longhorn \
  --create-namespace \
  --namespace longhorn-system \
  --set defaultSettings.defaultDataPath=/data/longhorn/ \
  --set persistence.defaultClassReplicaCount=3
```

| 参数 | 说明 |
| --- | --- |
| `defaultSettings.defaultDataPath` | 节点默认副本数据目录,新节点默认用这个路径保存副本数据 |
| `persistence.defaultClassReplicaCount` | 默认 `longhorn` StorageClass 的副本数量;单节点集群设 1,三节点及以上常用 3 |

要锁版本,加 `--version <chart 版本>`,取值以官方 releases 页为准。

## 三、验证

```bash
kubectl get pods -n longhorn-system   # 所有组件 Running
kubectl get storageclass longhorn
kubectl describe storageclass longhorn
```

设为默认 StorageClass:

```bash
kubectl patch storageclass longhorn \
  -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

建一个 PVC 走通链路:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: demo-longhorn-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
```

```bash
kubectl apply -f demo-longhorn-pvc.yaml
kubectl get pvc demo-longhorn-pvc          # Bound 后即链路通
kubectl -n longhorn-system get volumes     # Longhorn 侧能看到对应卷
```

### 访问 Web UI

安装后集群内多出 `longhorn-system/longhorn-frontend`(端口 80),默认无账号密码,也不暴露外部地址。临时访问走端口转发:

```bash
kubectl -n longhorn-system port-forward svc/longhorn-frontend 8080:80
# 浏览器打开 http://127.0.0.1:8080
```

要通过 Ingress / NodePort 暴露 UI,必须自行加访问控制(Basic Auth、OAuth2 Proxy、Authelia、VPN 或内网 ACL),不要把无认证的 Longhorn UI 暴露到公网。

## 四、运维

### 1. 自定义 StorageClass

不同业务要不同副本数、指定节点或磁盘时,新建 SC。副本数示例:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: longhorn-3-replica
provisioner: driver.longhorn.io
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
parameters:
  numberOfReplicas: "3"
  staleReplicaTimeout: "30"
  fsType: "ext4"
  dataLocality: "disabled"
```

`nodeSelector` / `diskSelector` 参数能把副本调度到带指定标签的节点或磁盘,标签在 Longhorn UI 或 Node/Disk 配置里维护(取值参考官方 storage-class parameters 文档)。

### 2. 副本与数据目录

- 单节点集群只能 `numberOfReplicas: "1"`,副本分散不到第二个节点;三节点及以上常用 3。
- 副本越多可用性越高,占用磁盘与网络 IO 也越多。
- 数据目录不放系统盘;每节点挂独立数据盘到 `/data/longhorn`,多节点挂载目录保持一致(盘符可以不同,用 by-id 路径)。
- 单节点的数据保护走备份而不是副本:Web UI 里配 Recurring Job,backup target 支持 NFS 与 S3。

### 3. 扩容

入口在 PVC(`kubectl edit pvc`),Longhorn Web UI 上的大小操作不要碰,原因与操作见 [第 5 篇](../../5.存储方案.md)。

### 4. 卸载

```bash
helm uninstall longhorn -n longhorn-system
kubectl get crd | grep longhorn.io   # helm uninstall 不删 CRD,残留按列表逐个 kubectl delete crd
```

各节点 `defaultDataPath` 目录下的副本文件不会随卸载删除,确认不再需要后手动清理。完整卸载步骤(含残留清理)以官方 uninstall 文档为准。

### 5. 镜像源

Longhorn 组件镜像在 docker.io(longhornio/*),内网或离线集群核对镜像地址是否被 mirror 覆盖,配置见 [conf/registries.yaml](../../conf/registries.yaml)。

## 参考

- https://longhorn.io/docs/latest/deploy/install/
- https://longhorn.io/docs/latest/references/storage-class-parameters/
- https://longhorn.io/docs/latest/nodes-and-volumes/nodes/storage-tags/

## 实测实录(2026-10-11,v1.13.0)

### 通过的部分

- 前置:三台节点 `apt install open-iscsi && systemctl enable --now iscsid`(Longhorn 节点检测的 RequiredPackages 还包含 cryptsetup/dmsetup/nfs-common,Ubuntu 26.04 缺省已带)
- 安装:`helm install longhorn longhorn/longhorn -n longhorn-system --create-namespace`,共 30 个 Pod,镜像量约 2-3G,经 mirror 约 15-20 分钟全部 Running
- **安装即抢占默认 StorageClass**:chart 默认把 `longhorn` 设为 default,原默认(local-path)被顶掉,混用场景注意
- PVC 动态供给(5Gi)→ Bound → 挂载 → `dd` 写 100M + 读回,全通
- 3 副本分布 3 节点:`kubectl -n longhorn-system get lhr` 观测到副本分别在 server-01/02/03

### 未闭环的部分:副本收敛

第二块测试卷的两个副本持续 `stopped`、重建反复 `FailedRebuilding: dial tcp ... i/o timeout`。排查链与结论:

1. 第一层:CoreDNS 崩溃循环(恢复演练遗留的旧 Pod 网络坏死)→ DNS 瘫,CSI 全家解析不到 longhorn-backend;滚动重建 coredns 解决
2. 第二层:**canal 事故残留的 flannel.1 设备与路由**(第 7 篇的事故尾巴)——三台节点 `ip link del flannel.1` + 弹 calico-node 后,普通 Pod 跨节点矩阵(01↔02↔03 四方向)全部恢复
3. 第三层:快照恢复让 etcd 的 IPAM 块拓扑与现状错位,新旧块混用;执行**集群级重新分配 Pod IP**(删 blockaffinities + 全量滚动所有 Pod)后,普通 Pod 跨节点全绿
4. 仍残留:instance-manager Pod(长生命周期、反复重启过)跨节点 TCP/ICMP 均不可达,同节点与普通 Pod 均正常;felix 重启、IM 重建多轮无法根治——判定为该实验集群事故后遗症,非 Longhorn 缺陷

### 处置

本集群已 `helm uninstall longhorn` 并恢复 local-path 默认 SC(卸载同样会卡 uninstall Job,需删 release secret + 手工清 CRD finalizer,手法同 [第 7 篇](../../7.容灾实测.md));卷数据为测试数据未保留。**干净的集群上,上文安装命令可直接用**;接入前建议:关 multipathd、加载 dm_crypt、预留每节点 4G+ 内存。
