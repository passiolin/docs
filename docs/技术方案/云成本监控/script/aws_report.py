"""AWS 成本报表: 日报 / 月报 / 时报(突增), 以飞书卡片推送。

数据源 Cost Explorer, 口径 UnblendedCost, 金额单位美元(不做汇率折算)。
CE 数据滞后约一天, 日报与时报默认评估 T-REPORT_DELAY_DAYS(默认 2) 的账单。

突增判定(conf 可调, 单位美元):
  基线 B = 同一服务、同星期几、前 4 周金额的中位数
  当日金额 A, 超出 Δ = A - B
  判定: Δ >= SPIKE_MIN_AMOUNT(默认 20 美元) 且 Δ >= SPIKE_RATIO(默认 2.0) x B

用法:
  python3 aws/script/aws_report.py daily   [--date yyyy-mm-dd] [--dry-run]
  python3 aws/script/aws_report.py monthly [--month yyyy-mm]   [--dry-run]
  python3 aws/script/aws_report.py spike   [--date yyyy-mm-dd] [--dry-run]
"""

import argparse
import datetime
import json
import os
import statistics
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "common"))
sys.path.insert(0, str(SCRIPT_DIR))

from aws_cost import request_ce  # noqa: E402
from cost_common import (  # noqa: E402
    cloud_dir,
    configure_logging,
    dump_raw,
    feishu_post,
    fmt_amount,
    load_env,
    month_range,
)

DATA_DIR = os.path.join(cloud_dir("aws"), "data")
DAILY_CACHE_DIR = os.path.join(DATA_DIR, "daily")
MONTHLY_CACHE_DIR = os.path.join(DATA_DIR, "monthly")
STATE_PATH = os.path.join(DATA_DIR, "spike_alerted.json")
CACHE_TTL_SECONDS = 6 * 3600
STABLE_CACHE_DAYS = 4


def money(value):
    return f"${fmt_amount(value)}"


def config_float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def config_int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def month_days(month):
    """'2026-10' -> [date, ...] 该月全部自然日。"""
    _, first, end = month_range(month)
    days = []
    day = first
    while day < end:
        days.append(day)
        day += datetime.timedelta(days=1)
    return days


