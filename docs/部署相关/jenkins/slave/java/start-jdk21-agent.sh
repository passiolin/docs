#!/bin/bash

set -ex

pkill -9 -f backend-jdk-21
nohup java -jar agent.jar -url http://192.168.0.150:8080/ -secret 0d943cd33e71b8f066d1b78e5b6e901db4a020025987bfbb55399605f03e975a -name "backend-jdk-21" -webSocket -workDir "/data/jenkins-jdk21-agent" &

tail -f nohup.out
