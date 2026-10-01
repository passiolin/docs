#!/usr/bin/env bash
# ============================================================
# gen-node-yml.sh —— 生成 ES 节点最终 elasticsearch.yml
# 组成 = conf/elasticsearch.yml(共享基线)+ node.name 注入
# 用法: ./gen-node-yml.sh <节点名如 es-1> <输出目录>
# 实测:ES 8.19.14,es-1/2/3(10.10.12.134/135/136)
# ============================================================
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
NAME=$1; OUT=$3

mkdir -p "$OUT"
{
  cat "$DIR/elasticsearch.yml"
  cat <<EOF

# ---------- 节点差异(脚本生成于 $(date +%F),勿手改) ----------
node.name: $NAME
EOF
} > "$OUT/elasticsearch.yml"
chmod 644 "$OUT/elasticsearch.yml"
echo "generated: $OUT/elasticsearch.yml (node.name=$NAME)"
