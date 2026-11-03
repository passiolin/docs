"""阿里云 BSS OpenAPI 适配器, RPC 签名, 零依赖。

API: QueryAccountBill(bss_open_api 2017-12-14), 按产品返回当月金额。
国内站与国际站签名流程一致, 站点差异只体现在 ALIYUN_BSS_HOST。
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import os
import urllib.parse
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))

from cost_common import (  # noqa: E402  sys.path 引导后才能导入公共层
    CostApiError,
    call_with_retry,
    configure_logging,
    dump_raw,
    emit_report,
    load_env,
    http_json,
    month_range,
    pick_first,
    require_env,
)

HOST = "business.aliyuncs.com"
VERSION = "2017-12-14"
ACTION = "QueryAccountBill"


def pop_encode(value):
    """阿里云 pop 编码: RFC3986 保留集, ~ 不转义, * 转成 %2A。"""
    return urllib.parse.quote(str(value), safe="-_.~")


def build_query(access_key, secret_key, params, nonce=None, now=None):
    """生成已签名的查询串(Signature 已附加在末尾)。"""
    common = {
        "Format": "JSON",
        "Version": VERSION,
        "AccessKeyId": access_key,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": nonce or uuid.uuid4().hex,
        "Timestamp": (now or datetime.datetime.now(datetime.timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    merged = {**common, **params}
    canonical = "&".join(
        f"{pop_encode(key)}={pop_encode(value)}" for key, value in sorted(merged.items())
    )
    # StringToSign = GET&/& + 整段 canonical 再编码一次(双重编码是 pop 规范要求)
    string_to_sign = "GET&%2F&" + pop_encode(canonical)
    digest = hmac.new((secret_key + "&").encode(), string_to_sign.encode(), hashlib.sha1)
    signature = base64.b64encode(digest.digest()).decode()
    return canonical + "&Signature=" + urllib.parse.quote(signature, safe="")


def fetch_bill_items(month, granularity="MONTHLY", billing_date=None, page_size=300):
    """拉取某月账单明细, 分页取全; DAILY 粒度必须传 billing_date(yyyy-mm-dd)。"""
    load_env("aliyun")
    require_env("ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET")
    host = os.getenv("ALIYUN_BSS_HOST", HOST)
    access_key = os.environ["ALIYUN_ACCESS_KEY_ID"]
    secret_key = os.environ["ALIYUN_ACCESS_KEY_SECRET"]
    items = []
    first_payload = None
    page_num = 1
    while True:
        params = {
            "Action": ACTION,
            "BillingCycle": month,
            "Granularity": granularity,
            # 账户级账单默认不分产品, 打开后才按产品出明细
            "IsGroupByProduct": "true",
            "PageNum": page_num,
            "PageSize": page_size,
        }
        if billing_date:
            params["BillingDate"] = billing_date
        query = build_query(access_key, secret_key, params)

        def fetch_page():
            payload = http_json(f"https://{host}/?{query}")
            # 阿里云业务错误可能以 HTTP 200 + Code 非 Success 返回
            if payload.get("Code") and payload["Code"] != "Success":
                raise CostApiError(payload["Code"], payload.get("Message", ""))
            return payload

        payload = call_with_retry(fetch_page)
        if first_payload is None:
            first_payload = payload
        data = payload.get("Data") or {}
        page_items = ((data.get("Items") or {}).get("Item")) or []
        items.extend(page_items)
        total_count = data.get("TotalCount")
        if not page_items or (total_count is not None and page_num * page_size >= total_count):
            break
        page_num += 1
    tag = month if granularity == "MONTHLY" else f"{month}-daily-{billing_date}"
    if isinstance(first_payload, dict):
        dump_raw("aliyun", tag, first_payload)
    return items


def collect(month=None):
    month_str, _, _ = month_range(month)
    rows = []
    currency = "CNY"
    for item in fetch_bill_items(month_str, "MONTHLY"):
        name = pick_first(item, ("ProductName", "ProductCode"))
        amount = pick_first(item, ("PretaxAmount", "PretaxGrossAmount"))
        if name is None or amount is None:
            continue
        try:
            value = float(amount)
        except (TypeError, ValueError):
            continue
        rows.append((str(name), value))
        currency = item.get("Currency") or currency
    rows.sort(key=lambda row: row[1], reverse=True)
    return {"cloud": "aliyun", "rows": rows, "currency": currency, "total": None,
            "note": "口径 PretaxAmount(应付, 已扣优惠); 月内金额会随出账调整"}


def main(argv=None):
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="yyyy-mm, 不填默认当月")
    args = parser.parse_args(argv)
    emit_report("aliyun", month_range(args.month)[0], collect(args.month))


if __name__ == "__main__":
    main()
