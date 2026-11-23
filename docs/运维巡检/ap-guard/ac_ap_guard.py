#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集客 AC 控制台 · 巡检与 AP 零终端 / 离线守护

流程（与网页端操作一一对应）：
  1. 登录          GET  /api/getrandom  ->  POST /api/sysauth   （password = md5(random + 明文)）
  2. 查看 AP 列表   GET  /api/ac/apsearch?pagenum=1&numperpage=500&groupid=all
  3. 重新下发配置   POST /api/ac/apcmd  {"cmd":"settemplate","maclist":"mac1,mac2","template":"default"}
                   （maclist 就是网页上"勾选 AP"后提交的设备清单）
  4. 终端数为 0 时发飞书卡片告警，默认每 300 秒复查一次，
     直到终端数 > 阈值（默认 3）才发恢复卡片并解除该 AP 的告警状态；
     AP 离线时单独告警，重新上线发恢复卡片，离线期间不下发配置。
  5. --inspect 模式跑一次 AC01–AC05 巡检（可用性、在线率、终端数、固件一致性、
     终端对账），退出码 0 健康 / 1 有异常 / 2 自身故障；--fix 对 AC03 命中项重推。

配置全部走命令行参数或环境变量，代码里不含地址和密码：
  AC_BASE_URL / AC_PASSWORD / FEISHU_WEBHOOK / FEISHU_SECRET

只用 Python 3 标准库，无第三方依赖。

注意：AC 只保留一个有效 token（后登录者生效，旧 token 返回 ret=-99 / LANG648）。
因此本脚本与控制台网页端不能长期同时在线：脚本被踢会自动重登，反过来网页端
再登录也会把脚本踢掉，脚本会在下一轮自动恢复。脚本不会主动定时换 token。

用法：
  ./ac_ap_guard.py --list                        # 只看 AP 列表（第 2 步）
  ./ac_ap_guard.py --report                      # 推一张 AP 状态卡片（在线/终端数）
  ./ac_ap_guard.py --inspect                     # 只读巡检 AC01–AC05，退出码 0/1/2
  ./ac_ap_guard.py --inspect --fix               # 巡检并对 AC03 命中的 AP 重推（须授权）
  ./ac_ap_guard.py --once                        # 守护巡检一轮就退出（配 cron / systemd timer）
  ./ac_ap_guard.py                               # 常驻，每 300 秒一轮
  ./ac_ap_guard.py --dry-run --once              # 只打印，不真下发、不发飞书
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

LOG = logging.getLogger("ac-ap-guard")

# ---------------------------------------------------------------- 默认参数

DEFAULT_BASE_URL = ""          # 必须用 --base-url 或 AC_BASE_URL 指定，如 http://<AC_IP>:8080
DEFAULT_PASSWORD = ""          # 必须用 --password 或 AC_PASSWORD 指定
DEFAULT_THRESHOLD = 3          # 终端数 > 3 视为恢复
DEFAULT_INTERVAL = 300         # 5 分钟一轮
DEFAULT_REISSUE_COOLDOWN = 1800  # 持续为 0 时，最快 30 分钟重复下发一次
DEFAULT_REMIND = 1800          # 持续为 0 时，最快 30 分钟重复告警一次（0 = 不重复）
HTTP_TIMEOUT = 10
HTTP_RETRIES = 3

AUTH_HINTS = ("LANG648", "登录已失效", "unauthorized", "登录已超时")


class ACError(RuntimeError):
    pass


class AuthError(ACError):
    pass


# ---------------------------------------------------------------- 工具函数


def now_ms() -> int:
    return int(time.time() * 1000)


