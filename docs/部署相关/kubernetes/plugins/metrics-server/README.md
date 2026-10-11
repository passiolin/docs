# metrics-server

HPA 与 `kubectl top` 的数据源,采集节点/Pod 的资源用量。**RKE2 已内置**,不需要安装。

## 集群里的形态(2026-10-10 实测)

RKE2 v1.35.6 以内置 HelmChart `rke2-metrics-server` 交付,安装完成即用:

```bash
kubectl get pods -n kube-system | grep metrics-server
# rke2-metrics-server-6cf7c65cd8-mzmnc   1/1   Running

kubectl top node        # 实测输出节点 CPU/内存用量
kubectl top pods -A
```

上一轮笔记里"metrics-server 需要手动 apply components.yaml"的步骤对应 kubeadm 集群,在 RKE2 下是多余的——装两套反而会抢同一组 APIService。

## 验证与排障

- `kubectl top` 报 `Metrics API not available`:等 1-2 分钟让首次采集完成,再看 `kubectl get apiservice v1beta1.metrics.k8s.io` 是否 True。
- Pod 显示的内存是工作集(working set),不是 JVM 堆——Java 应用按堆配置 limits 时两套口径别混。
- 数据只存内存、不落盘,历史曲线归 Prometheus(第 9 篇,待写)。
