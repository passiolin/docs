#!/usr/bin/env bash
# ============================================================
# gen-node-conf.sh —— 生成 Redis 节点配置
# 用法:
#   ./gen-node-conf.sh <本机IP> standalone <输出目录>
#   ./gen-node-conf.sh <本机IP> replica    <输出目录> <主库IP>
#   ./gen-node-conf.sh <本机IP> sentinel   <输出目录> <主库IP>
#   ./gen-node-conf.sh <本机IP> cluster    <输出目录> <实例端口>   # 每实例一次
# 实测:Redis 8.0.x,redis-1/2/3(10.10.12.118/119/127)
# ============================================================
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
IP=$1; ROLE=$2; OUT=$3

mkdir -p "$OUT"
case "$ROLE" in
  standalone)
    { cat "$DIR/common.conf"; cat "$DIR/role-standalone.conf"; } > "$OUT/redis.conf"
    echo "generated: $OUT/redis.conf (standalone, IP=$IP)"
    ;;
  replica)
    MASTER=$4
    { cat "$DIR/common.conf"; cat "$DIR/role-replica.conf"
      echo "# ---------- 节点差异(脚本生成于 $(date +%F),勿手改) ----------"
      echo "replicaof $MASTER 6379"
    } > "$OUT/redis.conf"
    echo "generated: $OUT/redis.conf (replica of $MASTER)"
    ;;
  sentinel)
    MASTER=$4
    sed -e "s|<主库IP>|$MASTER|g" \
        -e "s|sentinel announce-port 26379|sentinel announce-port 26379\nsentinel announce-ip $IP|" \
        "$DIR/role-sentinel.conf" > "$OUT/sentinel.conf"
    echo "generated: $OUT/sentinel.conf (monitor $MASTER, announce $IP)"
    ;;
  cluster)
    PORT=$4
    { cat "$DIR/common.conf"; cat "$DIR/role-cluster.conf"
      echo
      echo "# ---------- 节点差异(脚本生成于 $(date +%F),勿手改) ----------"
      echo "port $PORT"
      echo "cluster-announce-ip $IP"
      echo "cluster-announce-port $PORT"
      echo "cluster-announce-bus-port $((PORT+10000))"
    } > "$OUT/redis-$PORT.conf"
    echo "generated: $OUT/redis-$PORT.conf (cluster, $IP:$PORT bus $((PORT+10000)))"
    ;;
  *) echo "unknown role: $ROLE" >&2; exit 1 ;;
esac
chmod 644 "$OUT"/*.conf
