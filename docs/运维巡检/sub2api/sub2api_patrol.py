#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sub2api 网关巡检 · 飞书卡片

三块巡检内容，全部走 admin API：
  1. 账号管理   GET  /api/v1/admin/accounts                          活跃账号（status=active 且 schedulable）
                POST /api/v1/admin/accounts/opencode-go-usage/refresh  opencode 账号的 5h/周/月 额度窗口
                GET  /api/v1/admin/cn-providers/accounts/:id/quota     CommandCode 账号的 5h/周/月 窗口与余额
                GET  /api/v1/admin/cn-providers/accounts/:id/balance   其它 payg 账号的余额
  2. 用户管理   GET  /api/v1/admin/users                              balance 低于阈值的用户
  3. 运维监控   GET  /api/v1/admin/ops/request-errors                  最近窗口内的报错，按状态码+消息聚合
  4. 通知       POST $FEISHU_WEBHOOK                                  交互式卡片，机器人开了签名校验就带 sign

额度按「剩余百分比」展示：上游返回的是已用百分比，卡片里换算成剩余（100 - used）。
账号按平台分组，组内按「月剩余」升序，最紧张的排在前面。

配置全部走环境变量，代码里不含地址、Key 和 webhook：
  SUB2API_BASE_URL        网关地址，例如 https://sub2api.example.com
  SUB2API_ADMIN_API_KEY   管理 API Key（后台 → 设置 → 管理员 API Key）
  FEISHU_WEBHOOK          群机器人 webhook
  FEISHU_SECRET           可选，机器人开启「签名校验」时填
  LOW_BALANCE_THRESHOLD   可选，用户余额阈值，默认 1000
  ERROR_WINDOW_MINUTES    可选，报错回看窗口，默认 60

用法：
  ./sub2api_patrol.py --dry-run     # 采集并打印，不发飞书
  ./sub2api_patrol.py               # 采集并推送
  ./sub2api_patrol.py --json        # stdout 只输出卡片 JSON
  ./sub2api_patrol.py --window-minutes 180 --threshold 500

退出码：0 正常（发现异常也算正常，异常体现在卡片里）；2 配置缺失；3 采集失败；4 推送失败。
只用 Python 3 标准库，目标机不需要装依赖。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("PATROL_TZ", "Asia/Shanghai"))
DEFAULT_THRESHOLD = 1000.0
DEFAULT_WINDOW_MINUTES = 60
API_TIMEOUT = 30
PAGE_SIZE = 200
QUOTA_WORKERS = 6
CARD_ROWS_PER_GROUP = 12
CARD_USER_ROWS = 15
CARD_ERROR_LINES = 10
CARD_ERROR_GROUPS = 5
QUOTA_WARN_USED = 90.0
SUSPEND_AT_DEFAULT = 99.0
RESUME_AT_DEFAULT = 90.0
STATE_FILE_DEFAULT = "/var/lib/sub2api-patrol/auto_suspend.json"
# 有额度窗口的平台参与自动启停；有余额接口的平台只在卡片上显示余额
SUSPEND_PLATFORMS = ("opencode_go", "command_code")
QUOTA_PLATFORMS = ("opencode_go", "command_code", "deepseek", "kimi", "zhipu", "minimax")

PLATFORM_ORDER = ["opencode_go", "command_code"]
PLATFORM_LABEL = {
    "opencode_go": "opencode",
    "command_code": "CommandCode",
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "grok": "Grok",
}


class ConfigError(Exception):
    pass


class ApiError(Exception):
    pass


@dataclass
class Quota:
    """已用额度百分比；None 表示该窗口没有数据。上游返回的就是已用比例，这里不再换算。"""

    five_hour: float | None = None
    seven_day: float | None = None
    month: float | None = None
    balance: float | None = None
    currency: str = ""
    resets: dict[str, str] = field(default_factory=dict)
    snapshot_age_minutes: float | None = None


