FROM ubuntu:26.04

# 设置非交互式安装环境变量，防止安装过程中出现提示
ENV DEBIAN_FRONTEND=noninteractive

# 修改一下ubuntu镜像默认的源，不然会很慢
RUN sed -i s@/archive.ubuntu.com/@/mirrors.tuna.tsinghua.edu.cn/@g /etc/apt/sources.list.d/ubuntu.sources \
    && sed -i s@/security.ubuntu.com/@/mirrors.tuna.tsinghua.edu.cn/@g /etc/apt/sources.list.d/ubuntu.sources

RUN apt clean

RUN apt update -y

RUN apt install git wget curl -y \
    && apt install python3-pip -y

RUN pip3 config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/simple

RUN wget https://d10.injdk.cn/openjdk/zulu/21/zulu21.50.19-ca-jdk21.0.11-linux_x64.tar.gz \
    && mkdir /opt/zulujdk/ \
    && mv zulu21.50.19-ca-jdk21.0.11-linux_x64.tar.gz /opt/zulujdk/ \
    && cd /opt/zulujdk/ \
    && tar -xf zulu21.50.19-ca-jdk21.0.11-linux_x64.tar.gz \
    && mv zulu21.50.19-ca-jdk21.0.11-linux_x64/* ./ \
    && rm -rf zulu21.50.19-ca-jdk21.0.11-linux_x64  zulu21.50.19-ca-jdk21.0.11-linux_x64.tar.gz

ENV JAVA_HOME=/opt/zulujdk
ENV PATH=${PATH}:/opt/zulujdk/bin

RUN apt install -y maven

# maven的配置文件
COPY settings.xml /root/.m2/settings.xml
COPY entrypoint.sh /entrypoint.sh

CMD ["exec"]
ENTRYPOINT ["bash","/entrypoint.sh"]