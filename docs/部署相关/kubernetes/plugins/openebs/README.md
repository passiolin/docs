# OpenEBS

OpenEBS 是一组可单独启用的存储引擎:Local PV 系列(HostPath、LVM、ZFS)把本节点的目录或卷管理能力供给成无副本的本地卷,Replicated 系列(Mayastor)提供跨节点同步复制卷;总 chart 用 `engines.*.enabled` 只装需要的引擎。单节点集群用 Local PV 系列即可,Mayastor 的多副本在单节点上放不住,留作扩展到多节点时的选项。选型对比见 [第 5 篇](../../5.存储方案.md)。

2026-10-11 在三节点集群(PVE 虚机)实测了四种路径中的三种:**Local PV HostPath、LVM LocalPV、ZFS LocalPV 全部通过**,Mayastor(Replicated PV)在三台虚机各加一块独占 thin 盘 + 1024 个 2MiB 大页后**也完整跑通,含跨节点复制卷验证**。安装方式的两个关键结论:umbrella 总 chart 与 RKE2 内置 snapshot-controller 的 CRD 所有权冲突,且 `openebs-crds` 子 chart 不受 `enabled=false` 控制——各引擎请用独立 chart 安装(见文末实测记录)。

## 一、引擎分类

### 1. Local PV

只把本节点的磁盘、目录或本地卷管理能力暴露成 PV。Pod 必须调度到 PV 所在节点,节点故障时数据不会自动在其他节点可用。

| 引擎 | 底层介质 | 特点 | 限制 |
| --- | --- | --- | --- |
| HostPath | 节点上的目录 | 最简单,维护成本低,适合测试、缓存、轻量服务;安装后自动建 `openebs-hostpath` SC | 无存储层快照、克隆、复制;节点故障靠应用或备份恢复 |
| LVM LocalPV | 节点上的 LVM VG | 存储池、精简配置、在线扩容、快照;性能接近本地盘 | 节点要预先建 VG;仍是单节点本地卷 |
| ZFS LocalPV | 节点上的 ZFS pool | 快照、克隆、精简配置、压缩等 ZFS 能力 | 节点要预先建 pool;仍是单节点本地卷 |
| Device LocalPV | 独占块设备 | 接近裸盘性能,适合单负载独占磁盘 | 不支持共享池、精简配置、扩容、快照 |
| Rawfile LocalPV | 目录中的文件模拟块设备 | 目录型存储上提供块设备语义 | 场景较少,主要用于兼容 |

### 2. Replicated PV

在多个节点保存同一卷的完整副本并同步写入,节点故障后从其他副本继续服务,代价是容量与网络 IO(10Gi 三副本占 30Gi)。

| 引擎 | 特点 | 适合场景 | 限制 |
| --- | --- | --- | --- |
| Mayastor | 当前主推的复制引擎,基于 NVMe-oF/SPDK,目标低延迟高吞吐 | 有 NVMe SSD 和充足 CPU/内存/网络,且需要存储层高可用 | 部署要求高:独占裸盘、DiskPool、HugePages 等前置条件 |
| cStor | 基于池的复制引擎,支持快照、克隆、扩容 | 存量集群维护 | 性能开销大;v4 总 chart 已不再提供 `cstor.enabled` 开关 |
| Jiva | 较简单的复制引擎,用本地目录存副本 | 小容量、需同步复制的存量场景 | 功能少,不适合大容量/高性能;v4 总 chart 已不再提供 `jiva.enabled` 开关 |

新集群需要复制存储时用 Mayastor,cStor/Jiva 只做存量维护。快速判断:

- 应用自己做高可用,要动态本地卷:HostPath,或要快照/扩容时选 LVM、ZFS LocalPV。
- 只想快速跑起来:HostPath。
- 应用不做副本、要存储层跨节点保护:Mayastor(需多节点)。

## 二、安装

OpenEBS v4 起用新仓库 `https://openebs.github.io/openebs`。生产环境只装实际会用的引擎,多装的组件只会增加排障面。

```bash
helm repo add openebs https://openebs.github.io/openebs
helm repo update
```

helm 本身的安装见 [第 2 篇](../../2.安装部署-集群.md)。以下命令按引擎选择其一执行,不要把多个"原子安装命令"重复装到同一个 release。