def ts_str(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d %H:%M:%S")


def parse_mac(value: str) -> str:
    """网页端 qn()：去掉非 16 进制字符并转小写，长度必须 12。"""
    mac = "".join(c for c in str(value or "").lower() if c in "0123456789abcdef")
    return mac if len(mac) == 12 else ""


def http_json(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    """发一个 JSON 请求。401/403 抛 AuthError，网络错误重试后抛 ACError。"""
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    last_err: Exception | None = None

    for attempt in range(1, HTTP_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("sysauth", token)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError(f"HTTP {exc.code} {url}") from exc
            last_err = exc
            LOG.warning("HTTP %s %s（第 %d/%d 次）", exc.code, url, attempt, HTTP_RETRIES)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_err = exc
            LOG.warning("请求失败 %s：%s（第 %d/%d 次）", url, exc, attempt, HTTP_RETRIES)

        if attempt < HTTP_RETRIES:
            time.sleep(2 * attempt)

    raise ACError(f"请求失败 {url}：{last_err}")


def is_auth_msg(msg: object) -> bool:
    text = str(msg or "")
    return any(h.lower() in text.lower() for h in AUTH_HINTS)


# ---------------------------------------------------------------- AC 客户端


class ACClient:
    def __init__(self, base_url: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.token: str | None = None

    # --- 第 1 步：登录 ---------------------------------------------------
    def login(self) -> None:
        rnd = http_json("GET", f"{self.base_url}/api/getrandom?_t={now_ms()}")
        if rnd.get("ret") != 1 or not rnd.get("random"):
            raise AuthError(f"取随机数失败：{rnd.get('msg') or rnd}")

        # 网页端：md5(String(random) + 明文密码)
        digest = hashlib.md5(f"{rnd['random']}{self.password}".encode()).hexdigest()
        auth = http_json("POST", f"{self.base_url}/api/sysauth", body={"password": digest})
        if auth.get("ret") != 1 or not auth.get("token"):
            raise AuthError(f"登录失败：{auth.get('msg') or auth}")

        self.token = str(auth["token"])
        LOG.info("登录成功，token=%s…", self.token[:8])

    def _ensure_token(self) -> None:
        # AC 只保留一个有效 token：谁后登录谁生效，旧 token 调接口返回 ret=-99 / LANG648。
        # 所以不做定时主动换 token，只有被 AC 拒绝时才重新登录。
        if not self.token:
            self.login()

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        self._ensure_token()
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return http_json(method, url, body=body, token=self.token)

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        """调接口；token 失效（HTTP 401/403 或 ret!=1 且 msg 提示登录）时重登一次。"""
        try:
            res = self._request(method, path, params, body)
        except AuthError:
            LOG.warning("token 已失效，重新登录")
            self.login()
            return self._request(method, path, params, body)

        if res.get("ret") != 1 and is_auth_msg(res.get("msg")):
            LOG.warning("接口提示会话失效（%s），重新登录", res.get("msg"))
            self.login()
            return self._request(method, path, params, body)
        return res

    # --- 第 2 步：AP 列表 ------------------------------------------------
    def list_aps(self) -> list[dict]:
        res = self.call(
            "GET",
            "/api/ac/apsearch",
            params={"pagenum": 1, "numperpage": 500, "groupid": "all", "_t": now_ms()},
        )
        if res.get("ret") != 1:
            raise ACError(f"查 AP 列表失败：{res.get('msg') or res}")
        return list(res.get("aplist") or [])

    # --- 概览接口（仅 AC05 对账用，取不到就降级） -------------------------
    def overview(self) -> dict | None:
        try:
            res = self.call(
                "GET",
                "/api/status/overview",
                params={"simplify": "no", "_t": now_ms()},
            )
        except ACError:
            return None
        return res if res.get("acstatus") else None

    # --- 第 3 步：重新下发配置 -------------------------------------------
    def reissue(self, aps: list[dict]) -> dict[str, dict]:
        """按当前模板分组下发，等价于网页"勾选 AP → 重新下发配置"。

        返回 {template: 接口响应}
        """
        groups: dict[str, list[str]] = {}
        for ap in aps:
            mac = parse_mac(ap.get("mac"))
            if not mac:
                continue
            template = str(ap.get("template") or "default").strip() or "default"
            groups.setdefault(template, []).append(mac)

        results: dict[str, dict] = {}
        for template, macs in groups.items():
            payload = {"cmd": "settemplate", "maclist": ",".join(macs), "template": template}
            LOG.info("下发配置 template=%s maclist=%s", template, payload["maclist"])
            results[template] = self.call("POST", "/api/ac/apcmd", body=payload)
            LOG.info("下发结果 template=%s：%s", template, results[template])
        return results


# ---------------------------------------------------------------- 飞书告警


def feishu_sign(secret: str) -> dict:
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    return {"timestamp": timestamp, "sign": base64.b64encode(digest).decode()}


def build_card(kind: str, ap: dict, ctx: dict) -> dict:
    """kind: alert / remind / recovery（零终端）；offline / offline_remind / offline_recovery（离线）；
    online_zero（恢复上线但仍无终端）。"""
    count = ap.get("clientnum", 0)
    online = str(ap.get("online", "")).lower() == "yes"
    name = ap.get("name")
    try:
        report_ts = int(ap.get("reporttime") or 0)
    except (TypeError, ValueError):
        report_ts = 0
    last_report = ts_str(report_ts) if report_ts > 0 else "-"

    if kind == "recovery":
        title, color = f"✅ AP 终端数已恢复：{name}", "green"
    elif kind == "remind":
        title, color = f"⏰ AP 仍无终端：{name}", "orange"
    elif kind == "offline":
        title, color = f"🔌 AP 离线告警：{name}", "red"
    elif kind == "offline_remind":
        title, color = f"⏰ AP 仍离线：{name}", "orange"
    elif kind == "offline_recovery":
        title, color = f"✅ AP 已恢复上线：{name}", "green"
    elif kind == "online_zero":
        title, color = f"⚠️ AP 已上线但仍无终端：{name}", "orange"
    else:
        title, color = f"🚨 AP 零终端告警：{name}", "red"

    def field(label: str, value: object, short: bool = True) -> dict:
        return {"is_short": short, "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"}}

    detail = []
    if kind == "recovery":
        detail.append(f"终端数已回升到 **{count}**（阈值 >{ctx['threshold']}），告警解除。")
        detail.append(f"零终端持续：{ctx.get('duration', '-')}")
    elif kind in ("offline", "offline_remind"):
        detail.append(f"AP **{name}** 当前**离线**，最后一次上报：**{last_report}**。")
        detail.append(f"离线持续：{ctx.get('duration', '-')}；下次复查：{ctx.get('next_check', '-')}")
        detail.append("离线期间不会下发配置，AP 重新上线后自动发恢复卡片；若上线后仍无终端，转入零终端流程。")
    elif kind == "offline_recovery":
        detail.append(f"AP **{name}** 已重新上线，当前终端数 **{count}**。")
        detail.append(f"离线持续：{ctx.get('duration', '-')}")
    elif kind == "online_zero":
        detail.append(f"AP **{name}** 已重新上线，但终端数仍为 **0**，已自动重新下发配置。")
        detail.append(f"继续按零终端规则监控，终端数 >{ctx['threshold']} 才解除。")
        if ctx.get("reissue_result"):
            detail.append(f"下发接口返回：`{json.dumps(ctx['reissue_result'], ensure_ascii=False)}`")
    else:
        detail.append(f"AP **{name}** 当前终端数为 **{count}**，已自动重新下发配置。")
        detail.append(f"异常持续：{ctx.get('duration', '-')}；下次复查：{ctx.get('next_check', '-')}")
        if ctx.get("reissue_result"):
            detail.append(f"下发接口返回：`{json.dumps(ctx['reissue_result'], ensure_ascii=False)}`")

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {"template": color, "title": {"tag": "plain_text", "content": title}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(detail)}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        field("AP 名称", ap.get("name", "-")),
                        field("MAC", ap.get("mac", "-")),
                        field("IP", ap.get("ip", "-")),
                        field("在线状态", "在线" if online else "离线"),
                        field("终端数", count),
                        field("最后上报", last_report),
                        field("模板", ap.get("template", "-")),
                        field("型号 / 版本", f"{ap.get('model', '-')} / {ap.get('version', '-')}"),
                        field("AC 时间", ctx.get("checked_at", "-")),
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"集客AC控制台 {ctx['base_url']} · 第 {ctx.get('cycle', 0)} 轮巡检 · {ts_str()}",
                        }
                    ],
                },
            ],
        },
    }


