    FROM azul/zulu-openjdk:21-latest

    ENV SERVICE_NAME=elasticsearch
    ENV ES_VERSION=8.19.14
    ENV ES_HOME=/opt/${SERVICE_NAME}

    RUN useradd -m ${SERVICE_NAME} \
        && mkdir -p ${ES_HOME} \
        && apt update -y \
        && apt install -y wget

    WORKDIR ${ES_HOME}

    # https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.19.14-linux-x86_64.tar.gz
    RUN wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-${ES_VERSION}-linux-x86_64.tar.gz \
        && tar -xvf elasticsearch-${ES_VERSION}-linux-x86_64.tar.gz \
        && mv elasticsearch-${ES_VERSION}/* ${ES_HOME}/ \
        && rm -rf elasticsearch-${ES_VERSION}* \
        && chmod +x -R ${ES_HOME}/bin \
        && chown -R 1000:1000 ${ES_HOME}

    ENV PATH=$PATH:${ES_HOME}/bin

    USER ${SERVICE_NAME}

    CMD ["bash","elasticsearch"]

    # docker build -f Dockerfile -t dev-mirrors.ipuff.online/component/elasticsearch:8.19.14 .