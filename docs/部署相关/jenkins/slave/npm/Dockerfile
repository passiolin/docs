FROM ubuntu:20.04

# 设置非交互式安装环境变量，防止安装过程中出现提示
ENV DEBIAN_FRONTEND=noninteractive

# 修改一下ubuntu镜像默认的源，不然会很慢
RUN sed -i s@/archive.ubuntu.com/@/mirrors.cloud.tencent.com/@g /etc/apt/sources.list \
    && sed -i s@/security.ubuntu.com/@/mirrors.cloud.tencent.com/@g /etc/apt/sources.list
RUN apt clean
RUN apt update -y

RUN apt install -y wget git openjdk-11-jdk curl python3 python3-pip
RUN pip3 config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple
RUN pip3 install pyyaml paramiko requests

RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash -

RUN apt update -y
RUN apt install -y nodejs

RUN npm install -g pnpm

RUN wget https://jenkins.dl-aiot.com/jnlpJars/agent.jar