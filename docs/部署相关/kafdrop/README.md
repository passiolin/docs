

```shell
docker run -d \
--name=kafdrop \
--restart=always \
-p 9003:9000 \
-e KAFKA_BROKERCONNECT="10.15.0.107:9092" \
-e JVM_OPTS="-Xmx512m" \
obsidiandynamics/kafdrop
```