def add_months(month, delta):
    year, mon = (int(part) for part in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def parse_day(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"日期格式应为 yyyy-mm-dd: {value}")


def cache_month_is_fresh(path, month, today):
    """稳定月份的 CE 结果按永久缓存; 近几天可能被重算, 6 小时内重拉。"""
    if not os.path.isfile(path):
        return False
    _, _, end = month_range(month)
    if end <= today - datetime.timedelta(days=STABLE_CACHE_DAYS):
        return True
    return time.time() - os.path.getmtime(path) < CACHE_TTL_SECONDS


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def parse_daily_payload(payload):
    """CE DAILY 响应 -> {date: {服务: 金额}}。"""
    series = {}
    for result in payload.get("ResultsByTime", []):
        start = (result.get("TimePeriod") or {}).get("Start")
        if not start:
            continue
        day = datetime.date.fromisoformat(start)
        products = {}
        for group in result.get("Groups", []):
            name = ",".join(group.get("Keys", [])) or "未分组"
            metric = (group.get("Metrics") or {}).get("UnblendedCost") or {}
            try:
                amount = float(metric.get("Amount", 0))
            except (TypeError, ValueError):
                continue
            products[name] = products.get(name, 0.0) + amount
        series[day] = products
    return series


def parse_monthly_payload(payload):
    """CE MONTHLY 响应 -> [{"product": 服务, "amount": 金额}, ...] 按金额降序。"""
    rows = []
    for result in payload.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            name = ",".join(group.get("Keys", [])) or "未分组"
            metric = (group.get("Metrics") or {}).get("UnblendedCost") or {}
            try:
                amount = float(metric.get("Amount", 0))
            except (TypeError, ValueError):
                continue
            rows.append({"product": name, "amount": amount})
    rows.sort(key=lambda item: item["amount"], reverse=True)
    return rows


def _ce_body(month, granularity, today):
    _, first, end = month_range(month)
    end = min(end, today + datetime.timedelta(days=1))
    if first >= end:
        return None
    return {
        "TimePeriod": {"Start": first.isoformat(), "End": end.isoformat()},
        "Granularity": granularity,
        "Metrics": ["UnblendedCost"],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }


def fetch_daily_series(month, today=None):
    """整月按天的服务明细, CE 一次请求拉全月并缓存为 daily/<month>.json。"""
    today = today or datetime.date.today()
    path = os.path.join(DAILY_CACHE_DIR, f"{month}.json")
    if cache_month_is_fresh(path, month, today):
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return {datetime.date.fromisoformat(key): value for key, value in raw.items()}
    body = _ce_body(month, "DAILY", today)
    if body is None:
        return {}
    payload = request_ce(body)
    dump_raw("aws", f"{month}-daily", payload)
    series = parse_daily_payload(payload)
    _write_json(path, {day.isoformat(): products for day, products in series.items()})
    return series


def monthly_items(month, today=None):
    """某月按服务汇总(金额), 稳定月走本地缓存。"""
    today = today or datetime.date.today()
    path = os.path.join(MONTHLY_CACHE_DIR, f"{month}.json")
    if cache_month_is_fresh(path, month, today):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    body = _ce_body(month, "MONTHLY", today)
    if body is None:
        return []
    payload = request_ce(body)
    dump_raw("aws", month, payload)
    rows = parse_monthly_payload(payload)
    _write_json(path, rows)
    return rows


def monthly_total(month, today=None):
    return sum(item["amount"] for item in monthly_items(month, today))


def daily_series(dates, today=None):
    """{date: {服务: 金额}}; 某日无账单则为空 dict。"""
    today = today or datetime.date.today()
    merged = {}
    for month in sorted({f"{day:%Y-%m}" for day in dates}):
        merged.update(fetch_daily_series(month, today))
    return {day: merged.get(day, {}) for day in dates}


def resolve_day(series, target):
    """取 <= target 的最近一个有账单数据的日期。"""
    candidates = [day for day, products in series.items() if day <= target and products]
    if not candidates:
        raise SystemExit(f"没有可评估的账单数据(<= {target.isoformat()})")
    return max(candidates)


def mtd_total(series, day):
    total = 0.0
    for current, products in series.items():
        if current.year == day.year and current.month == day.month and current <= day:
            total += sum(products.values())
    return total


def baselines_for(series, day, weeks=None):
    """同服务同星期几前 N 周金额的中位数; 缺失日期按 0 计。"""
    weeks = weeks or config_int("SPIKE_BASELINE_WEEKS", 4)
    samples = [day - datetime.timedelta(days=7 * offset) for offset in range(1, weeks + 1)]
    baselines = {}
    for product in series.get(day, {}):
        values = [series[sample].get(product, 0.0) for sample in samples if sample in series]
        if values:
            baselines[product] = statistics.median(values)
    return baselines


def detect_spikes(series, day, min_amount=None, ratio=None):
    """按 基线 + 双闸门 找突增; 返回按超出金额降序的明细。"""
    min_amount = config_float("SPIKE_MIN_AMOUNT", 20.0) if min_amount is None else min_amount
    ratio = config_float("SPIKE_RATIO", 2.0) if ratio is None else ratio
    baselines = baselines_for(series, day)
    spikes = []
    for product, amount in series.get(day, {}).items():
        baseline = baselines.get(product)
        if baseline is None:
            continue
        delta = amount - baseline
        if delta < min_amount:
            continue
        if delta < ratio * baseline:
            continue
        spikes.append({"product": product, "amount": amount,
                       "baseline": baseline, "delta": delta})
    spikes.sort(key=lambda item: item["delta"], reverse=True)
    return spikes


def _ratio_label(amount, baseline):
    if baseline <= 0:
        return "∞"
    return f"{amount / baseline:.1f}×"


def load_state():
    if not os.path.isfile(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def mark_alerted(day, spikes):
    state = load_state()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for spike in spikes:
        state[f"{day.isoformat()}|{spike['product']}"] = {
            "delta": round(spike["delta"], 2), "sent_at": now}
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    state = {key: value for key, value in state.items() if key.split("|", 1)[0] >= cutoff}
    _write_json(STATE_PATH, state)


def month_spike_events(month, today=None):
    """扫描一个月内所有可评估日期的突增(按天数据一次拉整月)。"""
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=config_int("REPORT_DELAY_DAYS", 2))
    days = [day for day in month_days(month) if day <= cutoff]
    if not days:
        return []
    dates = month_days(add_months(month, -1)) + days
    series = daily_series(dates, today)
    events = []
    for day in days:
        for spike in detect_spikes(series, day):
            events.append({"day": day, **spike})
    events.sort(key=lambda item: item["delta"], reverse=True)
    return events


def _load_series_for_day(target, today):
    month = target.strftime("%Y-%m")
    dates = month_days(add_months(month, -1)) + [day for day in month_days(month) if day <= target]
    return daily_series(dates, today)


# ---------- 飞书卡片基础 ----------


def _card(title, template, elements):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": elements,
    }


