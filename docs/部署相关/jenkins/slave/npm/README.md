

```shell
docker build -f Dockerfile -t mirrors.dl-aiot.com/dl/component/jenkins-slave-npm:1.0.0 .
```


```sh
docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-npm-0 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-npm-0:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-npm:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 7e42605982dc30bf5619cd94bd10ce7bacd7d1128fbc0eed163b157d5b7a5b89    \
-name "slave-npm-0" \
-workDir "/data"


docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-npm-1 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-npm-1:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-npm:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret d2b3505103660d486f24950b846fc72764af1c30bd86e911b3cadc156f0cf76c    \
-name "slave-npm-1" \
-workDir "/data"

docker run -d \
-uroot \
--privileged \
--net=host \
--name=jenkins-slave-npm-2 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/slave-npm-2:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
mirrors.dl-aiot.com/dl/component/jenkins-slave-npm:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://192.168.10.103:8080/ \
-secret 94799c86bfdae3b35e93caaf2adc51b90be326613e9c6497b5cdb0fcdfae0e02    \
-name "slave-npm-2" \
-workDir "/data"
```