def build_status_card(aps: list[dict], ctx: dict) -> dict:
    """每日状态卡片：每台 AP 的在线状态与终端数。"""

    def field(label: str, value: object, short: bool = True) -> dict:
        return {"is_short": short, "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"}}

    def ap_count(ap: dict) -> int:
        try:
            return int(ap.get("clientnum") or 0)
        except (TypeError, ValueError):
            return 0

    online = [ap for ap in aps if str(ap.get("online", "")).lower() == "yes"]
    total_clients = sum(ap_count(ap) for ap in aps)
    all_online = bool(aps) and len(online) == len(aps)

    lines = []
    for ap in sorted(aps, key=lambda a: str(a.get("name") or a.get("mac") or "")):
        is_online = str(ap.get("online", "")).lower() == "yes"
        mark = "🟢 在线" if is_online else "🔴 离线"
        lines.append(f"**{ap.get('name') or '-'}**　{mark}　·　终端 **{ap_count(ap)}**")
    if not lines:
        lines.append("AP 列表为空")

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": "green" if all_online else "orange",
                "title": {"tag": "plain_text", "content": f"📊 AP 状态 · {ctx['checked_at']}"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        field("AP 在线", f"{len(online)}/{len(aps)}"),
                        field("终端合计", total_clients),
                        field("采集时间", ctx["checked_at"]),
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"集客AC控制台 {ctx['base_url']} · 每日 09:00 / 14:00 推送",
                        }
                    ],
                },
            ],
        },
    }


