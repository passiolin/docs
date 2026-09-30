FROM azul/zulu-openjdk:21-latest

ENV SERVICE_NAME=zookeeper
ENV ZOOKEEPER_VERSION=3.8.5
ENV ZOOKEEPER_HOME=/opt/zookeeper

RUN useradd -m ${SERVICE_NAME} \
    && mkdir -p ${ZOOKEEPER_HOME}

WORKDIR ${ZOOKEEPER_HOME}
RUN wget https://dlcdn.apache.org/zookeeper/stable/apache-zookeeper-${ZOOKEEPER_VERSION}-bin.tar.gz \
	&& tar -xvf apache-zookeeper-${ZOOKEEPER_VERSION}-bin.tar.gz \
	&& mv apache-zookeeper-${ZOOKEEPER_VERSION}-bin/* ${ZOOKEEPER_HOME}/ \
	&& rm -rf apache-zookeeper-${ZOOKEEPER_VERSION}* \
	&& chmod +x -R ${ZOOKEEPER_HOME}/bin \
    && chown -R 1000:1000 ${ZOOKEEPER_HOME}

ENV PATH=$PATH:${ZOOKEEPER_HOME}/bin

USER ${SERVICE_NAME}

CMD ["bash","zkServer.sh","start-foreground"]