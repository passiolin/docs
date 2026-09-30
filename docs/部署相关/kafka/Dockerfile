FROM openjdk:11

ENV SERVICE_NAME=kafka
ENV KAFKA_HOME=/opt/kafka
ENV KAFKA_VERSION=2.8.2

RUN useradd -m ${SERVICE_NAME} \
    && mkdir -p ${KAFKA_HOME}

WORKDIR ${KAFKA_HOME}

COPY kafka_2.12-${KAFKA_VERSION}.tgz kafka_2.12-${KAFKA_VERSION}.tgz

RUN tar -xvf kafka_2.12-${KAFKA_VERSION}.tgz \
    && mkdir -p ${KAFKA_HOME} \
	&& mv kafka_2.12-${KAFKA_VERSION}/* ${KAFKA_HOME}/ \
	&& rm -rf kafka_2.12-${KAFKA_VERSION}* \
    && chmod +x -R ${KAFKA_HOME}/bin \
    && chown -R 1000:1000 ${KAFKA_HOME}

ENV PATH=$PATH:${KAFKA_HOME}/bin

USER ${SERVICE_NAME}

CMD ["bash","/opt/kafka/bin/kafka-server-start.sh","/opt/kafka/config/server.properties"]