@dataclass
class AccountRow:
    id: int
    name: str
    platform: str
    status: str
    schedulable: bool
    error_message: str
    cost: float = 0.0
    quota: Quota | None = None
    quota_error: str = ""


@dataclass
class UserRow:
    id: int
    email: str
    role: str
    status: str
    balance: float


@dataclass
class ErrorGroup:
    status_code: int
    message: str
    count: int = 0


@dataclass
class SuspendAction:
    """一次自动启停动作。kind: suspend 停用 / resume 恢复 / adopt 接管。"""

    account_id: int
    name: str
    platform: str
    kind: str
    reason: str
    applied: bool = False


@dataclass
class Report:
    generated_at: datetime
    window_minutes: int
    threshold: float
    suspend_at: float = SUSPEND_AT_DEFAULT
    resume_at: float = RESUME_AT_DEFAULT
    suspended: list[SuspendAction] = field(default_factory=list)
    resumed: list[SuspendAction] = field(default_factory=list)
    adopted: list[SuspendAction] = field(default_factory=list)
    actions_applied: bool = True
    accounts_total: int = 0
    accounts_active: list[AccountRow] = field(default_factory=list)
    accounts_unschedulable: int = 0
    accounts_error: int = 0
    users_low: list[UserRow] = field(default_factory=list)
    users_total: int = 0
    errors_total: int = 0
    error_groups: list[ErrorGroup] = field(default_factory=list)
    error_samples: list[dict] = field(default_factory=list)
    collect_failures: list[str] = field(default_factory=list)