def run_report(client: ACClient, args: argparse.Namespace) -> int:
    """拉一次 AP 列表，推一张状态卡片（在线状态 + 终端数）。"""
    try:
        aps = client.list_aps()
    except (AuthError, ACError) as exc:
        LOG.error("状态推送失败：%s", exc)
        return 1

    if not aps:
        LOG.warning("AP 列表为空，仍推送空状态")

    def ap_count(ap: dict) -> int:
        try:
            return int(ap.get("clientnum") or 0)
        except (TypeError, ValueError):
            return 0

    online = sum(1 for ap in aps if str(ap.get("online", "")).lower() == "yes")
    total_clients = sum(ap_count(ap) for ap in aps)
    LOG.info("AP 状态：在线 %d/%d，终端合计 %d", online, len(aps), total_clients)

    card = build_status_card(aps, {"base_url": client.base_url, "checked_at": ts_str()})
    ok = send_feishu(args.feishu_webhook, args.feishu_secret, card, dry_run=args.dry_run)
    return 0 if ok else 1


def send_feishu(webhook: str, secret: str, card: dict, dry_run: bool = False) -> bool:
    if not webhook:
        LOG.warning("未配置飞书 webhook，告警卡片仅打印：\n%s", json.dumps(card, ensure_ascii=False, indent=2))
        return False
    if dry_run:
        LOG.info("[dry-run] 飞书卡片：\n%s", json.dumps(card, ensure_ascii=False, indent=2))
        return True

    payload = dict(card)
    if secret:
        payload.update(feishu_sign(secret))

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
        result = json.loads(raw) if raw.strip() else {}
        ok = result.get("code") == 0 or result.get("StatusCode") == 0
        LOG.info("飞书返回：%s", raw.strip()[:200])
        if not ok:
            LOG.error("飞书告警发送失败：%s", raw.strip()[:200])
        return ok
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        LOG.error("飞书告警发送异常：%s", exc)
        return False


# ---------------------------------------------------------------- 巡检状态机


