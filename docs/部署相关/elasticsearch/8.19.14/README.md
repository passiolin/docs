
### 生成证书和准备目录

```bash

cd /data
wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.19.14-linux-x86_64.tar.gz

tar -xf elasticsearch-8.19.14-linux-x86_64.tar.gz
mv elasticsearch-8.19.14 test-es-cert

tar -xf elasticsearch-8.19.14-linux-x86_64.tar.gz
mv elasticsearch-8.19.14 test-es-master

tar -xf elasticsearch-8.19.14-linux-x86_64.tar.gz
mv elasticsearch-8.19.14 test-es-data

tar -xf elasticsearch-8.19.14-linux-x86_64.tar.gz
mv elasticsearch-8.19.14 test-es-client


cd /data/test-es-cert

./bin/elasticsearch-certutil ca --pass "ipuff.online" --out /data/test-es-cert/elastic-stack-ca.p12

./bin/elasticsearch-certutil cert --ca-pass "ipuff.online" --ca elastic-stack-ca.p12 --pass "ipuff.online" --out /data/test-es-cert/config/elastic-certificates.p12

cp /data/test-es-cert/elastic-stack-ca.p12 /data
cp /data/test-es-cert/config/elastic-certificates.p12 /data/test-es-master/config
cp /data/test-es-cert/config/elastic-certificates.p12 /data/test-es-data/config
cp /data/test-es-cert/config/elastic-certificates.p12 /data/test-es-client/config

rm -rf /data/test-es-cert
```

### 启动主节点

```bash
cd /data/test-es-master/config

echo 'cluster.name: test-es-cluster
node.name: test-es-master-0
node.roles: ["master"]
network.host: 10.61.74.99
http.port: 9200
transport.port: 9300
discovery.seed_hosts: ["10.61.74.99:9300"]
cluster.initial_master_nodes: ["test-es-master-0"]


xpack.security.enabled: true
xpack.security.transport.ssl.enabled: true
xpack.security.transport.ssl.verification_mode: certificate
xpack.security.transport.ssl.keystore.path: /data/test-es-master/config/elastic-certificates.p12
xpack.security.transport.ssl.truststore.path: /data/test-es-master/config/elastic-certificates.p12
xpack.security.transport.ssl.keystore.password: "ipuff.online"
xpack.security.transport.ssl.truststore.password: "ipuff.online"
' > elasticsearch.yml


echo '-Xms1g
-Xmx1g

-XX:+UseG1GC
-Djava.io.tmpdir=${ES_TMPDIR}
20-:--add-modules=jdk.incubator.vector
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.setAsTypeCache
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.asTypeUncached

-Dorg.apache.lucene.store.MMapDirectory.sharedArenaMaxPermits=1

-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
-XX:ErrorFile=hs_err_pid%p.log
-Xlog:gc*,gc+age=trace,safepoint:file=gc.log:utctime,level,pid,tags:filecount=32,filesize=64m
' > jvm.options


chown -R ubuntu /data/test-es-*

su ubuntu
cd /data/test-es-master
./bin/elasticsearch -d

exit
```

### 启动数据节点

```bash
cd /data/test-es-data/config

echo 'cluster.name: test-es-cluster
node.name: test-es-data-0
node.roles: ["data"]
network.host: 10.61.74.99
http.port: 9201
transport.port: 9301
discovery.seed_hosts: ["10.61.74.99:9300"]
cluster.initial_master_nodes: ["test-es-master-0"]


xpack.security.enabled: true
xpack.security.transport.ssl.enabled: true
xpack.security.transport.ssl.verification_mode: certificate
xpack.security.transport.ssl.keystore.path: /data/test-es-data/config/elastic-certificates.p12
xpack.security.transport.ssl.truststore.path: /data/test-es-data/config/elastic-certificates.p12
xpack.security.transport.ssl.keystore.password: "ipuff.online"
xpack.security.transport.ssl.truststore.password: "ipuff.online"
' > elasticsearch.yml


echo '-Xms1g
-Xmx1g

-XX:+UseG1GC
-Djava.io.tmpdir=${ES_TMPDIR}
20-:--add-modules=jdk.incubator.vector
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.setAsTypeCache
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.asTypeUncached

-Dorg.apache.lucene.store.MMapDirectory.sharedArenaMaxPermits=1

-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
-XX:ErrorFile=hs_err_pid%p.log
-Xlog:gc*,gc+age=trace,safepoint:file=gc.log:utctime,level,pid,tags:filecount=32,filesize=64m
' > jvm.options


chown -R ubuntu /data/test-es-*

su ubuntu
cd /data/test-es-data
./bin/elasticsearch -d

exit

```

