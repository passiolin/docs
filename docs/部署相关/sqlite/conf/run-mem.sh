#!/bin/bash
set -e
export LC_ALL=C
cd /root/sqlite-bench
NPROC=$(nproc)
echo "### HOST $(hostname) nproc=$NPROC memtotal_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo) swaptotal_kb=$(awk '/SwapTotal/{print $2}' /proc/meminfo) date=$(date -u +%FT%TZ)"
for m in 1 3 5; do
  DB=./bench-m$m.db
  rm -f "$DB" "$DB-journal" "$DB-wal" "$DB-shm"
  sync
  echo 3 > /proc/sys/vm/drop_caches
  LOG=./mem-m$m.log
  rm -f ./stop-sampler
  timeout 600 ./memlog.sh "$LOG" &
  SPID=$!
  sleep 0.5
  mark() { echo "MARK ts=$(date +%s.%N) phase=$1" >> "$LOG"; }
  echo "### mem-run metrics=$m"
  mark insert
  ./sqlite_bench insert "$DB" 0 0 5000 "$m" > ./res-insert-m$m.txt
  mark analyze
  sqlite3 "$DB" 'ANALYZE;'
  mark point
  ./sqlite_bench point "$DB" 100000 0 0 "$m" > ./res-point-m$m.txt
  mark range
  ./sqlite_bench range "$DB" 2000 0 0 > ./res-range-m$m.txt
  mark agg
  ./sqlite_bench agg "$DB" 100 0 0 > ./res-agg-m$m.txt
  mark conc4
  ./sqlite_bench conc "$DB" 4 10 95 "$m" > ./res-conc-m$m.txt
  mark fullscan
  sqlite3 "$DB" 'SELECT count(*),sum(value) FROM metrics;' >/dev/null
  mark idle
  sleep 10
  mark end
  touch ./stop-sampler
  wait $SPID 2>/dev/null || true
  rm -f ./stop-sampler
  echo "### mem m=$m done $(free -m | sed -n 2p)"
done
echo "### phase done $(date -u +%FT%TZ)"
