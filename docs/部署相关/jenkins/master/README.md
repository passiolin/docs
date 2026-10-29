# Jenkins master

主控容器:官方 `jenkins/jenkins:2.567-jdk21`,host 网络,`/data/jenkins/master` 挂成 `JENKINS_HOME`。

```
docker run -d \
--net=host \
--user=root \
--name=jenkins-master \
--restart=always \
-v /data/jenkins/master:/var/jenkins_home \
-v /etc/localtime:/etc/localtime \
jenkins/jenkins:2.567-jdk21
```
