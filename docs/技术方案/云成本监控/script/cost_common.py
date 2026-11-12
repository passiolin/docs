"""Shared helpers for cost-job 云账单适配器.

适配器契约: 每个 ``*_cost.py`` 实现 ``collect(month) -> dict``, 字段:
  cloud    适配器名(aws/aliyun/tencent/guance)
  rows     [(服务名, 金额)] 按金额降序
  currency 账单币种
  total    当月合计; None 时由 emit_report 按 rows 求和
  note     报表尾注(计费口径说明)
collect 只负责取数与解析; 打印、CSV、飞书推送由 emit_report 统一承担。
每个适配器取数成功后先 dump_raw 落原始响应, 字段解析对不上时人工回读校正。
"""

import csv
import datetime
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

LOGGER = logging.getLogger(__name__)
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cloud_dir(cloud):
    """<repo>/<cloud>, 其下为 script/ conf/ data/ 三个子目录。"""
    return os.path.join(REPO_DIR, cloud)


def load_env(cloud):
    """加载 <cloud>/conf/.env (KEY=VALUE, '#' 注释), 不覆盖已有环境变量。"""
    conf = os.path.join(cloud_dir(cloud), "conf", ".env")
    if not os.path.isfile(conf):
        return conf
    with open(conf, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value
    return conf


class CostApiError(Exception):
    """云 API 调用失败, 带状态码与响应体片段。"""

    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRYABLE_CODES = {"ServiceUnavailable", "Throttling", "Throttling.User", "InternalError"}


def _retryable(error):
    status = str(error.status)
    return status in {str(code) for code in RETRYABLE_STATUS} or status in RETRYABLE_CODES


def call_with_retry(func, attempts=None, base_delay=None):
    """云 API 瞬时故障(5xx/限流/网络抖动)指数退避重试; 其它错误立即抛出。"""
    if attempts is None:
        attempts = int(os.getenv("COST_RETRY_ATTEMPTS", "3"))
    if base_delay is None:
        base_delay = float(os.getenv("COST_RETRY_DELAY", "3"))
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except CostApiError as error:
            if attempt >= attempts or not _retryable(error):
                raise
            LOGGER.warning("调用失败(%s), %.1fs 后重试 %d/%d: %s",
                           error.status, delay, attempt, attempts, str(error)[:200])
        except urllib.error.URLError as error:
            if attempt >= attempts:
                raise
            LOGGER.warning("网络异常(%s), %.1fs 后重试 %d/%d",
                           error.reason, delay, attempt, attempts)
        time.sleep(delay)
        delay *= 2


def configure_logging():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def ssl_context():
    # 部署环境存在 TLS 拦截时, .env 里 COST_INSECURE_TLS=1 放开校验
    if os.getenv("COST_INSECURE_TLS", "").strip().lower() in ("1", "true", "yes"):
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def http_json(url, method="GET", headers=None, body=None, timeout=60):
    """发 HTTP 请求并解析 JSON 响应; 非 2xx 抛 CostApiError。"""
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        if isinstance(body, bytes):
            data = body
        elif isinstance(body, str):
            data = body.encode()
        else:
            data = json.dumps(body).encode()
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as resp:
            payload = resp.read()
            return json.loads(payload.decode()) if payload else {}
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode(errors="replace")[:500]
        except Exception:
            pass
        raise CostApiError(error.code, detail) from error


def month_range(month=None, today=None):
    """'2026-11' -> (month, 月首 date, 下月首 date)。

    End 为排他语义(适配 AWS TimePeriod), 当月未结束即给到月末, 取到月至今数据。
    """
    if month is None:
        if today is None:
            today = datetime.date.today()
        month = f"{today:%Y-%m}"
    year, mon = (int(part) for part in month.split("-"))
    first = datetime.date(year, mon, 1)
    end = datetime.date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return month, first, end


def require_env(*names):
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise SystemExit("缺少环境变量 %s(见 <云>/conf/env.example)" % ",".join(missing))


def pick_first(source, keys):
    """按优先级取第一个非空字段, 云端响应字段名不一致时容错用。"""
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def fmt_amount(value):
    return f"{value:,.2f}"


def render_table(headers, rows):
    widths = [len(header) for header in headers]
    grid = [[str(cell) for cell in row] for row in rows]
    for row in grid:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fmt_row(cells):
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)).rstrip()

    lines = [fmt_row(headers), "  ".join("-" * width for width in widths)]
    lines.extend(fmt_row(row) for row in grid)
    return "\n".join(lines)


