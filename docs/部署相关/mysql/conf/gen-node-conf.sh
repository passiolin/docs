#!/usr/bin/env bash
# ============================================================
# gen-node-conf.sh —— 生成 MGR 节点的完整 my.cnf
# 组成 = common.cnf + role-mgr.cnf + 节点差异参数(3 个)
# 用法: ./gen-node-conf.sh <本机IP> <server_id> <输出目录>
# 实测:MySQL 8.4.11,节点 115/116/117(server_id 取 IP 末段)
# ============================================================
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
IP=$1; SID=$2; OUT=$3

mkdir -p "$OUT"
{
  # common 里已有 server_id=1(单机占位),这里删掉避免重复,以节点值为准
  sed '/^server_id/d' "$DIR/common.cnf"
  cat "$DIR/role-mgr.cnf"
  cat <<EOF

# ---------- 节点差异(脚本生成于 $(date +%F),勿手改) ----------
# MGR 组内标识与选主展示用宿主机 IP
report_host = $IP
server_id = $SID
# 组通信监听地址(xcom 层,不是 3306 业务口)
loose-group_replication_local_address = $IP:33061
EOF
} > "$OUT/my.cnf"
chmod 644 "$OUT/my.cnf"
echo "generated: $OUT/my.cnf  (IP=$IP server_id=$SID)"
