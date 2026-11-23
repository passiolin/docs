#!/bin/sh
set -u
umask 077

cd /opt/lark2didi || exit 1
mkdir -p logs

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> logs/cron.log
.venv/bin/python -m lark2didi.apply_plan --apply >> logs/cron.log 2>&1
.venv/bin/python -m lark2didi.sync_departments --apply --move-members >> logs/cron.log 2>&1
.venv/bin/python -m lark2didi.sync_departures --apply >> logs/cron.log 2>&1
