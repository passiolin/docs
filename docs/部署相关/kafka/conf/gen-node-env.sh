#!/usr/bin/env bash
# ============================================================
# gen-node-env.sh —— 生成 Kafka 节点差异 env 文件
# 用法: ./gen-node-env.sh <本机IP> <node_id 1..N> <输出目录>
# 产出: kafka-node.env(与 kafka-common.env 配合 --env-file 使用)
# 实测:Kafka 4.3.1 KRaft,kafka-1/2/3(10.10.12.128/129/130)
# ============================================================
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
IP=$1; NID=$2; OUT=$3

mkdir -p "$OUT"
cat > "$OUT/kafka-node.env" <<EOF
# ---------- 节点差异(脚本生成于 $(date +%F),勿手改) ----------
KAFKA_NODE_ID=$NID
KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://$IP:9092
EOF
chmod 644 "$OUT/kafka-node.env"
echo "generated: $OUT/kafka-node.env (node.id=$NID advertise=$IP:9092)"