HostPath LocalPV:

```bash
helm install openebs openebs/openebs -n openebs --create-namespace \
  --set engines.local.lvm.enabled=false \
  --set engines.local.zfs.enabled=false \
  --set engines.local.rawfile.enabled=false \
  --set engines.replicated.mayastor.enabled=false \
  --set localpv-provisioner.localpv.basePath="/data/openebs" \
  --set localpv-provisioner.hostpathClass.enabled=true \
  --set localpv-provisioner.hostpathClass.basePath="/data/openebs" \
  --set localpv-provisioner.hostpathClass.name=openebs-hostpath \
  --set localpv-provisioner.hostpathClass.isDefaultClass=true
```

LVM LocalPV:

```bash
helm install openebs openebs/openebs -n openebs --create-namespace \
  --set engines.local.lvm.enabled=true \
  --set engines.local.zfs.enabled=false \
  --set engines.local.rawfile.enabled=false \
  --set engines.replicated.mayastor.enabled=false
```

ZFS LocalPV:

```bash
helm install openebs openebs/openebs -n openebs --create-namespace \
  --set engines.local.lvm.enabled=false \
  --set engines.local.zfs.enabled=true \
  --set engines.local.rawfile.enabled=false \
  --set engines.replicated.mayastor.enabled=false
```

Rawfile LocalPV:

```bash
helm install openebs openebs/openebs -n openebs --create-namespace \
  --set engines.local.lvm.enabled=false \
  --set engines.local.zfs.enabled=false \
  --set engines.local.rawfile.enabled=true \
  --set engines.replicated.mayastor.enabled=false
```

Mayastor Replicated PV:

```bash
helm install openebs openebs/openebs -n openebs --create-namespace \
  --set engines.local.lvm.enabled=false \
  --set engines.local.zfs.enabled=false \
  --set engines.local.rawfile.enabled=false \
  --set engines.replicated.mayastor.enabled=true
```

装完后:

- HostPath 和 Rawfile 自动创建 StorageClass。
- LVM 和 ZFS 装的是 CSI 驱动,不认识节点上已有的 VG/pool 名称,要按实际环境手工建 SC(见下节)。
- Mayastor 装完会出 `openebs-single-replica` SC;多副本要先建 DiskPool,再建带 `repl` 参数的 SC。

镜像源:OpenEBS 组件镜像主要在 docker.io,个别组件会引用其他 registry,内网/离线集群先核对镜像地址是否被 mirror 覆盖,配置见 [conf/registries.yaml](../../conf/registries.yaml)。

## 三、StorageClass 与卷

### 1. HostPath(安装即用)

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: demo-hostpath-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: openebs-hostpath
  resources:
    requests:
      storage: 10Gi
```

```bash
kubectl apply -f demo-hostpath-pvc.yaml
kubectl get pvc demo-hostpath-pvc   # Bound 后,数据落在节点 /data/openebs 下
```

### 2. LVM(先在节点建 VG)

```bash
# 节点上准备 VG,盘用 by-id 稳定路径
pvcreate /dev/disk/by-id/xxx
vgcreate openebs-vg /dev/disk/by-id/xxx
```

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: openebs-lvm
allowVolumeExpansion: true
parameters:
  storage: lvm
  volgroup: openebs-vg
provisioner: local.csi.openebs.io
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
```

### 3. ZFS(先在节点建 pool)

```bash
zpool create -o ashift=12 openebs-zpool /dev/disk/by-id/xxx
```

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: openebs-zfs
allowVolumeExpansion: true
parameters:
  poolname: openebs-zpool
provisioner: zfs.csi.openebs.io
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
```

LVM / ZFS 的 SC 建好后:`kubectl apply -f <sc>.yaml && kubectl describe storageclass <name>`。

### 4. Mayastor(先建 DiskPool)

DiskPool 是 Mayastor 接管一块独占裸盘后在节点上创建的存储池对象,不是业务直接挂的文件系统:

```mermaid
flowchart LR
    A[裸盘 / 块设备] --> B[DiskPool]
    B --> C[Volume Replica]
    C --> D[PVC / PV]