def env(name: str, required: bool = True, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if required and not value:
        raise ConfigError(f"缺少环境变量 {name}")
    return value


def api_request(base_url: str, api_key: str, method: str, path: str, body: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    data = None
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            payload = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ApiError(f"{method} {path} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"{method} {path} 连接失败: {exc.reason}") from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(f"{method} {path} 返回非 JSON: {payload[:200]}") from exc
    if parsed.get("code") not in (0, "0", None):
        raise ApiError(f"{method} {path} 业务错误: {parsed.get('message') or parsed.get('code')}")
    return parsed.get("data") or {}


def fetch_accounts(base_url: str, api_key: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        data = api_request(
            base_url, api_key, "GET", f"/api/v1/admin/accounts?page={page}&page_size={PAGE_SIZE}"
        )
        items = data.get("items") or []
        rows.extend(items)
        if len(items) < PAGE_SIZE:
            return rows
        page += 1


def fetch_today_cost(base_url: str, api_key: str, account_ids: list[int]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for start in range(0, len(account_ids), 100):
        chunk = account_ids[start : start + 100]
        data = api_request(
            base_url,
            api_key,
            "POST",
            "/api/v1/admin/accounts/today-stats/batch",
            {"account_ids": chunk},
        )
        stats.update(data.get("stats") or {})
    return stats


def fetch_users(base_url: str, api_key: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        data = api_request(
            base_url, api_key, "GET", f"/api/v1/admin/users?page={page}&page_size=100"
        )
        items = data.get("items") or []
        rows.extend(items)
        if len(items) < 100:
            return rows
        page += 1


def fetch_errors(base_url: str, api_key: str, start: datetime) -> list[dict]:
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S%z")
    start_str = start_str[:-2] + ":" + start_str[-2:] if len(start_str) >= 5 else start_str
    query = urllib.parse.urlencode({"page": 1, "page_size": PAGE_SIZE, "start_time": start_str})
    data = api_request(base_url, api_key, "GET", f"/api/v1/admin/ops/request-errors?{query}")
    return data.get("items") or []


def used_percent(value: float | None) -> float | None:
    """上游返回的已用百分比，夹到 0–100。"""
    if value is None:
        return None
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        frac = "".join(ch for ch in tail if ch.isdigit())[:6]
        rest = tail[len(frac):] if len(tail) > len(frac) else ""
        text = f"{head}.{frac}{rest}" if frac else head + rest
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def fetch_opencode_quota(base_url: str, api_key: str, account_id: int) -> tuple[Quota | None, str]:
    """先读缓存快照，超过 10 分钟才主动刷新；刷新失败沿用缓存。

    刷新接口有 30 秒一次的频率限制，巡检按小时跑不会撞上；两次手动验证挤在一起时会，
    这时回退到缓存而不是报错。
    """
    data = api_request(base_url, api_key, "GET", f"/api/v1/admin/accounts/{account_id}/opencode-go-usage")
    snapshot = data.get("snapshot") or {}
    fetched = parse_timestamp(snapshot.get("fetched_at"))
    age_minutes = None
    if fetched:
        age_minutes = (datetime.now(TZ) - fetched.astimezone(TZ)).total_seconds() / 60
    if snapshot.get("status") != "ok" or age_minutes is None or age_minutes > 10:
        try:
            data = api_request(
                base_url,
                api_key,
                "POST",
                f"/api/v1/admin/accounts/{account_id}/opencode-go-usage/refresh",
                {},
            )
            snapshot = data.get("snapshot") or snapshot
            fetched = parse_timestamp(snapshot.get("fetched_at"))
            age_minutes = (datetime.now(TZ) - fetched.astimezone(TZ)).total_seconds() / 60 if fetched else None
        except ApiError:
            pass
    windows = snapshot.get("data") or {}
    if not windows:
        return None, f"快照状态 {snapshot.get('status') or 'unknown'}"
    quota = Quota(
        five_hour=used_percent((windows.get("rolling") or {}).get("percent")),
        seven_day=used_percent((windows.get("weekly") or {}).get("percent")),
        month=used_percent((windows.get("monthly") or {}).get("percent")),
        snapshot_age_minutes=age_minutes,
    )
    for key, name in (("rolling", "5h"), ("weekly", "7d"), ("monthly", "月")):
        resets_at = (windows.get(key) or {}).get("resets_at")
        if resets_at:
            quota.resets[name] = resets_at
    return quota, ""


def pick_balance(data: dict) -> tuple[float | None, str]:
    """从余额响应里挑一个非零币种。

    上游可能同时返回多币种（如 CNY 2126 + USD 0），顶层 balance/currency 会跟着
    最近一次探测的币种漂移，直接取顶层偶尔会读到 USD 0。取 balances 里最大的非零项。
    """
    best_value = None
    best_currency = ""
    for item in data.get("balances") or []:
        try:
            value = float(item.get("balance") or 0)
        except (TypeError, ValueError):
            continue
        if value > 0 and (best_value is None or value > best_value):
            best_value = value
            best_currency = str(item.get("currency") or "")
    if best_value is None:
        try:
            best_value = float(data.get("balance") or 0)
        except (TypeError, ValueError):
            best_value = 0.0
        best_currency = str(data.get("currency") or "USD")
    return best_value, best_currency


def quota_for_account(base_url: str, api_key: str, account: dict) -> tuple[Quota | None, str]:
    """按平台取额度。返回 (额度, 错误说明)。"""
    platform = str(account.get("platform") or "")
    account_id = int(account["id"])
    try:
        if platform == "opencode_go":
            return fetch_opencode_quota(base_url, api_key, account_id)
        if platform == "command_code":
            data = api_request(
                base_url, api_key, "GET", f"/api/v1/admin/cn-providers/accounts/{account_id}/quota"
            )
            quota = Quota()
            for tier in data.get("tiers") or []:
                window = str(tier.get("window") or "")
                value = used_percent(tier.get("used_percent"))
                if window == "5h":
                    quota.five_hour = value
                elif window in ("weekly", "7d"):
                    quota.seven_day = value
                elif window in ("monthly", "month"):
                    quota.month = value
                if tier.get("reset_at"):
                    quota.resets[{"5h": "5h", "weekly": "7d", "monthly": "月"}.get(window, window)] = tier["reset_at"]
            balance = data.get("balance") or {}
            if balance.get("success"):
                value, currency = pick_balance(balance)
                quota.balance = value
                quota.currency = currency
            return quota, ""
        if platform in ("deepseek", "kimi", "zhipu", "minimax"):
            data = api_request(
                base_url, api_key, "GET", f"/api/v1/admin/cn-providers/accounts/{account_id}/balance"
            )
            if data.get("success"):
                value, currency = pick_balance(data)
                return Quota(balance=value, currency=currency), ""
            return None, "余额查询未返回数据"
        return None, "该平台无额度接口"
    except ApiError as exc:
        return None, str(exc)[:120]


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"accounts": {}}
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return {"accounts": {}}
    return data


def save_state(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def set_schedulable(base_url: str, api_key: str, account_id: int, value: bool) -> None:
    api_request(
        base_url,
        api_key,
        "POST",
        f"/api/v1/admin/accounts/{account_id}/schedulable",
        {"schedulable": value},
    )


def max_window(quota: Quota) -> tuple[str, float]:
    pairs = [("5h", quota.five_hour), ("7d", quota.seven_day), ("月", quota.month)]
    available = [(label, value) for label, value in pairs if value is not None]
    if not available:
        return "", 0.0
    return max(available, key=lambda item: item[1])


def window_reset_text(quota: Quota, label: str) -> str:
    raw = quota.resets.get(label)
    when = parse_timestamp(raw) if raw else None
    if not when:
        return ""
    return when.astimezone(TZ).strftime("%m-%d %H:%M")


def action_reason(quota: Quota, label: str, used: float) -> str:
    text = f"{label} {used:.0f}%"
    reset = window_reset_text(quota, label)
    if reset:
        text += f"，{reset} 重置"
    return text


def platform_label(platform: str) -> str:
    return PLATFORM_LABEL.get(platform, platform or "未知平台")


def group_accounts(rows: list[AccountRow]) -> list[tuple[str, list[AccountRow]]]:
    buckets: dict[str, list[AccountRow]] = {}
    for row in rows:
        buckets.setdefault(row.platform, []).append(row)
    order = [p for p in PLATFORM_ORDER if p in buckets]
    order += sorted(p for p in buckets if p not in PLATFORM_ORDER)
    result = []
    for platform in order:
        items = buckets[platform]
        items.sort(
            key=lambda r: (
                -(r.quota.month) if r.quota and r.quota.month is not None else 1e9,
                r.name,
            )
        )
        result.append((platform, items))
    return result


def collect(
    base_url: str,
    api_key: str,
    threshold: float,
    window_minutes: int,
    state_path: str = STATE_FILE_DEFAULT,
    suspend_at: float = SUSPEND_AT_DEFAULT,
    resume_at: float = RESUME_AT_DEFAULT,
    apply_actions: bool = True,
) -> Report:
    now = datetime.now(TZ)
    report = Report(
        generated_at=now,
        window_minutes=window_minutes,
        threshold=threshold,
        suspend_at=suspend_at,
        resume_at=resume_at,
        actions_applied=apply_actions,
    )

    try:
        accounts = fetch_accounts(base_url, api_key)
    except ApiError as exc:
        report.collect_failures.append(f"账号列表采集失败：{exc}")
        accounts = []
    report.accounts_total = len(accounts)

    active_raw = [a for a in accounts if a.get("status") == "active" and a.get("schedulable")]
    report.accounts_unschedulable = sum(
        1 for a in accounts if a.get("status") == "active" and not a.get("schedulable")
    )
    report.accounts_error = sum(1 for a in accounts if (a.get("error_message") or "").strip())

    costs: dict[str, dict] = {}
    if active_raw:
        try:
            costs = fetch_today_cost(base_url, api_key, [int(a["id"]) for a in active_raw])
        except ApiError as exc:
            report.collect_failures.append(f"今日成本采集失败：{exc}")

    # 额度采集覆盖「在调度的活跃账号」和「停用中的活跃账号」——后者用于判断是否已恢复
    quota_candidates = [
        a
        for a in accounts
        if a.get("status") == "active" and str(a.get("platform") or "") in QUOTA_PLATFORMS
    ]
    quota_map: dict[int, tuple[Quota | None, str]] = {}
    if quota_candidates:
        with ThreadPoolExecutor(max_workers=QUOTA_WORKERS) as pool:
            results = list(
                pool.map(lambda item: quota_for_account(base_url, api_key, item), quota_candidates)
            )
        quota_map = {
            int(item["id"]): result for item, result in zip(quota_candidates, results)
        }

    def build_row(item: dict) -> AccountRow:
        row = AccountRow(
            id=int(item["id"]),
            name=str(item.get("name") or item["id"]).strip(),
            platform=str(item.get("platform") or "-"),
            status=str(item.get("status") or "-"),
            schedulable=bool(item.get("schedulable")),
            error_message=str(item.get("error_message") or ""),
            cost=float((costs.get(str(item["id"])) or {}).get("cost") or 0),
        )
        row.quota, row.quota_error = quota_map.get(row.id, (None, ""))
        return row

    if active_raw:
        report.accounts_active = [build_row(item) for item in active_raw]

    # 自动启停：任一窗口已用 ≥ suspend_at 停用；本工具停用过的账号降到 resume_at 以下再恢复
    if quota_candidates:
        state = load_state(state_path)
        dirty = False
        for item in quota_candidates:
            account_id = int(item["id"])
            quota, _err = quota_map.get(account_id, (None, ""))
            if not quota:
                continue
            label, used = max_window(quota)
            if not label:
                continue
            name = str(item.get("name") or account_id).strip()
            platform = str(item.get("platform") or "-")
            schedulable = bool(item.get("schedulable"))
            key = str(account_id)
            managed = key in state["accounts"]
            reason = action_reason(quota, label, used)
            fresh = quota.snapshot_age_minutes is None or quota.snapshot_age_minutes <= 90

            if schedulable and platform in SUSPEND_PLATFORMS and used >= suspend_at and fresh:
                action = SuspendAction(account_id, name, platform, "suspend", reason)
                if apply_actions:
                    try:
                        set_schedulable(base_url, api_key, account_id, False)
                        action.applied = True
                        state["accounts"][key] = {
                            "name": name,
                            "platform": platform,
                            "disabled_at": now.isoformat(),
                            "reason": reason,
                            "window": label,
                            "used": used,
                        }
                        dirty = True
                    except ApiError as exc:
                        report.collect_failures.append(f"停用 {name} 失败：{exc}")
                report.suspended.append(action)
            elif not schedulable and managed and used < resume_at and fresh:
                action = SuspendAction(account_id, name, platform, "resume", reason)
                if apply_actions:
                    try:
                        set_schedulable(base_url, api_key, account_id, True)
                        action.applied = True
                        state["accounts"].pop(key, None)
                        dirty = True
                    except ApiError as exc:
                        report.collect_failures.append(f"恢复 {name} 失败：{exc}")
                report.resumed.append(action)
            elif (
                not schedulable
                and not managed
                and platform in SUSPEND_PLATFORMS
                and used >= resume_at
            ):
                # 停用中、用量仍在高位：视为配额耗尽导致的停用，纳入管理，等自然恢复
                action = SuspendAction(account_id, name, platform, "adopt", reason)
                if apply_actions:
                    state["accounts"][key] = {
                        "name": name,
                        "platform": platform,
                        "disabled_at": "",
                        "adopted_at": now.isoformat(),
                        "reason": reason,
                        "window": label,
                        "used": used,
                    }
                    dirty = True
                report.adopted.append(action)
        if dirty:
            try:
                save_state(state_path, state)
            except OSError as exc:
                report.collect_failures.append(f"状态文件写入失败：{exc}")

    try:
        users = fetch_users(base_url, api_key)
    except ApiError as exc:
        report.collect_failures.append(f"用户列表采集失败：{exc}")
        users = []
    report.users_total = len(users)
    for item in users:
        balance = float(item.get("balance") or 0)
        if balance < threshold:
            report.users_low.append(
                UserRow(
                    id=int(item["id"]),
                    email=str(item.get("email") or "-"),
                    role=str(item.get("role") or "-"),
                    status=str(item.get("status") or "-"),
                    balance=balance,
                )
            )
    report.users_low.sort(key=lambda u: u.balance)

    try:
        errors = fetch_errors(base_url, api_key, now - timedelta(minutes=window_minutes))
    except ApiError as exc:
        report.collect_failures.append(f"报错日志采集失败：{exc}")
        errors = []
    report.errors_total = len(errors)
    groups: dict[tuple[int, str], ErrorGroup] = {}
    for item in errors:
        code = int(item.get("status_code") or 0)
        message = str(item.get("message") or "").strip().replace("\n", " ")[:60]
        key = (code, message)
        if key not in groups:
            groups[key] = ErrorGroup(status_code=code, message=message)
        groups[key].count += 1
    report.error_groups = sorted(groups.values(), key=lambda g: -g.count)
    report.error_samples = errors
    return report


def any_quota_warning(report: Report) -> bool:
    for row in report.accounts_active:
        if not row.quota:
            continue
        for value in (row.quota.five_hour, row.quota.seven_day, row.quota.month):
            if value is not None and value >= QUOTA_WARN_USED:
                return True
    return False


def card_color(report: Report) -> str:
    has_5xx = any(g.status_code >= 500 for g in report.error_groups)
    if has_5xx or not report.accounts_active or report.collect_failures:
        return "red"
    if report.errors_total or report.users_low or any_quota_warning(report):
        return "orange"
    return "green"


def fmt_percent(value: float | None) -> str:
    if value is None:
        return "—"
    if value <= 0:
        return "0%"
    return f"{value:.0f}%"


def fmt_balance_text(balance: float | None, currency: str) -> str:
    if balance is None:
        return ""
    symbol = "$" if currency.upper() == "USD" or not currency else currency + " "
    return f"余额 {symbol}{balance:,.2f}"


def quota_line(row: AccountRow) -> str:
    if not row.quota:
        reason = row.quota_error or "无额度数据"
        return f"**{row.name}**｜{reason}"
    quota = row.quota
    windows = (quota.five_hour, quota.seven_day, quota.month)
    balance_text = ""
    if quota.balance is not None and row.platform != "command_code":
        balance_text = fmt_balance_text(quota.balance, quota.currency)
    if all(value is None for value in windows):
        if balance_text:
            return f"**{row.name}**｜{balance_text}"
        return f"**{row.name}**｜无窗口额度"
    parts = [
        f"5h {fmt_percent(quota.five_hour)}",
        f"7d {fmt_percent(quota.seven_day)}",
        f"月 {fmt_percent(quota.month)}",
    ]
    line = f"**{row.name}**｜" + "｜".join(parts)
    if balance_text:
        line += f"｜{balance_text}"
    highest = None
    for name, value in (("5h", quota.five_hour), ("7d", quota.seven_day), ("月", quota.month)):
        if value is not None and value >= QUOTA_WARN_USED and (highest is None or value > highest[1]):
            highest = (name, value)
    if highest and quota.resets.get(highest[0]):
        line += f"（{highest[0]} {quota.resets[highest[0]][:16].replace('T', ' ')} 重置）"
    if quota.snapshot_age_minutes is not None and quota.snapshot_age_minutes > 15:
        line += f"（快照 {int(quota.snapshot_age_minutes)} 分钟前）"
    return line


def build_card(report: Report) -> dict:
    total_cost = sum(r.cost for r in report.accounts_active)
    title = f"sub2api 巡检 · {report.generated_at.strftime('%m-%d %H:%M')}"
    elements: list[dict] = []

    overview = (
        f"**账号**：活跃 {len(report.accounts_active)} 个，停调度 {report.accounts_unschedulable} 个，"
        f"带错误信息 {report.accounts_error} 个 · 今日成本 {money(total_cost)}"
    )
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": overview}})

    action_lines: list[str] = []
    for action in report.suspended:
        action_lines.append(f"停用｜**{action.name}**｜{action.reason}")
    for action in report.resumed:
        action_lines.append(f"恢复｜**{action.name}**｜{action.reason}")
    for action in report.adopted:
        action_lines.append(
            f"接管｜**{action.name}**｜{action.reason}（低于 {report.resume_at:.0f}% 自动恢复）"
        )
    if action_lines:
        header = "**自动启停**" if report.actions_applied else "**自动启停（预览，未写入）**"
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": header + "\n" + "\n".join(action_lines)},
            }
        )

    groups = group_accounts(report.accounts_active)
    if not groups:
        elements.append({"tag": "hr"})
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "**没有可调度的活跃账号**"}}
        )
    for platform, rows in groups:
        elements.append({"tag": "hr"})
        lines = [f"**{platform_label(platform)}**（{len(rows)} 个活跃）"]
        for row in rows[:CARD_ROWS_PER_GROUP]:
            lines.append(quota_line(row))
        if len(rows) > CARD_ROWS_PER_GROUP:
            lines.append(f"其余 {len(rows) - CARD_ROWS_PER_GROUP} 个账号未列出")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    elements.append({"tag": "hr"})
    if report.users_low:
        lines = [f"**低额度用户（< {money(report.threshold)}）**：{len(report.users_low)} 人"]
        for user in report.users_low[:CARD_USER_ROWS]:
            lines.append(f"**{user.email}** · {money(user.balance)} · {user.role}/{user.status}")
        if len(report.users_low) > CARD_USER_ROWS:
            lines.append(f"其余 {len(report.users_low) - CARD_USER_ROWS} 人未列出")
        content = "\n".join(lines)
    else:
        content = f"**低额度用户（< {money(report.threshold)}）**：无"
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    elements.append({"tag": "hr"})
    if report.error_groups:
        lines = [f"**近 {report.window_minutes} 分钟报错**：{report.errors_total} 条"]
        shown = 0
        covered = 0
        for group in report.error_groups[:CARD_ERROR_GROUPS]:
            if shown >= CARD_ERROR_LINES:
                break
            lines.append(f"`{group.status_code}` {group.message} ×{group.count}")
            shown += 1
            covered += group.count
        for sample in report.error_samples:
            if shown >= CARD_ERROR_LINES:
                break
            when = str(sample.get("created_at") or "")[11:16]
            model = sample.get("requested_model") or sample.get("model") or "-"
            email = sample.get("user_email") or "-"
            message = str(sample.get("message") or "")[:50]
            lines.append(f"{when}｜{model}｜{email}｜{message}")
            shown += 1
        if covered < report.errors_total:
            lines.append(f"其余 {report.errors_total - covered} 条未列出")
        content = "\n".join(lines)
    else:
        content = f"**近 {report.window_minutes} 分钟报错**：无"
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    if report.collect_failures:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**采集异常**\n" + "\n".join(f"- {f}" for f in report.collect_failures),
                },
            }
        )

    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": (
                        f"生成于 {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')} · "
                        f"用户总数 {report.users_total} · 账号总数 {report.accounts_total}"
                    ),
                }
            ],
        }
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": card_color(report), "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def feishu_payload(card: dict, secret: str) -> dict:
    payload: dict = {"msg_type": "interactive", "card": card}
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    return payload


