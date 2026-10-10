#!/bin/bash

set -ex

wget https://jenkins.ipuff.online/jnlpJars/agent.jar

exec java -jar agent.jar -url https://jenkins.ipuff.online/ -secret 3e960e487d720cb7acfbe7a5e6caa51f83b30ef9f17ec3f011110319b8a6d5c6 -name "sz-zulujdk21-0" -webSocket -workDir "/data"