

### ENV
ENV KAFKA_HEAP_OPTS default 1g 

```properties
echo "broker.id=2
listeners=PLAINTEXT://:9092
advertised.listeners=PLAINTEXT://10.52.187.147:9092
num.network.threads=3
num.io.threads=8
socket.send.buffer.bytes=102400
socket.receive.buffer.bytes=102400
socket.request.max.bytes=104857600
log.dirs=/opt/kafka/data
num.partitions=3
num.recovery.threads.per.data.dir=1
offsets.topic.replication.factor=2
transaction.state.log.replication.factor=2
transaction.state.log.min.isr=2
log.retention.hours=24
log.segment.bytes=1073741824
log.retention.check.interval.ms=300000
zookeeper.connect=10.52.173.219:2181,10.52.161.252:2181,10.52.187.147:2181/kafka
zookeeper.connection.timeout.ms=18000
group.initial.rebalance.delay.ms=0" > /data/kafka/conf/server.properties
```


```shell
mkdir -p /data/kafka/conf/

docker run -d \
--net=host \
--name=kafka \
--restart=always \
-e KAFKA_HEAP_OPTS="-Xmx2g -Xms2g" \
-v /data/kafka/data:/opt/kafka/data \
-v /data/kafka/logs:/opt/kafka/logs \
-v /data/kafka/conf/server.properties:/opt/kafka/config/server.properties \
mirrors.ipuff.online/component/kafka:2.8.2

chown -R 1000:1000 /data/kafka
```


```shell
docker run -d \
--net=host \
--name=kafka \
--restart=always \
-e KAFKA_HEAP_OPTS="-Xmx1g -Xms1g" \
-v /data/kafka/data:/opt/kafka/data \
-v /data/kafka/logs:/opt/kafka/logs \
-v /data/kafka/conf/server.properties:/opt/kafka/config/server.properties \
mirrors.ipuff.online/ipuff/public/kafka:2.8.2
```



kraft

```bash
cat > /etc/systemd/system/kafka.service << 'EOF'
[Unit]
Description=Apache Kafka (KRaft mode)
After=network.target

[Service]
Type=forking
User=root
Group=root
WorkingDirectory=/data/kafka
Environment="JAVA_HOME=/data/zulujdk"
ExecStart=/data/kafka/bin/kafka-server-start.sh -daemon /data/kafka/config/kraft/server.properties
ExecStop=/data/kafka/bin/kafka-server-stop.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 重载 systemd 并启动服务
systemctl daemon-reload
systemctl start kafka
systemctl enable kafka
systemctl status kafka
```