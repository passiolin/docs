FROM ubuntu:20.04

# 设置非交互式安装环境变量，防止安装过程中出现提示
ENV DEBIAN_FRONTEND=noninteractive

# 修改一下ubuntu镜像默认的源，不然会很慢
RUN sed -i s@/archive.ubuntu.com/@/mirrors.tuna.tsinghua.edu.cn/@g /etc/apt/sources.list \
    && sed -i s@/security.ubuntu.com/@/mirrors.tuna.tsinghua.edu.cn/@g /etc/apt/sources.list
RUN apt clean
RUN apt update -y

RUN apt install -y wget git openjdk-11-jdk curl

# 类似，如果有用到pip3的话，通过apt安装，然后修改一下默认的源，不然会很慢
RUN apt install python3-pip -y

# RUN pip3 install pip -U
RUN pip3 config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple

RUN pip3 install pyyaml paramiko requests

RUN wget http://192.168.10.103:8080/jnlpJars/agent.jar