def _fields(*pairs):
    return {"tag": "div", "fields": [
        {"is_short": True, "text": {"tag": "lark_md", "content": f"**{name}**\n{value}"}}
        for name, value in pairs]}


def _div(markdown):
    return {"tag": "div", "text": {"tag": "lark_md", "content": markdown}}


def _hr():
    return {"tag": "hr"}


def _note(text):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def _spike_markdown(spikes, label="突增"):
    if not spikes:
        return f"**{label}**\n无"
    lines = [f"**{label}（{len(spikes)} 项，合计超出 {money(sum(s['delta'] for s in spikes))}）**"]
    for spike in spikes[:5]:
        lines.append(
            f"<font color='red'>⚠ {spike['product']} 超出 {money(spike['delta'])}</font>"
            f"（当日 {money(spike['amount'])} / 基线 {money(spike['baseline'])}）")
    if len(spikes) > 5:
        lines.append(f"其余 {len(spikes) - 5} 项未展开")
    return "\n".join(lines)


# ---------- 日报 ----------


def build_daily_report(series, day):
    products = series.get(day, {})
    total = sum(products.values())
    prev_day = day - datetime.timedelta(days=1)
    prev_total = sum(series.get(prev_day, {}).values())
    month = day.strftime("%Y-%m")
    avg_months = [add_months(month, -offset) for offset in range(6, 0, -1)]
    top = [
        (name, amount, amount / total * 100 if total else 0.0)
        for name, amount in sorted(products.items(), key=lambda pair: pair[1], reverse=True)[:5]
    ]
    return {
        "day": day,
        "total": total,
        "prev_day": prev_day,
        "prev_total": prev_total,
        "mtd": mtd_total(series, day),
        "top": top,
        "average": statistics.mean(monthly_total(item) for item in avg_months),
        "avg_range": f"{avg_months[0]} ~ {avg_months[-1]}",
        "spikes": detect_spikes(series, day),
    }


def _daily_change_text(model, with_date):
    if model["prev_total"] > 0:
        change = (model["total"] - model["prev_total"]) / model["prev_total"] * 100
        date_part = f"（{model['prev_day']:%m-%d} {money(model['prev_total'])}）"
        return f"{change:+.1f}%" + (date_part if with_date else "")
    return f"—（{model['prev_day']:%m-%d} 无数据）"


def render_daily_text(model):
    day = model["day"]
    lines = [
        f"【AWS 成本日报】账期日 {day.isoformat()}",
        f"当日合计：{money(model['total'])}",
        f"环比昨日：{_daily_change_text(model, with_date=True)}",
        f"当月累计：{money(model['mtd'])}（截至 {day:%m-%d}）",
    ]
    if model["top"]:
        lines.append("服务 Top5：")
        for name, amount, share in model["top"]:
            lines.append(f"• {name}：{money(amount)}（{share:.1f}%）")
    lines.append(f"近6月平均月成本：{money(model['average'])}（{model['avg_range']}）")
    if model["spikes"]:
        lines.append(f"突增 {len(model['spikes'])} 项，合计超出 "
                     f"{money(sum(s['delta'] for s in model['spikes']))}：")
        for spike in model["spikes"][:5]:
            lines.append(
                f"⚠ {spike['product']} 超出 {money(spike['delta'])}"
                f"（当日 {money(spike['amount'])} / 基线 {money(spike['baseline'])}）")
    else:
        lines.append("突增：无")
    return "\n".join(lines)