def send_feishu(webhook: str, card: dict, secret: str) -> None:
    body = json.dumps(feishu_payload(card, secret)).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise ApiError(f"飞书 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"飞书连接失败: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(f"飞书返回非 JSON: {raw[:200]}") from exc
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, "0"):
        raise ApiError(f"飞书返回 code={code} msg={result.get('msg') or result.get('StatusMessage')}")


def money(value: float) -> str:
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.3f}"


def print_text_summary(report: Report, stream=None) -> None:
    out = stream or sys.stdout
    print(f"生成时间 {report.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=out)
    print(f"账号：总数 {report.accounts_total}，活跃 {len(report.accounts_active)}，"
          f"停调度 {report.accounts_unschedulable}，带错误 {report.accounts_error}", file=out)
    for platform, rows in group_accounts(report.accounts_active):
        print(f"[{platform_label(platform)}] {len(rows)} 个活跃", file=out)
        for row in rows:
            print("  - " + quota_line(row).replace("**", ""), file=out)
    if report.suspended or report.resumed or report.adopted:
        print("自动启停：", file=out)
        for action in report.suspended:
            print(f"  - 停用 {action.name}（{action.reason}）", file=out)
        for action in report.resumed:
            print(f"  - 恢复 {action.name}（{action.reason}）", file=out)
        for action in report.adopted:
            print(f"  - 接管 {action.name}（{action.reason}）", file=out)
    print(f"低额度用户（< {report.threshold}）：{len(report.users_low)} / 总用户 {report.users_total}", file=out)
    for user in report.users_low:
        print(f"  - {user.email} 余额 {user.balance:.4f} {user.role}/{user.status}", file=out)
    print(f"近 {report.window_minutes} 分钟报错：{report.errors_total} 条", file=out)
    for group in report.error_groups:
        print(f"  - {group.status_code} {group.message} ×{group.count}", file=out)
    for failure in report.collect_failures:
        print(f"  ! {failure}", file=out)


def main() -> int:
    parser = argparse.ArgumentParser(description="sub2api 网关巡检并推送飞书卡片")
    parser.add_argument("--dry-run", action="store_true", help="只采集并打印，不推送飞书")
    parser.add_argument("--json", action="store_true", help="stdout 只输出卡片 JSON")
    parser.add_argument("--window-minutes", type=int, default=None, help="报错回看窗口，默认 60")
    parser.add_argument("--threshold", type=float, default=None, help="用户余额阈值，默认 1000")
    parser.add_argument("--no-suspend", action="store_true", help="只展示启停判断，不写入账号状态")
    args = parser.parse_args()

    try:
        base_url = env("SUB2API_BASE_URL")
        api_key = env("SUB2API_ADMIN_API_KEY")
        webhook = "" if args.dry_run else env("FEISHU_WEBHOOK")
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    threshold = args.threshold
    if threshold is None:
        threshold = float(os.environ.get("LOW_BALANCE_THRESHOLD") or DEFAULT_THRESHOLD)
    window = args.window_minutes
    if window is None:
        window = int(os.environ.get("ERROR_WINDOW_MINUTES") or DEFAULT_WINDOW_MINUTES)

    suspend_at = float(os.environ.get("AUTO_SUSPEND_THRESHOLD") or SUSPEND_AT_DEFAULT)
    resume_at = float(os.environ.get("AUTO_RESUME_THRESHOLD") or RESUME_AT_DEFAULT)
    state_path = os.environ.get("AUTO_SUSPEND_STATE") or STATE_FILE_DEFAULT
    auto_suspend = os.environ.get("AUTO_SUSPEND", "true").strip().lower() not in ("0", "false", "no", "off")
    apply_actions = auto_suspend and not args.dry_run and not args.no_suspend

    report = collect(
        base_url,
        api_key,
        threshold,
        window,
        state_path=state_path,
        suspend_at=suspend_at,
        resume_at=resume_at,
        apply_actions=apply_actions,
    )
    card = build_card(report)

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print_text_summary(report, stream=sys.stderr)
    else:
        print_text_summary(report)

    if report.collect_failures:
        print("采集存在失败项，见上方 ! 行", file=sys.stderr)

    if args.dry_run:
        print("dry-run：未推送飞书", file=sys.stderr if args.json else sys.stdout)
        return 0 if not report.collect_failures else 3

    try:
        send_feishu(webhook, card, os.environ.get("FEISHU_SECRET", "").strip())
    except ApiError as exc:
        print(f"飞书推送失败：{exc}", file=sys.stderr)
        return 4

    print("已推送飞书卡片")
    return 0 if not report.collect_failures else 3


if __name__ == "__main__":
    sys.exit(main())
