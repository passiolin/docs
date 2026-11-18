#!/bin/bash
# 10 Hz sampler of /proc/meminfo plus sqlite_bench RSS sum
OUT=$1
echo "MEMSAMPLER start ts=$(date +%s.%N)" > "$OUT"
while [ ! -f /root/sqlite-bench/stop-sampler ]; do
  ts=$(date +%s.%N)
  vals=$(awk '/^(MemTotal|MemFree|MemAvailable|Buffers|Cached|Shmem|AnonPages|Mapped|Dirty|Writeback|Slab|SReclaimable|SUnreclaim|PageTables|KernelStack|Committed_AS|SwapTotal|SwapFree):/{printf "%s=%s ", $1, $2}' /proc/meminfo)
  n=0; rss=0
  for p in $(pgrep -x sqlite_bench 2>/dev/null); do
    r=$(awk '/^VmRSS:/{print $2}' "/proc/$p/status" 2>/dev/null)
    [ -n "$r" ] && rss=$((rss+r))
    n=$((n+1))
  done
  echo "MEM ts=$ts $vals bench_procs=$n bench_rss_kb=$rss" >> "$OUT"
  sleep 0.1
done
echo "MEMSAMPLER stop ts=$(date +%s.%N)" >> "$OUT"