def daily_card(model):
    day = model["day"]
    elements = [
        _fields(("当日合计", money(model["total"])),
                ("环比昨日", _daily_change_text(model, with_date=True))),
        _fields(("当月累计", f"{money(model['mtd'])}（截至 {day:%m-%d}）"),
                ("近6月平均", f"{money(model['average'])}（{model['avg_range']}）")),
    ]
    if model["top"]:
        elements.append(_hr())
        lines = ["**服务 Top5**"]
        for name, amount, share in model["top"]:
            lines.append(f"• {name}：{money(amount)}（{share:.1f}%）")
        elements.append(_div("\n".join(lines)))
    elements.append(_hr())
    elements.append(_div(_spike_markdown(model["spikes"])))
    elements.append(_note("口径：UnblendedCost（美元）｜按 T-2 账期统计，CE 数据可能随后更新"))
    return _card(f"AWS 成本日报 · {day.isoformat()}", "blue", elements)


# ---------- 月报 ----------


def build_monthly_report(month, today=None):
    today = today or datetime.date.today()
    total = monthly_total(month, today)
    prev_month = add_months(month, -1)
    prev_total = monthly_total(prev_month, today)
    avg_months = [add_months(month, -offset) for offset in range(5, -1, -1)]
    return {
        "month": month,
        "total": total,
        "prev_month": prev_month,
        "prev_total": prev_total,
        "average": statistics.mean(monthly_total(item, today) for item in avg_months),
        "avg_range": f"{avg_months[0]} ~ {avg_months[-1]}",
        "items": sorted(monthly_items(month, today), key=lambda item: item["amount"], reverse=True),
        "events": month_spike_events(month, today),
    }


def _monthly_change_text(model):
    if model["prev_total"] > 0:
        change = (model["total"] - model["prev_total"]) / model["prev_total"] * 100
        return f"{change:+.1f}%（{model['prev_month']} {money(model['prev_total'])}）"
    return f"—（{model['prev_month']} 无数据）"


def render_monthly_text(model):
    lines = [
        f"【AWS 成本月报】{model['month']}",
        f"本月合计：{money(model['total'])}",
        f"环比上月：{_monthly_change_text(model)}",
        f"近6月平均：{money(model['average'])}（{model['avg_range']}）",
    ]
    if model["items"]:
        lines.append("服务明细：")
        for item in model["items"][:10]:
            share = item["amount"] / model["total"] * 100 if model["total"] else 0.0
            lines.append(f"• {item['product']}：{money(item['amount'])}（{share:.1f}%）")
    if model["events"]:
        lines.append(f"本月突增：{len(model['events'])} 次，合计超出 "
                     f"{money(sum(e['delta'] for e in model['events']))}")
        for event in model["events"][:5]:
            lines.append(
                f"• {event['day']:%m-%d} {event['product']} 超出 {money(event['delta'])}"
                f"（当日 {money(event['amount'])} / 基线 {money(event['baseline'])}）")
        if len(model["events"]) > 5:
            lines.append(f"• 其余 {len(model['events']) - 5} 次未展开")
    else:
        lines.append("本月突增：无")
    return "\n".join(lines)


def monthly_card(model):
    elements = [
        _fields(("本月合计", money(model["total"])),
                ("环比上月", _monthly_change_text(model))),
        _fields(("近6月平均", f"{money(model['average'])}（{model['avg_range']}）"),
                ("突增次数", f"{len(model['events'])} 次" if model["events"] else "无")),
    ]
    if model["items"]:
        elements.append(_hr())
        lines = ["**服务明细**"]
        for item in model["items"][:10]:
            share = item["amount"] / model["total"] * 100 if model["total"] else 0.0
            lines.append(f"• {item['product']}：{money(item['amount'])}（{share:.1f}%）")
        elements.append(_div("\n".join(lines)))
    elements.append(_hr())
    elements.append(_div(_spike_markdown(model["events"], "本月突增")))
    elements.append(_note("口径：UnblendedCost（美元）｜近6月含报告月｜突增按日账单评估"))
    return _card(f"AWS 成本月报 · {model['month']}", "purple", elements)


