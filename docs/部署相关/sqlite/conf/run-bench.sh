#!/bin/bash
set -e
export LC_ALL=C
cd /root/sqlite-bench
NPROC=$(nproc)
echo "### HOST $(hostname) nproc=$NPROC memtotal_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo) kernel=$(uname -r) sqlite=$(sqlite3 --version | cut -d' ' -f1) date=$(date -u +%FT%TZ)"
for m in 1 3 5; do
  DB=./bench-m$m.db
  echo "### section metrics=$m expected_rows=$((1000*365*m))"
  for r in 1 2 3; do
    rm -f "$DB" "$DB-journal" "$DB-wal" "$DB-shm"
    sync
    echo 3 > /proc/sys/vm/drop_caches
    echo "### insert m=$m rep=$r"
    ./sqlite_bench insert "$DB" 0 0 5000 "$m"
    if [ "$r" -lt 3 ]; then rm -f "$DB" "$DB-journal" "$DB-wal" "$DB-shm"; fi
  done
  t0=$(date +%s%N); sqlite3 "$DB" 'ANALYZE;'; t1=$(date +%s%N)
  echo "RESULT mode=analyze metrics=$m wall_ms=$(( (t1-t0)/1000000 ))"
  ./sqlite_bench verify "$DB"
  for r in 1 2 3; do echo "### point m=$m rep=$r"; ./sqlite_bench point "$DB" 100000 0 0 "$m"; done
  for r in 1 2 3; do echo "### range m=$m rep=$r"; ./sqlite_bench range "$DB" 2000 0 0; done
  for r in 1 2 3; do echo "### agg m=$m rep=$r"; ./sqlite_bench agg "$DB" 100 0 0; done
  echo "### conc1 m=$m"; ./sqlite_bench conc "$DB" 1 20 95 "$m"
  if [ "$NPROC" -gt 1 ]; then echo "### concN m=$m"; ./sqlite_bench conc "$DB" "$NPROC" 20 95 "$m"; fi
  echo "### idle-default m=$m"; ./sqlite_bench idle "$DB" 0 10 0
done
echo "### idle-cache64 m=5"; ./sqlite_bench idle ./bench-m5.db 65536 10 0
echo "### phase done $(date -u +%FT%TZ)"
