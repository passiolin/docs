# Jenkins(jenkins)

> - **形态**:master 用官方镜像 `jenkins/jenkins:2.567-jdk21` 跑 docker,构建代理按语言分三套镜像自建。k8s 上的部署另有 [k3s](../k3s/README.md) 与 [kubernetes](../kubernetes/README.md) 篇,这里只放单机 docker 的配置。
> - **目录性质**:配置与镜像定义,不是系列文章。没有编号文章,因此无「文章索引」表。

## 目录内容

| 路径 | 内容 |
| --- | --- |
| [master/](master/README.md) | master 启动命令与数据卷挂载 |
| [slave/java/](slave/java/README.md) | JDK 21 构建代理(Dockerfile、entrypoint、Maven settings) |
| [slave/npm/](slave/npm/README.md) | Node 构建代理 |
| [slave/python/](slave/python/README.md) | Python 构建代理 |
| [Jenkinsfile](Jenkinsfile) | 流水线定义:checkout、构建、镜像推送、飞书通知 |
| [feishu.sh](feishu.sh) | 构建结果推送飞书群机器人 |

## 使用顺序

先在 [master/](master/README.md) 起主控,再按构建语言选一套 [slave/](slave/) 镜像起代理,最后把 [Jenkinsfile](Jenkinsfile) 里的 `agent { label }` 改成对应代理标签。

## 注意

`Jenkinsfile` 与 `feishu.sh` 里有内网地址与飞书机器人 webhook 的完整 URL,提交前按实际环境替换,不要直接复用。