```

- 用 `/dev/disk/by-id/...` 稳定路径,不用 `/dev/sdb`(重启后盘符漂移);不用系统盘,不用已被格式化、挂载、加入 LVM/ZFS 或被其他服务占用的盘。
- 3 副本要在 3 个不同节点各建一个 DiskPool,`spec.node` 写 K8s 节点名,`disks` 写该节点本机路径。
- `apiVersion` 以所装版本的 CRD 为准,查法:`kubectl get crd diskpools.openebs.io -o jsonpath='{.spec.versions[*].name}'`。

```yaml
apiVersion: openebs.io/v1beta1
kind: DiskPool
metadata:
  name: pool-node-1
  namespace: openebs
spec:
  node: k8s-node-1
  disks:
    - aio:///dev/disk/by-id/nvme-SAMSUNG_SSD_node1_disk
```

多副本 StorageClass:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: openebs-mayastor-3-replica
allowVolumeExpansion: true
parameters:
  protocol: nvmf
  repl: "3"
provisioner: io.openebs.csi-mayastor
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

## 四、运维

### 1. 验证

```bash
kubectl get pods -n openebs          # 对应引擎的组件 Running
kubectl get storageclass             # 出现对应 SC
kubectl get pvc                      # demo PVC Bound
```

### 2. 默认 StorageClass

HostPath 安装参数 `hostpathClass.isDefaultClass=true` 已设默认;装完再改用 patch:

```bash
kubectl patch storageclass <storage-class-name> \
  -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

### 3. 扩容

仅 LVM / ZFS 引擎支持在线扩容,入口在 PVC(`kubectl edit pvc`),要求 SC 开了 `allowVolumeExpansion` 且 VG / pool 有剩余空间;HostPath、Device、Rawfile 不支持,只能迁移重建。原理与排查见 [第 5 篇](../../5.存储方案.md)。

### 4. 卸载

```bash
helm uninstall openebs -n openebs
kubectl get crd | grep openebs.io   # CRD 不随卸载删除,确认不再用后逐个清理
```

卷数据不随卸载消失:HostPath 留在节点 `/data/openebs` 目录,LVM 的 LV 留在 VG 里(`lvremove` 回收),ZFS 的 dataset 留在 pool 里(`zfs destroy` 回收),都在节点上手动处理。

## 参考

- https://openebs.io/docs/concepts/data-engines/localstorage
- https://openebs.io/docs/concepts/data-engines/replicated-storage
- https://openebs.github.io/openebs/

## 实测记录(2026-10-11)

- **umbrella 总 chart 的坑**:`helm install openebs/openebs` 时 `openebs-crds` 子 chart 不受 `enabled=false` 控制,强制安装 VolumeSnapshot 三件套 CRD,与 RKE2 内置 snapshot-controller 的 CRD 所有权冲突,安装直接失败(`invalid ownership metadata`)。`--set crds.enabled=false`、关引擎子 chart 均绕不开
- **实测解法:改装独立 chart**,只含 hostpath 动态供给,无冲突:

```bash
helm repo add openebs-localpv https://openebs.github.io/dynamic-localpv-provisioner
helm install openebs-localpv openebs-localpv/localpv-provisioner -n openebs --create-namespace
```

- 产出 SC `openebs-hostpath`(WaitForFirstConsumer);1Gi PVC Bound、Pod 挂载读写全通
- 与 local-path 功能等价(单节点本地目录、RWO),**二选一即可**;本集群两者并存纯属实测需要
- Mayastor 需要独占块盘、hugepages 与专用内核模块,PVE 虚机共享盘环境不适用,未测

## 实测记录二:LVM/ZFS/Mayastor(2026-10-11)

前置:给三台 PVE 虚机各热加三块盘(20G LVM / 20G ZFS / 10G Mayastor 池),`qm set <vmid> -scsi1 local-lvm:20` 即插即用。

### LVM LocalPV ✅

```bash
pvcreate /dev/sdb && vgcreate vg-openebs /dev/sdb      # 每台节点
helm repo add lvm-localpv https://openebs.github.io/lvm-localpv
```

