

```shell
docker build -f Dockerfile -t mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 .
```

```sh
docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-java-0 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-java-0:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 78e72020f52da6eb5831ed3d011eab7f827017cb955a7c6f73e69a0a8a567622  \
-name "slave-java-0" \
-workDir "/data"


docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-java-1 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-java-1:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 48a3097aff050c8dc400a28f9c052dd8d66ac36688aeaa723407e3902ea81d4f   \
-name "slave-java-1" \
-workDir "/data"

docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-java-2 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-java-2:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 3b4c0a764db0410b9c1ce0939645512553929ac1c0914169f4c7229cda00dc24   \
-name "slave-java-2" \
-workDir "/data"


docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-java-3 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-java-3:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 8467863de63633a74668cd3502182021b9b96c152674c95c9739a0aaa97c5a23   \
-name "slave-java-3" \
-workDir "/data"


docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-java-4 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-java-4:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-java:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret edd760da33febd63686228f0bfe171080145fab2e8ef7898bfde2224e6ddb60e  \
-name "slave-java-4" \
-workDir "/data"
```