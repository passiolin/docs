#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
DAILY_SCHEDULE="${DAILY_SCHEDULE:-30 9 * * *}"
MONTHLY_SCHEDULE="${MONTHLY_SCHEDULE:-0 10 1 * *}"
SPIKE_SCHEDULE="${SPIKE_SCHEDULE:-5 * * * *}"
# AWS 与阿里云错峰 2 分钟, 避免同一秒打同一个飞书机器人触发限频
# AWS 突增按天检查: CE 每次请求 $0.01, 不照搬阿里云的每小时
AWS_DAILY_SCHEDULE="${AWS_DAILY_SCHEDULE:-32 9 * * *}"
AWS_MONTHLY_SCHEDULE="${AWS_MONTHLY_SCHEDULE:-2 10 1 * *}"
AWS_SPIKE_SCHEDULE="${AWS_SPIKE_SCHEDULE:-10 10 * * *}"
MARK_START="# BEGIN cost-job"
MARK_END="# END cost-job"

if ! command -v crontab >/dev/null 2>&1; then
    printf 'crontab command not found\n' >&2
    exit 1
fi

block() {
    cat <<BLOCK_EOF
${MARK_START}
${DAILY_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aliyun daily >> ${LOG_DIR}/aliyun-daily.log 2>&1
${MONTHLY_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aliyun monthly >> ${LOG_DIR}/aliyun-monthly.log 2>&1
${SPIKE_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aliyun spike >> ${LOG_DIR}/aliyun-spike.log 2>&1
${AWS_DAILY_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aws daily >> ${LOG_DIR}/aws-daily.log 2>&1
${AWS_MONTHLY_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aws monthly >> ${LOG_DIR}/aws-monthly.log 2>&1
${AWS_SPIKE_SCHEDULE} ${SCRIPT_DIR}/cron-run.sh aws spike >> ${LOG_DIR}/aws-spike.log 2>&1
${MARK_END}
BLOCK_EOF
}

mkdir -p "${LOG_DIR}"
existing="$(crontab -l 2>/dev/null || true)"
backup="${LOG_DIR}/crontab.backup.$(date +%Y%m%d%H%M%S)"
printf '%s\n' "${existing}" >"${backup}"

filtered="$(printf '%s\n' "${existing}" | sed "/^${MARK_START}$/,/^${MARK_END}$/d")"
{
    printf '%s\n' "${filtered}"
    block
} | sed '/^$/N;/^\n$/D' | crontab -

printf 'Installed crontab entries for cost-job:\n'
block
printf 'Previous crontab backed up to: %s\n' "${backup}"
