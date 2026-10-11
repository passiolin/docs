# cert-manager

把"申请证书"变成声明式资源:创建 Certificate 对象,控制器自动签发并续期,产物是标准的 TLS Secret,给 Ingress/应用直接挂。2026-10-11 在三节点集群实测安装与签发。

## 安装(官方 manifest,实测 v1.21.2)

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
kubectl get pods -n cert-manager    # controller/cainjector/webhook 三件套,实测约 1 分钟 Running
```

镜像在 quay.io/jetstack/*,经 registries.yaml 的 mirror 拉取。helm 方式(jetstack repo + CRDs)等价,未实测。

## 签发实测:ClusterIssuer + Certificate

自签场景(内网 ingress 用,浏览器不信任但链路完整;对外域名换 ACME/HTTP-01,待实测):

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster
spec:
  selfSigned: {}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: demo-k8s-lab
  namespace: default
spec:
  secretName: demo-k8s-lab-tls
  dnsNames: [demo.k8s.lab]
  issuerRef:
    name: selfsigned-cluster
    kind: ClusterIssuer
```

实测 10 秒出证:`certificate.ready=True`,产出 `kubernetes.io/tls` 类型 Secret。

## 挂到 Ingress(实测)

```bash
kubectl patch ingress web-demo --type=merge -p '{"spec":{"tls":[{"hosts":["demo.k8s.lab"],"secretName":"demo-k8s-lab-tls"}]}}'
curl -sk -H "Host: demo.k8s.lab" https://10.10.12.141/     # 实测命中后端
```

ingress-nginx 的 443(hostPort)直接用签好的 Secret 终结 TLS,`-k` 是因为自签;换成正规 CA 签发后 `-k` 可去。

## 运维

```bash
kubectl get certificate -A                          # READY 列, False 看 describe 的 Events
kubectl get clusterissuers,issuers -A
kubectl delete certificate demo-k8s-lab && kubectl delete secret demo-k8s-lab-tls
```

与 Rancher 的关系:Rancher 依赖 cert-manager,装 Rancher 前必须先有它;Rancher 安装篇待整理,定稿后在此互链。
