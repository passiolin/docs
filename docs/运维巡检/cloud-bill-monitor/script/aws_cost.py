"""AWS Cost Explorer 适配器, SigV4 手工签名, 不依赖 boto3。

wire 格式源自 botocore ce/2017-10-25/service-2.json: protocol json 1.1,
POST / 需要 X-Amz-Target = AWSInsightsIndexService.GetCostAndUsage。
计费口径 UnblendedCost; CE 数据滞后约一天, 当月数字代表到查询时点的月至今。
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))

from cost_common import (  # noqa: E402  sys.path 引导后才能导入公共层
    call_with_retry,
    configure_logging,
    dump_raw,
    emit_report,
    load_env,
    http_json,
    month_range,
    require_env,
)

TARGET = "AWSInsightsIndexService.GetCostAndUsage"
SERVICE = "ce"
JSON_CT = "application/x-amz-json-1.1"


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hmac_bytes(key, message):
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sigv4_headers(method, host, path, query, body, amz_date, region, service,
                  access_key, secret_key, session_token=None):
    """计算 SigV4 并返回随请求发送的全部头(含 Authorization)。

    canonical 里必须含 host 虽然 urllib 会隐式带上; 有 session token 时
    x-amz-security-token 也要进 canonical。
    """
    payload_hash = _sha256_hex(body.encode())
    headers = {
        "Content-Type": JSON_CT,
        "X-Amz-Date": amz_date,
        "X-Amz-Target": TARGET,
    }
    if session_token:
        headers["X-Amz-Security-Token"] = session_token
    signed_map = {"content-type": headers["Content-Type"], "host": host,
                  "x-amz-date": amz_date, "x-amz-target": TARGET}
    if session_token:
        signed_map["x-amz-security-token"] = session_token
    names = sorted(signed_map)
    canonical_headers = "".join(f"{name}:{signed_map[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical = "\n".join([method, path, query, canonical_headers, signed_headers, payload_hash])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    k_date = _hmac_bytes(("AWS4" + secret_key).encode(), amz_date[:8])
    k_region = _hmac_bytes(k_date, region)
    k_service = _hmac_bytes(k_region, service)
    k_signing = _hmac_bytes(k_service, "aws4_request")
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{_sha256_hex(canonical.encode())}"
    )
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _parse_report(month_str, payload):
    results = payload.get("ResultsByTime", [])
    if not results:
        return {"cloud": "aws", "rows": [], "currency": "USD",
                "total": None, "note": "无账单数据(查过于新的月份或账号未启用 CE)"}
    result = results[0]
    groups = result.get("Groups", [])
    currency = "USD"
    rows = []
    for group in groups:
        metric = group.get("Metrics", {}).get("UnblendedCost", {})
        name = ",".join(group.get("Keys", [])) or "unknown"
        rows.append((name, float(metric.get("Amount", 0))))
        currency = metric.get("Unit") or currency
    rows.sort(key=lambda row: row[1], reverse=True)
    total_metric = (result.get("Total") or {}).get("UnblendedCost") or {}
    total = float(total_metric["Amount"]) if total_metric.get("Amount") is not None else None
    return {"cloud": "aws", "rows": rows, "currency": currency, "total": total,
            "note": "口径 UnblendedCost; CE 账单滞后约一天, 当月为月至今"}


def credentials():
    load_env("aws")
    require_env("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    access_key = os.environ["AWS_ACCESS_KEY_ID"]
    secret_key = os.environ["AWS_SECRET_ACCESS_KEY"]
    session_token = os.getenv("AWS_SESSION_TOKEN") or None
    region = os.getenv("AWS_REGION", "us-east-1")
    host = os.getenv("AWS_CE_HOST") or f"ce.{region}.amazonaws.com"
    return access_key, secret_key, session_token, region, host


def request_ce(body):
    """POST CE GetCostAndUsage 并返回解析后的 JSON。

    瞬时 5xx/限流由 call_with_retry 退避重试, 每次尝试重新签名(amz_date 过期会让签名失效)。
    """
    access_key, secret_key, session_token, region, host = credentials()
    payload_body = json.dumps(body)

    def call():
        amz_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = sigv4_headers("POST", host, "/", "", payload_body, amz_date, region,
                                SERVICE, access_key, secret_key, session_token)
        return http_json(f"https://{host}/", method="POST", headers=headers,
                         body=payload_body)

    return call_with_retry(call)


def collect(month=None):
    month_str, first, end = month_range(month)
    # CE 的 End 是排他日期; 当月只查到今天, 传月末未来日期会被拒
    today_utc = datetime.datetime.now(datetime.timezone.utc).date()
    end = min(end, today_utc + datetime.timedelta(days=1))
    body = {
        "TimePeriod": {"Start": first.isoformat(), "End": end.isoformat()},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }
    payload = request_ce(body)
    dump_raw("aws", month_str, payload)
    return _parse_report(month_str, payload)


def main(argv=None):
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="yyyy-mm, 不填默认当月")
    args = parser.parse_args(argv)
    emit_report("aws", month_range(args.month)[0], collect(args.month))


if __name__ == "__main__":
    main()
