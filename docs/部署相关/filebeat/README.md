


```shell
docker run -d \
--name=filebeat \
--restart=always \
--user=root \
-v /data:/data \
-v /data/filebeat/conf/filebeat.yml:/usr/share/filebeat/filebeat.yml \
elastic/filebeat:7.17.9
```