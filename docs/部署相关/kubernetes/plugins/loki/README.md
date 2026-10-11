# Loki + promtail(日志)

指标有 kube-prometheus-stack(第 9 篇),日志这一块由 Loki 补齐:promtail 以 DaemonSet 收集各节点容器日志,推给 Loki 存储,查询走 LogQL,看板复用现有的 Grafana。2026-10-11 实测,四 Pod 全 Running,日志流与标签维度验证通过。

## 安装(helm,实测)

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm install loki grafana/loki-stack -n logging --create-namespace
```

产出:`loki-0`(单副本存储)+ `loki-promtail-xxx`(每节点一个)。镜像 grafana/loki、grafana/promtail 走 mirror,约 3-5 分钟就绪。单副本 Loki 无高可用,重启期间日志短暂断流(容器日志文件还在,恢复后继续收)。

## 验证(实测)

```bash
kubectl -n logging get pods        # 4 个全 Running
# 经 ClusterIP 查询已收到的标签维度(宿主机解析不了 svc 名,用 IP)
IP=$(kubectl -n logging get svc loki -o jsonpath="{.spec.clusterIP}")
curl -s "http://$IP:3100/loki/api/v1/labels"
# 实测返回:app/component/container/filename/instance/job/namespace/node_name
```

promtail 自动发现容器日志并打上这 8 个维度标签,业务日志不用做任何改造。

## 接入 Grafana(实测)

利用 kube-prometheus-stack 自带的 datasource sidecar:打上 `grafana_datasource: "1"` 标签的 Secret 会被自动加载,免重启:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: loki-datasource
  namespace: monitoring
  labels:
    grafana_datasource: "1"
stringData:
  loki-datasource.yaml: |-
    apiVersion: 1
    datasources:
    - name: Loki
      type: loki
      access: proxy
      url: http://loki.logging.svc.cluster.local:3100
      isDefault: false
```

sidecar 日志出现 `Datasources config reloaded` 即生效,Explore 页选 Loki 即可查。常用 LogQL:

```logql
{namespace="default"} |= "error"          # 按命名空间过滤关键字
{app="web-demo"}                          # 单个业务的全量日志
sum by (pod) (rate({job=~".+"}[5m]))      # 日志速率(异常爆发检测)
```

## 卸载

```bash
helm uninstall loki -n logging && kubectl delete ns logging
kubectl -n monitoring delete secret loki-datasource
```
