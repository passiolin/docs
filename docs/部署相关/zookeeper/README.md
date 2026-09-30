

### ENV

ZK_SERVER_HEAP default 1000


### single
- docker 

```shell
mkdir -p /data/zookeeper/conf

docker run -d \
--net=host \
-v /data/zookeeper/data:/opt/zookeeper/data \
-v /data/zookeeper/logs:/opt/zookeeper/datalog \
-v /data/zookeeper/conf:/opt/zookeeper/conf \
--name=zookeeper \
--restart=always \
mirrors.ipuff.online/component/zookeeper:3.8.5

chown -R 1000:1000 /data/zookeeper
```

- conf
```yaml
tickTime=2000
initLimit=10
syncLimit=5
dataDir=/opt/zookeeper/data
dataLogDir=/opt/zookeeper/datalog
clientPort=2181
```



### cluster

- docker 
```shell

echo 1 > /data/zookeeper/data/myid

docker run -d \
--net=host \
--name=zookeeper-2 \
--restart=always \
-e ZK_SERVER_HEAP=2048 \
-v /data/zookeeper/data:/opt/zookeeper/data \
-v /data/zookeeper/logs:/opt/zookeeper/datalog \
-v /data/zookeeper/conf:/opt/zookeeper/conf \
overseas-mirrors.dl-aiot.com/dl/public/zookeeper:3.9.1
```

- conf
```yaml
tickTime=2000
initLimit=10
syncLimit=5
dataDir=/opt/zookeeper/data
dataLogDir=/opt/zookeeper/datalog
clientPort=2181
server.0=10.52.173.219:2888:3888
server.1=10.52.161.252:2888:3888
server.2=10.52.187.147:2888:3888
metricsProvider.className=org.apache.zookeeper.metrics.prometheus.PrometheusMetricsProvider
```