- chart 同样捆绑 snapshot CRD,`helm install` 会撞所有权冲突,`--skip-crds` 也无效(CRD 在 templates 里)——与 umbrella 同源问题。实测解法:`helm template` 后过滤三件套 CRD 再 apply,并注意 chart 把 ServiceAccount 硬编码在 `namespace: openebs`、工作负载落在 default,两边要对齐
- SC 参数 `vgPattern: "vg-openebs"`;PVC Bound 后在 VG 里可见对应 LV
- **在线扩容实测通过**:PVC 2Gi→3Gi,LV 与 Pod 内文件系统三层同步增长,Pod 无感

### ZFS LocalPV ✅

```bash
apt install -y zfsutils-linux      # 内核 7.0 原生带 zfs 模块
zpool create -f zp-openebs /dev/sdc
helm repo add zfs-localpv https://openebs.github.io/zfs-localpv
```

- 同样需要 template 过滤 snapshot CRD;SC 参数 `poolname: "zp-openebs"`
- PVC Bound 后 `zfs list` 可见对应数据集;35 秒全链路完成,四组件一次就绪

### Replicated PV Mayastor ✅(含跨节点复制验证)

前置:每台节点独占盘一块(实测 /dev/sdd 10G)、`vm.nr_hugepages=1024`(2MiB,需重启前写入 sysctl)、`modprobe nvme_tcp`。

```bash
helm repo add mayastor https://openebs.github.io/mayastor-extensions/   # 注意:mayastor/ 仓库已 404
helm show crds mayastor/mayastor | kubectl apply -f -                    # snapshot CRD 仍需跳过,见 openebs-crds 坑
helm template mayastor mayastor/mayastor -n mayastor | kubectl apply -n mayastor -f -
kubectl label node <各节点> openebs.io/engine=mayastor                    # io-engine DS 的调度标签
```

实测踩坑,逐条都是安装成功与否的分水岭:

1. **仓库迁移**:openebs.github.io/mayastor/ 已 404,现址 mayastor-extensions/
2. umbrella 总 chart 的 `mayastor:` 键指向 v2.8 extensions(只装监控扩展,不装 io-engine),v4 引擎要用独立 mayastor chart(v2.12.2)
3. **DiskPool 的 apiVersion 是 `openebs.io/v1beta3`**(不是 mayastor.openebs.io),且 v1beta3 不接受 `spec.type` 字段
4. **池设备必须用持久路径**:`/dev/sdd` 会被拒(`Invalid devlink`),用 `/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_drive-scsi3`
5. io-engine 默认请求 **2 整核**/节点,资源紧的节点会 Pending,按需 patch 降请求
6. 组件的模板 namespace 字段为空,`helm template` 直配时必须 `kubectl apply -n mayastor`,否则组件散落到 default 起不来

验证:DiskPool ×3 全部 `Online`(10GiB,Healthy);3 副本 PVC(repl: 3)Bound;Pod 钉在 141 写入数据→杀掉→Pod 换到 143 重新挂卷,**数据原样读出**——跨节点复制卷闭环。

### 引擎选择速查(本集群实测后)

| 引擎 | SC | 副本 | 用途 | 状态 |
| --- | --- | --- | --- | --- |
| local-path(localpv-provisioner) | local-path | 无 | 通用默认 | ✅ |
| NFS(nfs-subdir) | nfs-client | 无(靠服务端) | RWX 共享 | ✅ |
| OpenEBS HostPath | openebs-hostpath | 无 | 与 local-path 等价,二选一 | ✅ |
| OpenEBS LVM | openebs-lvm | 无 | 要在线扩容/快照的本地卷 | ✅ |
| OpenEBS ZFS | openebs-zfs | 无 | 要 ZFS 能力的本地卷 | ✅ |
| Mayastor | mayastor-single-replica 等 | 1-3 | 跨节点复制块存储 | ✅ |

### 资源注意(实测)

io-engine 每节点请求 2 核 + 1Gi 内存、etcd 每成员 250m,4C/8G 的节点在跑满全套(监控/日志/LVM/ZFS)后 CPU 请求余量见底,个别 Pod 会 Pending。实验集群处理:patch io-engine 请求到 500m(功能不受影响,性能按需回补);etcd 出现 2/3 成员仍保持仲裁可用,但生产化必须给 io-engine 留足整核。
