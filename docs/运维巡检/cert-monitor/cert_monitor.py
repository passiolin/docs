#!/usr/bin/env python3
"""SSL 证书日报。

逐个探测目标列表里的 TLS 端点:握手、读叶子证书、校验主机名与信任链,
按剩余天数分级,每轮推一张飞书卡片。只做 TLS 握手与证书读取,
不改动对端;不涉及私钥。

用法:
    python3 cert_monitor.py --targets conf/cert-monitor.targets.example --dry-run
    python3 cert_monitor.py --targets /etc/cert-monitor/targets.conf
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import hmac
import ipaddress
from collections import Counter
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

DAY = 86400.0
STATUS_RANK = {"OK": 0, "WARN": 1, "CRIT": 2}
DEFAULT_OPTIONS = {"ca", "verify", "warn", "crit", "sni", "name"}


class ConfigError(Exception):
    """目标文件或环境变量配置错误。"""


@dataclass
class Target:
    host: str
    port: int = 443
    sni: Optional[str] = None
    ca: Optional[str] = None
    verify: str = "system"
    warn: float = 30.0
    crit: float = 7.0
    name: Optional[str] = None
    line: int = 0

    @property
    def instance(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return self.name or self.instance


@dataclass
class Result:
    target: Target
    ip: str = ""
    tls_version: str = ""
    cipher: str = ""
    subject: str = ""
    issuer: str = ""
    cn: str = ""
    sans: list[str] = field(default_factory=list)
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    days_left: Optional[float] = None
    trusted: Optional[bool] = None
    verify_error: str = ""
    hostname_ok: Optional[bool] = None
    error: str = ""
    duration: float = 0.0
    status: str = "OK"
    reasons: list[str] = field(default_factory=list)

    def raise_status(self, status: str, reason: str) -> None:
        if STATUS_RANK[status] > STATUS_RANK[self.status]:
            self.status = status
        if reason not in self.reasons:
            self.reasons.append(reason)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 不是数字: {raw}") from exc


def split_hostport(value: str, default_port: int) -> tuple[str, int]:
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ConfigError(f"IPv6 地址缺少右括号: {value}")
        host, rest = value[1:end], value[end + 1 :]
        if not rest:
            port = default_port
        elif rest.startswith(":"):
            port = int(rest[1:])
        else:
            raise ConfigError(f"端口格式不对: {value}")
    elif value.count(":") == 1:
        host, raw_port = value.split(":", 1)
        port = int(raw_port)
    else:
        host, port = value, default_port
    if not host:
        raise ConfigError(f"主机名为空: {value}")
    if not 1 <= port <= 65535:
        raise ConfigError(f"端口越界: {value}")
    return host, port


def openssl_command() -> str:
    openssl = shutil.which("openssl")
    if not openssl:
        raise ConfigError("找不到 openssl 命令")
    return openssl


def openssl_x509(pem: str, *args: str) -> str:
    cmd = [openssl_command(), "x509", "-noout", *args]
    try:
        proc = subprocess.run(
            cmd, input=pem, text=True, capture_output=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("openssl x509 解析超时") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"openssl 退出码 {proc.returncode}")
    return proc.stdout


def parse_cert_pem(pem: str) -> dict:
    """用 openssl 解析 PEM,返回主题、签发者、CN、SAN 与起止时间。"""
    text = openssl_x509(pem, "-startdate", "-enddate", "-subject", "-issuer", "-nameopt", "RFC2253")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()

    try:
        not_before = parsedate_to_datetime(fields["notBefore"])
        not_after = parsedate_to_datetime(fields["notAfter"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"证书时间字段解析失败: {fields}") from exc

    subject = fields.get("subject", "")
    issuer = fields.get("issuer", "")
    cn_match = re.search(r"(?:^|,)CN=(.*?)(?:,|$)", subject)
    cn = cn_match.group(1) if cn_match else ""

    san_text = openssl_x509(pem, "-ext", "subjectAltName")
    sans = re.findall(r"DNS:([^,\s]+)", san_text)
    sans += re.findall(r"IP Address:([^,\s]+)", san_text)
    return {
        "subject": subject,
        "issuer": issuer,
        "cn": cn,
        "sans": sans,
        "not_before": not_before,
        "not_after": not_after,
    }


def parse_targets(path: str, default_warn: float, default_crit: float) -> list[Target]:
    target_file = Path(path)
    if not target_file.is_file():
        raise ConfigError(f"目标文件不存在: {path}")

    targets: list[Target] = []
    for lineno, raw in enumerate(target_file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        try:
            host, port = split_hostport(parts[0], 443)
        except (ConfigError, ValueError) as exc:
            raise ConfigError(f"{path}:{lineno}: {exc}") from exc

        opts: dict[str, str] = {}
        for token in parts[1:]:
            if "=" not in token:
                raise ConfigError(f"{path}:{lineno}: 选项要写成 key=value: {token}")
            key, value = token.split("=", 1)
            opts[key] = value
        unknown = set(opts) - DEFAULT_OPTIONS
        if unknown:
            raise ConfigError(f"{path}:{lineno}: 未知选项 {', '.join(sorted(unknown))}")

        verify = opts.get("verify", "system")
        if verify not in ("system", "none"):
            raise ConfigError(f"{path}:{lineno}: verify 只能是 system 或 none")

        try:
            target = Target(
                host=host,
                port=port,
                sni=opts.get("sni", host),
                ca=opts.get("ca"),
                verify=verify,
                warn=float(opts.get("warn", default_warn)),
                crit=float(opts.get("crit", default_crit)),
                name=opts.get("name"),
                line=lineno,
            )
        except ValueError as exc:
            raise ConfigError(f"{path}:{lineno}: 阈值不是数字: {exc}") from exc

        if target.ca:
            if not Path(target.ca).is_file():
                raise ConfigError(f"{path}:{lineno}: CA 文件不存在: {target.ca}")
            try:
                ssl.create_default_context(cafile=target.ca)
            except (OSError, ssl.SSLError) as exc:
                raise ConfigError(f"{path}:{lineno}: CA 文件不可用: {exc}") from exc
        if target.warn < target.crit:
            raise ConfigError(f"{path}:{lineno}: warn 不能小于 crit")
        targets.append(target)

    if not targets:
        raise ConfigError(f"目标文件里没有有效目标: {path}")
    return targets


def hostname_matches(host: str, sans: list[str], cn: str) -> bool:
    host = host.lower().rstrip(".")
    names = [item.lower().rstrip(".") for item in sans]
    if not names and cn:
        names = [cn.lower().rstrip(".")]
    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        pass

    for name in names:
        if name == host:
            return True
        if is_ip:
            continue
        if name.startswith("*.") and host.count(".") == name.count(".") and host.endswith(name[1:]):
            return True
    return False


def describe_exc(exc: Exception) -> str:
    if isinstance(exc, socket.gaierror):
        return f"DNS 解析失败: {exc}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接或握手超时"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"链校验失败: {exc.verify_message or exc}"
    if isinstance(exc, ssl.SSLError):
        return f"TLS 握手失败: {exc.reason or exc}"
    if isinstance(exc, ConnectionRefusedError):
        return "连接被拒绝"
    if isinstance(exc, OSError):
        return f"连接失败: {exc.strerror or exc}"
    return str(exc)


def tls_handshake(target: Target, timeout: float) -> tuple[str, str, str, bytes]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((target.host, target.port), timeout=timeout) as sock:
        peer_ip = sock.getpeername()[0]
        with context.wrap_socket(sock, server_hostname=target.sni or target.host) as tls:
            version = tls.version() or ""
            cipher = tls.cipher()
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError("服务端没有返回证书")
    return peer_ip, version, cipher[0] if cipher else "", der


def check_chain(target: Target, timeout: float) -> tuple[Optional[bool], str]:
    if target.verify == "none":
        return None, ""
    context = ssl.create_default_context(cafile=target.ca) if target.ca else ssl.create_default_context()
    try:
        with socket.create_connection((target.host, target.port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=target.sni or target.host):
                pass
        return True, ""
    except ssl.SSLCertVerificationError as exc:
        return False, exc.verify_message or str(exc)
    except ssl.SSLError as exc:
        return False, f"TLS 错误: {exc.reason or exc}"
    except OSError as exc:
        # 主探测刚成功,这里失败多半是瞬时问题;不把连接错误算成证书不受信。
        return None, f"链校验未完成: {describe_exc(exc)}"


def probe(target: Target, timeout: float) -> Result:
    result = Result(target=target)
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    try:
        result.ip, result.tls_version, result.cipher, der = tls_handshake(target, timeout)
        pem = ssl.DER_cert_to_PEM_cert(der)
        leaf = parse_cert_pem(pem)
        result.subject = leaf["subject"]
        result.issuer = leaf["issuer"]
        result.cn = leaf["cn"]
        result.sans = leaf["sans"]
        result.not_before = leaf["not_before"]
        result.not_after = leaf["not_after"]
        result.days_left = (leaf["not_after"] - now).total_seconds() / DAY
        result.hostname_ok = hostname_matches(target.host, leaf["sans"], leaf["cn"])
    except Exception as exc:  # 探测失败要给出可读原因,不能吞掉
        result.error = describe_exc(exc)
        result.raise_status("CRIT", result.error)
        result.duration = time.monotonic() - started
        return result

    try:
        result.trusted, result.verify_error = check_chain(target, timeout)
    except Exception as exc:  # 证书链校验自身出错时,不让整轮采集崩掉
        result.trusted, result.verify_error = None, f"链校验异常: {describe_exc(exc)}"

    if result.days_left is not None:
        if result.days_left <= 0:
            result.raise_status("CRIT", "证书已过期")
        elif result.days_left <= target.crit:
            result.raise_status("CRIT", f"剩余 {result.days_left:.1f} 天,低于 {target.crit:g} 天")
        elif result.days_left <= target.warn:
            result.raise_status("WARN", f"剩余 {result.days_left:.1f} 天,低于 {target.warn:g} 天")

    if result.hostname_ok is False:
        result.raise_status("CRIT", f"主机名不匹配,证书只覆盖 {', '.join(result.sans) or result.cn}")

    if result.trusted is False:
        result.raise_status("CRIT", f"信任链校验失败: {result.verify_error}")
    elif result.trusted is None and target.verify != "none" and result.verify_error:
        result.raise_status("WARN", result.verify_error)

    result.duration = time.monotonic() - started
    return result


def probe_with_retry(target: Target, timeout: float) -> Result:
    """连接类失败重试一次,避免 10:00 的瞬时抖动直接报红。"""
    result = probe(target, timeout)
    if result.error:
        time.sleep(1)
        result = probe(target, timeout)
    return result


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def status_chain(result: Result) -> str:
    if result.trusted is True:
        return "OK"
    if result.trusted is False:
        return "FAIL"
    return "SKIP"


def print_table(results: list[Result], stream) -> None:
    headers = ["目标", "IP", "证书 CN", "到期日", "剩余", "链", "主机名", "TLS", "状态"]
    rows = []
    for result in results:
        expiry = result.not_after.strftime("%Y-%m-%d") if result.not_after else "-"
        days = f"{result.days_left:.1f}d" if result.days_left is not None else "-"
        hostname = "-" if result.hostname_ok is None else ("OK" if result.hostname_ok else "FAIL")
        rows.append([
            result.target.label,
            result.ip or "-",
            result.cn or "-",
            expiry,
            days,
            status_chain(result),
            hostname,
            result.tls_version or "-",
            result.status,
        ])

    widths = [display_width(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))
    print("  ".join(pad(header, widths[i]) for i, header in enumerate(headers)), file=stream)
    print("  ".join("-" * width for width in widths), file=stream)
    for row in rows:
        print("  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)), file=stream)

    reasons = [(result.target.label, reason) for result in results for reason in result.reasons]
    if reasons:
        print("", file=stream)
        for label, reason in reasons:
            print(f"! {label}: {reason}", file=stream)


def fetch_webhook() -> tuple[str, str]:
    return (
        os.environ.get("FEISHU_WEBHOOK", "").strip(),
        os.environ.get("FEISHU_SECRET", "").strip(),
    )


STATUS_ICON = {"OK": "🟢", "WARN": "🟠", "CRIT": "🔴"}
GROUP_ORDER = [("dev", "DEV"), ("test", "TEST"), ("other", "其他")]


def target_group(result: Result) -> str:
    host = result.target.host.lower()
    if host.startswith(("dev-", "dev.")):
        return "dev"
    if host.startswith(("test-", "test.")):
        return "test"
    return "other"


def target_line(result: Result) -> str:
    """一个端点一行:主机 + 到期时间;只有异常时才在行尾补原因。"""
    icon = STATUS_ICON[result.status]
    if result.error:
        return f"{icon} {result.target.label} 探测失败：{result.error}"
    if result.days_left is None:
        days = "剩 -"
    elif result.days_left <= 0:
        days = f"已过期 {-result.days_left:.0f} 天"
    else:
        days = f"剩 {result.days_left:.0f} 天"
    expiry = result.not_after.strftime("%Y-%m-%d") if result.not_after else "-"
    line = f"{icon} {result.target.label} 到期时间：{expiry}({days})"
    notes = []
    if result.trusted is False:
        notes.append("链失败")
    elif result.trusted is None and result.verify_error:
        notes.append("链未完成")
    if result.hostname_ok is False:
        notes.append("主机名不匹配")
    if notes:
        line += "（" + "，".join(notes) + "）"
    return line


def build_card(results: list[Result], when: datetime) -> dict:
    worst = "OK"
    for result in results:
        if STATUS_RANK[result.status] > STATUS_RANK[worst]:
            worst = result.status
    color = {"OK": "green", "WARN": "orange", "CRIT": "red"}[worst]
    counts = Counter(result.status for result in results)

    elements = [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**{len(results)} 个端点** ｜ "
                f"🟢 正常 {counts['OK']} ｜ 🟠 警告 {counts['WARN']} ｜ 🔴 严重 {counts['CRIT']}"
            ),
        },
    }]

    for key, label in GROUP_ORDER:
        items = [result for result in results if target_group(result) == key]
        if not items:
            continue
        items = sorted(items, key=lambda item: STATUS_RANK[item.status], reverse=True)
        group_counts = Counter(result.status for result in items)
        parts = [f"{len(items)} 个"]
        for status in ("CRIT", "WARN", "OK"):
            if group_counts[status]:
                parts.append(f"{STATUS_ICON[status]} {group_counts[status]}")
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{label}**（{' · '.join(parts)}）"},
        })
        body = "\n".join(target_line(result) for result in items)
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": body}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": f"SSL 证书日报 · {when:%m-%d %H:%M}"},
        },
        "elements": elements,
    }


def feishu_payload(card: dict, secret: str) -> dict:
    payload: dict = {"msg_type": "interactive", "card": card}
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        sign = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    return payload


def send_feishu(webhook: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"飞书 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"飞书请求失败: {exc.reason}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"飞书返回不是 JSON: {raw[:200]}") from exc
    code = data.get("code", data.get("StatusCode"))
    if code not in (0, "0", None):
        raise RuntimeError(f"飞书返回 code={code} msg={data.get('msg') or data.get('StatusMessage')}")


def result_json(result: Result) -> dict:
    return {
        "instance": result.target.instance,
        "name": result.target.label,
        "ip": result.ip,
        "cn": result.cn,
        "subject": result.subject,
        "issuer": result.issuer,
        "sans": result.sans,
        "not_before": result.not_before.isoformat() if result.not_before else None,
        "not_after": result.not_after.isoformat() if result.not_after else None,
        "days_left": round(result.days_left, 3) if result.days_left is not None else None,
        "trusted": result.trusted,
        "verify_error": result.verify_error,
        "hostname_ok": result.hostname_ok,
        "tls_version": result.tls_version,
        "cipher": result.cipher,
        "status": result.status,
        "reasons": result.reasons,
        "error": result.error,
        "duration_seconds": round(result.duration, 3),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SSL 证书日报")
    parser.add_argument("--targets", default=os.environ.get("TARGETS_FILE", "/etc/cert-monitor/targets.conf"),
                        help="目标文件,默认 $TARGETS_FILE 或 /etc/cert-monitor/targets.conf")
    parser.add_argument("--timeout", type=float, default=None, help="单次连接/握手超时秒数,默认 8")
    parser.add_argument("--warn", type=float, default=None, help="默认警告阈值(天)")
    parser.add_argument("--crit", type=float, default=None, help="默认严重阈值(天)")
    parser.add_argument("--dry-run", action="store_true", help="不发飞书,打印卡片 JSON")
    parser.add_argument("--json", action="store_true", help="输出探测结果 JSON")
    args = parser.parse_args(argv)

    machine_output = bool(args.json or args.dry_run)
    table_stream = sys.stderr if machine_output else sys.stdout

    try:
        timeout = args.timeout if args.timeout is not None else env_float("PROBE_TIMEOUT", 8.0)
        warn = args.warn if args.warn is not None else env_float("WARN_DAYS", 30.0)
        crit = args.crit if args.crit is not None else env_float("CRIT_DAYS", 7.0)
        targets = parse_targets(args.targets, warn, crit)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    checked_at = int(time.time())
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        results = list(pool.map(lambda item: probe_with_retry(item, timeout), targets))

    print_table(results, table_stream)

    worst = "OK"
    for result in results:
        if STATUS_RANK[result.status] > STATUS_RANK[worst]:
            worst = result.status

    card = build_card(results, datetime.now().astimezone())
    payload = feishu_payload(card, fetch_webhook()[1])

    if args.json:
        report = {
            "checked_at": datetime.fromtimestamp(checked_at, timezone.utc).isoformat(),
            "worst": worst,
            "targets": [result_json(result) for result in results],
        }
        if args.dry_run:
            report["feishu_payload"] = payload
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("dry-run: 未发送飞书", file=sys.stderr)
    else:
        webhook, _ = fetch_webhook()
        if not webhook:
            print("未配置 FEISHU_WEBHOOK,卡片未发送", file=sys.stderr)
            return 2
        try:
            send_feishu(webhook, payload)
            print("已推送飞书卡片", file=sys.stderr)
        except RuntimeError as exc:
            print(f"飞书推送失败: {exc}", file=sys.stderr)
            return 5

    return {"OK": 0, "WARN": 3, "CRIT": 4}[worst]


if __name__ == "__main__":
    sys.exit(main())
