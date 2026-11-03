#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 用法: cron-run.sh <cloud> <job>; 单参数形式兼容旧 crontab, 视为阿里云
if [[ $# -ge 2 ]]; then
    CLOUD="$1"
    JOB="$2"
else
    CLOUD="aliyun"
    JOB="${1:-}"
fi

case "${CLOUD}" in
    aliyun|aws) ;;
    *)
        printf 'Unknown cloud: %s\n' "${CLOUD}" >&2
        exit 2
        ;;
esac

case "${JOB}" in
    daily|monthly|spike) ;;
    *)
        printf 'Usage: %s <aliyun|aws> <daily|monthly|spike>\n' "$0" >&2
        exit 2
        ;;
esac

LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
mkdir -p "${LOG_DIR}"

PYTHON="${PYTHON:-$(command -v python3 || true)}"
if [[ -z "${PYTHON}" ]]; then
    printf 'python3 not found in PATH\n' >&2
    exit 1
fi

exec 9>"${LOG_DIR}/.${CLOUD}-${JOB}.lock"
if ! flock -n 9; then
    printf '%s-%s is already running\n' "${CLOUD}" "${JOB}"
    exit 0
fi

exec "${PYTHON}" "${SCRIPT_DIR}/${CLOUD}/script/${CLOUD}_report.py" "${JOB}" \
    >>"${LOG_DIR}/${CLOUD}-${JOB}.log" 2>&1
