

```shell
docker build -f Dockerfile -t overseas-mirrors.dl-aiot.com/dl/component/jenkins-slave-python:1.0.0 .
```


```sh
docker run -d \
-uroot \
--privileged \
--net=host \
--name=us-jenkins-ai-python-0 \
--restart=always \
-v /etc/localtime:/etc/localtime \
-v /data/jenkins/slave/us-jenkins-ai-python-0:/data \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /usr/bin/docker:/usr/bin/docker  \
overseas-mirrors.dl-aiot.com/dl/component/jenkins-slave-python:1.0.0 \
java -jar -Dfile.encoding=UTF-8 /agent.jar -url http://129.226.121.44:8080/ \
-secret ab9fbbf5bf6619c123baa54a46e0fc13fa808d770f9f70e73ab6392dc4e010fb \
-name "us-ai-python-0" \
-workDir "/data"

```