# ---------- 时报(突增) ----------


def build_spike_alert(series, day):
    """有未推送过的突增时返回告警模型, 否则 None。"""
    spikes = detect_spikes(series, day)
    state = load_state()
    new_spikes = [spike for spike in spikes
                  if f"{day.isoformat()}|{spike['product']}" not in state]
    if not new_spikes:
        return None
    return {
        "day": day,
        "spikes": new_spikes,
        "total_delta": sum(spike["delta"] for spike in new_spikes),
        "mtd": mtd_total(series, day),
    }


def render_spike_text(model):
    day = model["day"]
    lines = [f"【AWS 成本突增告警】账期日 {day.isoformat()}"]
    for spike in model["spikes"]:
        lines.append(
            f"⚠ {spike['product']}：当日 {money(spike['amount'])}，"
            f"基线 {money(spike['baseline'])}，超出 {money(spike['delta'])}"
            f"（{_ratio_label(spike['amount'], spike['baseline'])} 基线）")
    lines.append(f"合计超出：{money(model['total_delta'])}")
    lines.append(f"当月累计：{money(model['mtd'])}（截至 {day:%m-%d}）")
    return "\n".join(lines)


def spike_card(model):
    day = model["day"]
    elements = []
    for index, spike in enumerate(model["spikes"]):
        if index:
            elements.append(_hr())
        elements.append(_div(
            f"**{spike['product']}**\n"
            f"当日 {money(spike['amount'])} ｜ 基线 {money(spike['baseline'])}\n"
            f"<font color='red'>超出 {money(spike['delta'])}"
            f"（{_ratio_label(spike['amount'], spike['baseline'])} 基线）</font>"))
    elements.append(_hr())
    elements.append(_fields(("合计超出", money(model["total_delta"])),
                            ("当月累计", f"{money(model['mtd'])}（截至 {day:%m-%d}）")))
    elements.append(_note("按 T-2 账期评估；同一突增只推送一次。"))
    return _card(f"AWS 成本突增告警 · {day.isoformat()}", "red", elements)


# ---------- 入口 ----------


def main(argv=None):
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name in ("daily", "monthly", "spike"):
        child = subparsers.add_parser(name)
        child.add_argument("--dry-run", action="store_true", help="只打印, 不发送飞书")
        if name == "monthly":
            child.add_argument("--month", help="yyyy-mm, 默认上月")
        else:
            child.add_argument("--date", help="账期日 yyyy-mm-dd, 默认 T-REPORT_DELAY_DAYS")
    args = parser.parse_args(argv)
    load_env("aws")
    today = datetime.date.today()

    if args.mode == "monthly":
        month = args.month or add_months(f"{today:%Y-%m}", -1)
        model = build_monthly_report(month, today)
        text = render_monthly_text(model)
        card = monthly_card(model)
        print(text)
        if not args.dry_run:
            feishu_post(text, card=card)
        return 0

    delay = config_int("REPORT_DELAY_DAYS", 2)
    target = parse_day(args.date) if args.date else today - datetime.timedelta(days=delay)
    series = _load_series_for_day(target, today)
    day = resolve_day(series, target)

    if args.mode == "daily":
        model = build_daily_report(series, day)
        text = render_daily_text(model)
        card = daily_card(model)
        print(text)
        if not args.dry_run:
            feishu_post(text, card=card)
        return 0

    model = build_spike_alert(series, day)
    if model is None:
        print(f"无新增突增（账期日 {day.isoformat()}）")
        return 0
    text = render_spike_text(model)
    card = spike_card(model)
    print(text)
    if not args.dry_run:
        feishu_post(text, card=card)
        mark_alerted(day, model["spikes"])
    return 0


if __name__ == "__main__":
    main()