class Guard:
    # 每台 AP 一份状态：alerting_* 是"零终端"和"离线"两套独立告警
    STATE_DEFAULTS = {
        "alerting": False,      # 零终端告警中
        "since": 0,
        "last_alert": 0,
        "last_reissue": 0,
        "offline": False,       # 离线告警中
        "offline_since": 0,
        "last_offline_alert": 0,
    }

    def __init__(self, client: ACClient, args: argparse.Namespace):
        self.client = client
        self.args = args
        self.cycle = 0
        self.state: dict[str, dict] = {}
        if args.state_file and os.path.exists(args.state_file):
            try:
                with open(args.state_file, encoding="utf-8") as fh:
                    self.state = json.load(fh)
                LOG.info("已载入状态文件 %s（%d 个 AP）", args.state_file, len(self.state))
            except (OSError, json.JSONDecodeError) as exc:
                LOG.warning("状态文件读取失败，忽略：%s", exc)

    def _st(self, mac: str) -> dict:
        """取（或初始化）某台 AP 的状态，兼容旧版本状态文件缺字段。"""
        st = self.state.setdefault(mac, {})
        for key, default in self.STATE_DEFAULTS.items():
            st.setdefault(key, default)
        return st

    def save_state(self) -> None:
        if not self.args.state_file:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.args.state_file)), exist_ok=True)
            tmp = f"{self.args.state_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.args.state_file)
        except OSError as exc:
            LOG.warning("状态文件写入失败：%s", exc)

    def _filter_aps(self, aps: list[dict]) -> list[dict]:
        if not self.args.ap:
            return aps
        wanted = set()
        for item in self.args.ap.split(","):
            item = item.strip().lower()
            if not item:
                continue
            wanted.add(item)
            mac = parse_mac(item)
            if mac:
                wanted.add(mac)
        return [ap for ap in aps if str(ap.get("name", "")).lower() in wanted or parse_mac(ap.get("mac")) in wanted]

    def run_cycle(self) -> None:
        self.cycle += 1
        aps = self._filter_aps(self.client.list_aps())
        if not aps:
            LOG.warning("AP 列表为空（或过滤后为空），本轮跳过")
            return

        for ap in aps:
            mac = parse_mac(ap.get("mac")) or str(ap.get("name"))
            name = str(ap.get("name") or mac)
            try:
                count = int(ap.get("clientnum") or 0)
                uptime = int(ap.get("uptime") or 0)
            except (TypeError, ValueError):
                count, uptime = 0, 0
            online = str(ap.get("online", "")).lower() == "yes"
            st = self._st(mac)
            now = time.time()

            LOG.info(
                "%-6s %-12s %-14s 终端=%-3d 在线=%-3s 模板=%s",
                name,
                mac,
                ap.get("ip", "-"),
                count,
                "是" if online else "否",
                ap.get("template", "-"),
            )

            # ---- 离线状态机 ----
            if not online:
                if not st["offline"]:
                    st.update(offline=True, offline_since=now, last_offline_alert=now)
                    # 离线接管告警：同一台 AP 不再重复发零终端告警，等上线后重新判定
                    st["alerting"] = False
                    if self.args.offline_alert:
                        send_feishu(
                            self.args.feishu_webhook,
                            self.args.feishu_secret,
                            build_card(
                                "offline",
                                ap,
                                {
                                    "threshold": self.args.threshold,
                                    "base_url": self.client.base_url,
                                    "cycle": self.cycle,
                                    "checked_at": ts_str(),
                                    "duration": "刚开始",
                                    "next_check": ts_str(now + self.args.interval),
                                },
                            ),
                            dry_run=self.args.dry_run,
                        )
                elif self.args.offline_alert and self.args.remind > 0 and now - st["last_offline_alert"] >= self.args.remind:
                    st["last_offline_alert"] = now
                    send_feishu(
                        self.args.feishu_webhook,
                        self.args.feishu_secret,
                        build_card(
                            "offline_remind",
                            ap,
                            {
                                "threshold": self.args.threshold,
                                "base_url": self.client.base_url,
                                "cycle": self.cycle,
                                "checked_at": ts_str(),
                                "duration": self._duration(st["offline_since"]),
                                "next_check": ts_str(now + self.args.interval),
                            },
                        ),
                        dry_run=self.args.dry_run,
                    )
                continue

            # ---- 从离线恢复 ----
            if st["offline"]:
                st["offline"] = False
                if count == 0:
                    # 上线了但还没有终端：合并成一张卡片，并直接进入零终端告警态
                    st.update(alerting=True, since=now, last_alert=now)
                    reissue_result = self._reissue([ap])
                    st["last_reissue"] = now
                    send_feishu(
                        self.args.feishu_webhook,
                        self.args.feishu_secret,
                        build_card(
                            "online_zero",
                            ap,
                            {
                                "threshold": self.args.threshold,
                                "base_url": self.client.base_url,
                                "cycle": self.cycle,
                                "checked_at": ts_str(),
                                "duration": self._duration(st["offline_since"]),
                                "next_check": ts_str(now + self.args.interval),
                                "reissue_result": reissue_result,
                            },
                        ),
                        dry_run=self.args.dry_run,
                    )
                else:
                    send_feishu(
                        self.args.feishu_webhook,
                        self.args.feishu_secret,
                        build_card(
                            "offline_recovery",
                            ap,
                            {
                                "threshold": self.args.threshold,
                                "base_url": self.client.base_url,
                                "cycle": self.cycle,
                                "checked_at": ts_str(),
                                "duration": self._duration(st["offline_since"]),
                            },
                        ),
                        dry_run=self.args.dry_run,
                    )

            # ---- 零终端状态机 ----
            if count == 0:
                if not st["alerting"]:
                    # 刚重启的 AP 给终端回流窗口，避免"重启即告警+重推"
                    if 0 < uptime < self.args.uptime_grace:
                        LOG.info(
                            "%s 在线 %d 秒（< %d 秒豁免窗口），终端未回流，本轮不告警",
                            name, uptime, self.args.uptime_grace,
                        )
                        continue
                    # 刚掉到 0：下发配置 + 首告警
                    st.update(alerting=True, since=now, last_alert=now)
                    reissue_result = self._reissue([ap])
                    st["last_reissue"] = now
                    send_feishu(
                        self.args.feishu_webhook,
                        self.args.feishu_secret,
                        build_card(
                            "alert",
                            ap,
                            {
                                "threshold": self.args.threshold,
                                "base_url": self.client.base_url,
                                "cycle": self.cycle,
                                "checked_at": ts_str(),
                                "duration": "刚开始",
                                "next_check": ts_str(now + self.args.interval),
                                "reissue_result": reissue_result,
                            },
                        ),
                        dry_run=self.args.dry_run,
                    )
                else:
                    # 持续为 0：按冷却时间重复下发，按提醒间隔重复告警
                    if now - st["last_reissue"] >= self.args.reissue_cooldown:
                        st["last_reissue"] = now
                        self._reissue([ap])
                    if self.args.remind > 0 and now - st["last_alert"] >= self.args.remind:
                        st["last_alert"] = now
                        send_feishu(
                            self.args.feishu_webhook,
                            self.args.feishu_secret,
                            build_card(
                                "remind",
                                ap,
                                {
                                    "threshold": self.args.threshold,
                                    "base_url": self.client.base_url,
                                    "cycle": self.cycle,
                                    "checked_at": ts_str(),
                                    "duration": self._duration(st["since"]),
                                    "next_check": ts_str(now + self.args.interval),
                                },
                            ),
                            dry_run=self.args.dry_run,
                        )

            elif st["alerting"] and count > self.args.threshold:
                # 恢复到阈值以上：解除告警
                st["alerting"] = False
                LOG.info("%s 终端数回升到 %d，解除告警", name, count)
                send_feishu(
                    self.args.feishu_webhook,
                    self.args.feishu_secret,
                    build_card(
                        "recovery",
                        ap,
                        {
                            "threshold": self.args.threshold,
                            "base_url": self.client.base_url,
                            "cycle": self.cycle,
                            "checked_at": ts_str(),
                            "duration": self._duration(st.get("since", now)),
                        },
                    ),
                    dry_run=self.args.dry_run,
                )

        self.save_state()

    @staticmethod
    def _duration(since: float) -> str:
        if not since:
            return "-"
        seconds = max(0, int(time.time() - since))
        hours, rest = divmod(seconds, 3600)
        minutes = rest // 60
        return f"{hours} 小时 {minutes} 分" if hours else f"{minutes} 分"

    def _reissue(self, aps: list[dict]) -> dict:
        if self.args.dry_run:
            LOG.info("[dry-run] 跳过下发：%s", [ap.get("name") for ap in aps])
            return {"dry_run": True}
        try:
            results = self.client.reissue(aps)
            failed = {t: r for t, r in results.items() if r.get("ret") != 1}
            if failed:
                LOG.error("下发失败：%s", failed)
            return results
        except ACError as exc:
            LOG.error("下发配置异常：%s", exc)
            return {"error": str(exc)}