### 启动客户端节点

```bash


cd /data/test-es-client/config

echo 'cluster.name: test-es-cluster
node.name: test-es-client-0
node.roles: []
network.host: 10.61.74.99
http.port: 9202
transport.port: 9302
discovery.seed_hosts: ["10.61.74.99:9300"]
cluster.initial_master_nodes: ["test-es-client-0"]


xpack.security.enabled: true
xpack.security.transport.ssl.enabled: true
xpack.security.transport.ssl.verification_mode: certificate
xpack.security.transport.ssl.keystore.path: /data/test-es-client/config/elastic-certificates.p12
xpack.security.transport.ssl.truststore.path: /data/test-es-client/config/elastic-certificates.p12
xpack.security.transport.ssl.keystore.password: "ipuff.online"
xpack.security.transport.ssl.truststore.password: "ipuff.online"
' > elasticsearch.yml


echo '-Xms1g
-Xmx1g

-XX:+UseG1GC
-Djava.io.tmpdir=${ES_TMPDIR}
20-:--add-modules=jdk.incubator.vector
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.setAsTypeCache
23:-XX:CompileCommand=dontinline,java/lang/invoke/MethodHandle.asTypeUncached

-Dorg.apache.lucene.store.MMapDirectory.sharedArenaMaxPermits=1

-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
-XX:ErrorFile=hs_err_pid%p.log
-Xlog:gc*,gc+age=trace,safepoint:file=gc.log:utctime,level,pid,tags:filecount=32,filesize=64m
' > jvm.options


chown -R ubuntu /data/test-es-*

su ubuntu
cd /data/test-es-client
./bin/elasticsearch -d

exit

```



```bash

cat > /etc/systemd/system/test-es-client.service << 'EOF'
[Unit]
Description=Elasticsearch
After=network.target

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/data/test-es-client
ExecStart=/data/test-es-client/bin/elasticsearch -d
ExecStop=/bin/kill -SIGTERM $MAINPID
Restart=on-failure
RestartSec=10


[Install]
WantedBy=multi-user.target
EOF

# 重载 systemd 并启动服务
systemctl daemon-reload
systemctl start test-es-client
systemctl enable test-es-client
systemctl status test-es-client

cat > /etc/systemd/system/test-es-data.service << 'EOF'
[Unit]
Description=Elasticsearch
After=network.target

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/data/test-es-data
ExecStart=/data/test-es-data/bin/elasticsearch -d
ExecStop=/bin/kill -SIGTERM $MAINPID
Restart=on-failure
RestartSec=10


[Install]
WantedBy=multi-user.target
EOF

# 重载 systemd 并启动服务
systemctl daemon-reload
systemctl start test-es-data
systemctl enable test-es-data
systemctl status test-es-data


cat > /etc/systemd/system/test-es-master.service << 'EOF'
[Unit]
Description=Elasticsearch
After=network.target

[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/data/test-es-master
ExecStart=/data/test-es-master/bin/elasticsearch -d
ExecStop=/bin/kill -SIGTERM $MAINPID
Restart=on-failure
RestartSec=10


[Install]
WantedBy=multi-user.target
EOF

# 重载 systemd 并启动服务
systemctl daemon-reload
systemctl start test-es-master
systemctl enable test-es-master
systemctl status test-es-master

```