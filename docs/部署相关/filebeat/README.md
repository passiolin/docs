# filebeat

宿主机日志采集容器,挂 `/data` 与 `filebeat.yml`,输出到 Elasticsearch 7.17 集群。

```shell
docker run -d \
--name=filebeat \
--restart=always \
--user=root \
-v /data:/data \
-v /data/filebeat/conf/filebeat.yml:/usr/share/filebeat/filebeat.yml \
elastic/filebeat:7.17.9
```