# ---------------------------------------------------------------- 一次性巡检


def run_inspect(client: ACClient, args: argparse.Namespace) -> int:
    """AC01–AC05 只读巡检；--fix 时对 AC03 命中的 AP 按模板重推（须授权）。

    退出码：0 健康 / 1 存在异常 / 2 巡检自身故障。
    """
    # AC01 服务可用：登录 + 取数任一失败即自身故障
    try:
        aps = client.list_aps()
    except AuthError as exc:
        LOG.error("巡检自身故障（AC01 登录失败）：%s", exc)
        return 2
    except ACError as exc:
        LOG.error("巡检自身故障（AC01 接口不可达）：%s", exc)
        return 2
    LOG.info("AC01 服务可用：登录成功")

    ov = client.overview()

    p1: list[str] = []
    p2: list[str] = []
    exempt: list[str] = []
    fix_aps: list[dict] = []

    # AC02 AP 在线率：任一离线 P1，全部离线 P0
    offline = [ap for ap in aps if str(ap.get("online", "")).lower() != "yes"]
    if offline:
        level = "P0" if len(offline) == len(aps) else "P1"
        names = "、".join(f"{ap.get('name')}({ap.get('ip')})" for ap in offline)
        p1.append(f"| AC02 | AP 在线率 | 离线:{names} | 0 台离线 | {level} | 查 POE 供电/网线,重电无效再查 AP 自身 |")

    # AC03 终端数异常：在线且 clientnum <= 阈值；在线不足豁免窗口记豁免
    for ap in aps:
        if str(ap.get("online", "")).lower() != "yes":
            continue
        try:
            count = int(ap.get("clientnum") or 0)
            uptime = int(ap.get("uptime") or 0)
        except (TypeError, ValueError):
            count, uptime = 0, 0
        if count > args.threshold:
            continue
        if uptime < args.uptime_grace:
            exempt.append(f"| AC03 | {ap.get('name')} | 终端数 {count},在线 {uptime // 60} 分钟,判为刚重启 |")
            continue
        template = str(ap.get("template") or "default")
        p1.append(
            f"| AC03 | {ap.get('name')} | 终端数异常 | {count} | ≤ {args.threshold} | P1 | "
            f"按模板 {template} 重推配置(--fix,须授权);复发则重启 AP |"
        )
        fix_aps.append(ap)

    # AC04 固件版本一致性：版本不唯一或有低版本标记
    versions = sorted({str(ap.get("version") or "") for ap in aps})
    if len(versions) > 1 or any(ap.get("is_low_version") is True for ap in aps):
        p2.append(f"| AC04 | 固件版本一致性 | 版本:{','.join(versions)} | 全网单一版本 | P2 | 低版本走升级流程,变更窗口执行 |")

    # AC05 终端总数对账：AP 侧之和 vs 概览
    if ov:
        try:
            ap_sum = sum(int(ap.get("clientnum") or 0) for ap in aps)
            ac_total = int((ov.get("acstatus") or {}).get("client_num") or 0)
            diff = abs(ap_sum - ac_total)
            limit = max(5, ac_total // 5)
            if diff > limit:
                p2.append(
                    f"| AC05 | 终端总数对账 | AP 侧 {ap_sum} vs AC 侧 {ac_total} | 差 ≤ {limit} 台 | P2 | "
                    f"两接口有取样时间差,持续偏差查假死终端 |"
                )
        except (TypeError, ValueError):
            ov = None
    else:
        p2.append("| AC05 | 终端总数对账 | 数据缺失(overview 不可达) | — | P2 | 检查 AC 状态接口后复巡 |")

    rc = 1 if (p1 or p2) else 0
    do_fix = bool(args.fix and fix_aps)

    # ---- 输出：固定先答"是否下发"，再全量清单，再异常 ----
    if do_fix and not args.dry_run:
        LOG.info("下发配置：有（--fix 处置，推送对象与受理结果见文末处置记录）")
    else:
        LOG.info("下发配置：无（未执行处置，未向 AC 下发任何命令）")

    LOG.info("---- 全量清单（名称/IP/连接数/状态/版本/模板） ----")
    for ap in aps:
        print(
            f"{str(ap.get('name', '-')):<10} {str(ap.get('ip', '-')):<16} "
            f"{str(ap.get('clientnum', '-')):<6} {str(ap.get('online', '-')):<6} "
            f"{str(ap.get('version', '-')):<16} {ap.get('template', '-')}"
        )

    if p1 or p2:
        LOG.info("---- 异常项（级别|编号|巡检项|当前值|阈值|处置建议） ----")
        for row in p1 + p2:
            print(row)
    else:
        LOG.info("巡检通过：AC01–AC05 全部健康")

    if exempt:
        LOG.info("---- 豁免项 ----")
        for row in exempt:
            print(row)

    # ---- 处置（仅 --fix）：按模板分组重推 ----
    if args.fix:
        if not fix_aps:
            LOG.info("处置：无 AC03 异常，无需重推")
        elif args.dry_run:
            LOG.info("[dry-run] 跳过下发：%s", [ap.get("name") for ap in fix_aps])
        else:
            try:
                for template, res in client.reissue(fix_aps).items():
                    if res.get("ret") == 1:
                        LOG.info("处置已下发：模板 %s 受理成功，终端回流以下一轮巡检为准", template)
                    else:
                        LOG.error("处置失败：模板 %s %s", template, res.get("msg") or res)
            except ACError as exc:
                LOG.error("处置失败：%s", exc)

    LOG.info("巡检结束（退出码 %d）", rc)
    return rc


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="集客 AC 巡检与 AP 零终端/离线守护")
    p.add_argument("--base-url", default=os.getenv("AC_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--password", default=os.getenv("AC_PASSWORD", DEFAULT_PASSWORD), help="控制台管理密码")
    p.add_argument("--feishu-webhook", default=os.getenv("FEISHU_WEBHOOK", ""), help="飞书自定义机器人 webhook")
    p.add_argument("--feishu-secret", default=os.getenv("FEISHU_SECRET", ""), help="飞书机器人签名密钥（可选）")
    p.add_argument("--threshold", type=int, default=int(os.getenv("AC_THRESHOLD", DEFAULT_THRESHOLD)),
                   help=f"终端数 > 该值视为恢复（默认 {DEFAULT_THRESHOLD}）")
    p.add_argument("--uptime-grace", type=int, default=int(os.getenv("AC_UPTIME_GRACE", 600)),
                   help="AC03 豁免：在线不足该秒数视为刚重启（默认 600）")
    p.add_argument("--interval", type=int, default=int(os.getenv("AC_INTERVAL", DEFAULT_INTERVAL)),
                   help=f"巡检间隔秒数（默认 {DEFAULT_INTERVAL}）")
    p.add_argument("--reissue-cooldown", type=int, default=int(os.getenv("AC_REISSUE_COOLDOWN", DEFAULT_REISSUE_COOLDOWN)),
                   help="持续 0 终端时重复下发的最小间隔秒数")
    p.add_argument("--remind", type=int, default=int(os.getenv("AC_REMIND", DEFAULT_REMIND)),
                   help="持续 0 终端时重复告警的间隔秒数，0 = 不重复")
    p.add_argument("--ap", default=os.getenv("AC_AP_FILTER", ""), help="只监控指定 AP，逗号分隔名称或 MAC")
    p.add_argument("--no-offline-alert", dest="offline_alert", action="store_false",
                   help="AP 离线时不发告警卡片（仍记录状态，上线后照常恢复判定）")
    p.set_defaults(offline_alert=os.getenv("AC_OFFLINE_ALERT", "1").lower() not in ("0", "false", "no"))
    p.add_argument("--state-file", default=os.getenv("AC_STATE_FILE", "ac_ap_guard_state.json"),
                   help="告警状态文件，--once 模式跨轮次保持")
    p.add_argument("--once", action="store_true", help="只巡检一轮就退出（适合 cron / systemd timer）")
    p.add_argument("--report", action="store_true",
                   help="拉一次 AP 列表并推送状态卡片后退出（供 systemd timer / cron 调用）")
    p.add_argument("--inspect", action="store_true",
                   help="跑一次 AC01–AC05 只读巡检后退出（退出码 0 健康 / 1 有异常 / 2 自身故障）")
    p.add_argument("--fix", action="store_true",
                   help="配合 --inspect：对 AC03 命中的 AP 按模板重推配置（须授权）")
    p.add_argument("--list", action="store_true", help="只打印 AP 列表后退出")
    p.add_argument("--test-card", nargs="?", const="alert", choices=["alert", "offline"],
                   help="发一张样例卡片后退出（alert=零终端，offline=离线），用来验证 webhook")
    p.add_argument("--dry-run", action="store_true", help="不下发、不发飞书，只打印")
    p.add_argument("--log-file", default=os.getenv("AC_LOG_FILE", ""))
    p.add_argument("--verbose", action="store_true")
    return p


def setup_logging(args: argparse.Namespace) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fix and not args.inspect:
        parser.error("--fix 只能与 --inspect 一起使用")
    setup_logging(args)

    client = ACClient(args.base_url, args.password)

    if args.test_card:
        sample = {
            "name": "ap-demo",
            "mac": "001122334455",
            "ip": "192.0.2.10",
            "online": "yes",
            "clientnum": 0,
            "template": "default",
            "model": "N3000",
            "version": "8.0_2024081000",
            "reporttime": int(time.time()) - 900,
        }
        ctx = {
            "threshold": args.threshold,
            "base_url": args.base_url,
            "cycle": 0,
            "checked_at": ts_str(),
            "duration": "15 分",
            "next_check": ts_str(time.time() + args.interval),
        }
        if args.test_card == "offline":
            sample.update(online="no", clientnum=0)
            card = build_card("offline", sample, ctx)
        else:
            ctx["reissue_result"] = {"ret": 1, "msg": "LANG687"}
            card = build_card("alert", sample, ctx)
        ok = send_feishu(args.feishu_webhook, args.feishu_secret, card, dry_run=args.dry_run)
        return 0 if ok else 1

    if args.report:
        try:
            return run_report(client, args)
        except ACError as exc:
            LOG.error("状态推送失败：%s", exc)
            return 1

    if args.inspect:
        try:
            return run_inspect(client, args)
        except ACError as exc:
            LOG.error("巡检自身故障：%s", exc)
            return 2

    guard = Guard(client, args)

    if args.list:
        aps = guard._filter_aps(guard.client.list_aps())
        print(f"共 {len(aps)} 台 AP")
        print(f"{'名称':<8}{'MAC':<14}{'IP':<16}{'在线':<6}{'终端':<6}{'模板':<10}型号")
        for ap in aps:
            print(
                f"{str(ap.get('name', '-')):<8}{str(ap.get('mac', '-')):<14}{str(ap.get('ip', '-')):<16}"
                f"{'在线' if ap.get('online') == 'yes' else '离线':<6}{int(ap.get('clientnum') or 0):<6}"
                f"{str(ap.get('template', '-')):<10}{ap.get('model', '-')}"
            )
        return 0

    if args.once:
        try:
            guard.run_cycle()
        except (ACError, AuthError) as exc:
            LOG.error("巡检失败：%s", exc)
            return 1
        return 0

    stopping = False

    def _stop(signum, _frame):
        nonlocal stopping
        stopping = True
        LOG.info("收到信号 %s，本轮结束后退出", signum)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    LOG.info(
        "启动守护：%s 每 %d 秒一轮，终端数 0 告警，>%d 恢复",
        args.base_url,
        args.interval,
        args.threshold,
    )
    while not stopping:
        started = time.time()
        try:
            guard.run_cycle()
        except AuthError as exc:
            LOG.error("登录失败：%s", exc)
        except ACError as exc:
            LOG.error("巡检失败：%s", exc)
        except Exception:  # 守护进程不能因为单轮异常退出
            LOG.exception("未预期异常，继续下一轮")

        elapsed = time.time() - started
        remain = max(5, args.interval - elapsed)
        LOG.info("本轮耗时 %.1fs，%d 秒后下一轮", elapsed, int(remain))
        deadline = time.time() + remain
        while not stopping and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    guard.save_state()
    LOG.info("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