def data_path(cloud, name):
    """落在 <cloud>/data/; 测试可用 COST_DATA_DIR 重定向。"""
    path_dir = os.getenv("COST_DATA_DIR") or os.path.join(cloud_dir(cloud), "data")
    os.makedirs(path_dir, exist_ok=True)
    return os.path.join(path_dir, name)


def dump_raw(cloud, month, payload):
    """响应原文存 <cloud>/data/<cloud>-<month>.raw.json, 供人工核对解析字段。"""
    path = data_path(cloud, f"{cloud}-{month}.raw.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def write_csv(cloud, name, headers, rows):
    path = data_path(cloud, name)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([headers, *rows])
    return path


def emit_report(cloud, month, report):
    """打印单云报表并落 CSV <cloud>/data/<cloud>-<month>.csv; 返回 CSV 路径。"""
    rows = [(str(name), float(amount)) for name, amount in report["rows"]]
    total = report.get("total")
    if total is None:
        total = sum(amount for _, amount in rows)
    currency = report.get("currency", "-")
    headers = ["服务", "金额", "币种"]
    table_rows = [(name, fmt_amount(amount), currency) for name, amount in rows]
    table_rows.append(("合计", fmt_amount(total), currency))
    print(f"== {cloud} {month} ==")
    print(render_table(headers, table_rows))
    note = report.get("note")
    if note:
        print(f"note: {note}")
    csv_rows = [(name, f"{amount:.2f}", currency) for name, amount in rows]
    csv_rows.append(("TOTAL", f"{total:.2f}", currency))
    return write_csv(cloud, f"{cloud}-{month}.csv", headers, csv_rows)


def redact_url(url):
    """去掉 webhook 后缀与 userinfo, 避免日志泄露凭据。"""
    parsed = urllib.parse.urlsplit(str(url))
    path = parsed.path
    if "/bot/v2/hook/" in path:
        path = path.split("/bot/v2/hook/")[0] + "/bot/v2/hook/<redacted>"
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, "", ""))


FEISHU_RATE_LIMIT_CODE = 11232
FEISHU_RETRY_DELAYS = (10, 30, 60)


def feishu_post(text, dry_run=False, card=None):
    """推送飞书消息; 传 card 时发交互卡片, 否则发纯文本。dry_run 只跳过发送。

    飞书限频(11232)按 10/30/60 秒退避重试; 其他错误码立即失败。
    """
    webhook = os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("FEISHU_WEBHOOK")
    if dry_run:
        LOGGER.info("dry-run: 跳过飞书推送")
        return False
    if not webhook:
        raise SystemExit("缺少环境变量 FEISHU_WEBHOOK_URL(见 aliyun/conf/env.example)")
    if card is not None:
        body = {"msg_type": "interactive", "card": card}
    else:
        body = {"msg_type": "text", "content": {"text": text}}
    LOGGER.info("推送飞书 %s", redact_url(webhook))
    payload = {}
    for delay in (0,) + FEISHU_RETRY_DELAYS:
        if delay:
            LOGGER.warning("飞书限频(%s), %s 秒后重试", FEISHU_RATE_LIMIT_CODE, delay)
            time.sleep(delay)
        payload = http_json(webhook, method="POST", body=body)
        code = payload.get("code", payload.get("StatusCode", 0))
        if code == 0:
            return True
        if code != FEISHU_RATE_LIMIT_CODE:
            break
    raise RuntimeError(
        f"飞书推送失败: code={payload.get('code', payload.get('StatusCode'))} "
        f"msg={payload.get('msg')}")
