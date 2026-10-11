# NFS(nfs-subdir-external-provisioner)

nfs-subdir-external-provisioner 把一个现成的 NFS export 接成 K8s 动态供给器:每个 PVC 在 export 下生成一个子目录(路径由 `pathPattern` 控制),以 RWX 文件卷挂给 Pod。K8s 侧无副本、无快照、无在线扩容,数据安全完全取决于 NFS server 本身;已经有 NAS 或宿主机 export 的场景,它是最省事的 RWX 方案。选型对比见 [第 5 篇](../../5.存储方案.md)。

上游状态:kubernetes-sigs/nfs-subdir-external-provisioner 已不再活跃维护,存量使用没有问题;新部署可评估 kubernetes-csi/csi-driver-nfs,本篇不覆盖。**2026-10-11 已在三节点集群实测**(安装、PVC 动态供给、跨节点 RWX 读写全通过),实测记录见文末;chart 未钉版本,安装前对一眼所用 chart 版本的 values.yaml。

## 一、安装(Helm)

helm 本身的安装见 [第 2 篇](../../2.安装部署-集群.md)。

```bash
helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
helm repo update

helm install nfs-subdir-external-provisioner \
  nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
  --create-namespace \
  --namespace nfs-system \
  --set replicaCount=1 \
  --set nfs.server=10.10.12.x \
  --set nfs.path=/data/nfs/k8s \
  --set storageClass.defaultClass=true

# nfs.server / nfs.path 换成真实 NFS server 地址与 export 路径
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `nfs.server` / `nfs.path` | —— | NFS server 地址与 export 路径 |
| `storageClass.name` | `nfs-client` | chart 创建的 StorageClass 名称 |
| `storageClass.defaultClass` | `false` | 设为 `true` 会抢占集群默认 SC;已有默认 SC(如 Longhorn)的集群保持 `false`,业务 PVC 显式写 `storageClassName` |
| `storageClass.archiveOnDelete` | `true` | 删除 PVC 时 server 端目录改名 `archived-*` 而非真删 |
| `storageClass.pathPattern` | `${namespace}-${pvcName}-${pvName}` | 每个卷在 export 下的子目录名模板 |

NFS server 侧:export 要允许 provisioner 在 export 下创建、删除、改名子目录,常见配法是对集群节点网段开放 `rw` + `no_root_squash`(provisioner 以 root 身份操作)。

镜像源:镜像在 registry.k8s.io/sig-storage,内网或离线集群核对该地址是否被 mirror 覆盖,配置见 [conf/registries.yaml](../../conf/registries.yaml)。

## 二、验证

```bash
kubectl get pods -n nfs-system    # provisioner Running
kubectl get sc                    # nfs-client 出现,defaultClass=true 时带 (default) 标注
```

RWX 的关键是多 Pod 同挂一卷互见,起一个两副本 Deployment 验证:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: demo-nfs-pvc
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: nfs-client
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-nfs-rwx
spec:
  replicas: 2
  selector:
    matchLabels:
      app: demo-nfs-rwx
  template:
    metadata:
      labels:
        app: demo-nfs-rwx
    spec:
      containers:
        - name: main
          image: busybox
          command: ["sh", "-c", "sleep 3600"]
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: demo-nfs-pvc
```

```bash
kubectl apply -f demo-nfs.yaml
kubectl get pvc demo-nfs-pvc                 # Bound,且两个 Pod 都 Running
# 两个 Pod 互写互读,RWX 生效
kubectl exec deploy/demo-nfs-rwx -- sh -c 'echo one > /data/a.txt'
kubectl exec deploy/demo-nfs-rwx -- cat /data/a.txt
# NFS server 上确认子目录生成
ls /data/nfs/k8s/
```

## 三、运维

### 1. 回收

`reclaimPolicy` 默认 Delete;`archiveOnDelete: true`(默认)时删 PVC 只把 server 端目录改名 `archived-<原名>`,数据还在——误删保护,空间要人工去 server 端回收。

### 2. 扩容

该 provisioner 不支持在线扩容,SC 未开 `allowVolumeExpansion`。PVC 里写的容量只是目录配额参考,实际可用空间受 NFS server 文件系统限制,空间不够在 server 端扩 export 底下的文件系统。K8s 侧卷扩容的通用原理见 [第 5 篇](../../5.存储方案.md)。

### 3. 卸载

```bash
helm uninstall nfs-subdir-external-provisioner -n nfs-system
kubectl get sc   # 确认 nfs-client 是否残留,残留则 kubectl delete sc nfs-client
```

已创建的 PV/PVC 和 server 端数据不受卸载影响,只是不再供给新卷;export 下的子目录是普通文件,`rsync` 即可迁移。

## 参考

- https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner

## 实测记录(2026-10-11)

- 服务端:节点 k8s-server-01 的 nfs-kernel-server,export `/srv/nfs-k8s`(仅 10.10.12.0/24)
- 客户端:三台节点 `apt install nfs-common`
- provisioner:helm 安装,SC `nfs-client`(非默认);Pod 约 1 分钟 Running
- RWX 验证:1Gi RWX PVC Bound 后,Pod-A(钉 141)写入 `written-on-141`,Pod-B(钉 142)25 秒内读到同文件——跨节点共享读写成立
- 注意:测试用 reader Pod 用了 `restartPolicy: Always`,读完即退会进入 CrashLoopBackOff 重启循环,属预期现象,删掉即可
