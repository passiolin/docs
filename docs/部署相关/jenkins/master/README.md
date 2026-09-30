
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