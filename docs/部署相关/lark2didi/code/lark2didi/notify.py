from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / "conf" / ".env"
_TZ = ZoneInfo("Asia/Shanghai")


class WebhookError(RuntimeError):
    """飞书 webhook 推送失败。"""


def load_webhook_url(env_path: Path | str = DEFAULT_ENV_PATH) -> str | None:
    url = (os.getenv("FEISHU_WEBHOOK") or "").strip()
    if not url:
        path = Path(env_path)
        if path.is_file():
            url = (dotenv_values(path).get("FEISHU_WEBHOOK") or "").strip()
    return url or None


def send_card(
    title: str,
    lines: Iterable[str],
    *,
    template: str = "blue",
    note: str | None = None,
    webhook_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 10.0,
) -> bool:
    url = (
        load_webhook_url()
        if webhook_url is None
        else webhook_url.strip()
    )
    if not url:
        return False

    items = [str(line).strip() for line in lines if str(line).strip()]
    shown = items[:15]
    if len(items) > 15:
        shown.append(f"... 其余 {len(items) - 15} 条见日志")
    content = "\n".join(f"- {line}" for line in shown) or "- 无明细"

    timestamp = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    footer = f"{note} | {timestamp}" if note else timestamp

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title[:100]},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": footer}],
            },
        ],
    }

    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(
                url, json={"msg_type": "interactive", "card": card}
            )
    except httpx.HTTPError as exc:
        raise WebhookError(
            f"飞书 webhook 网络错误: {type(exc).__name__}"
        ) from None
    except Exception as exc:
        raise WebhookError(
            f"飞书 webhook 请求失败: {type(exc).__name__}"
        ) from None

    if response.status_code >= 400:
        raise WebhookError(f"飞书 webhook HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        raise WebhookError("飞书 webhook 返回非 JSON") from None

    code = data.get("code", data.get("StatusCode"))
    if code not in (0, "0", None):
        message = str(
            data.get("msg") or data.get("StatusMessage") or ""
        ).strip()
        raise WebhookError(
            f"飞书 webhook 返回 code={code} {message}".strip()
        )
    return True


def send_card_safe(
    title: str,
    lines: Iterable[str],
    *,
    template: str = "blue",
    note: str | None = None,
) -> bool:
    try:
        return send_card(title, lines, template=template, note=note)
    except WebhookError as exc:
        print(f"警告: {exc}")
        return False


if __name__ == "__main__":
    import sys

    title = sys.argv[1] if len(sys.argv) > 1 else "lark2didi 测试"
    lines = sys.argv[2:] or ["Webhook 已接通"]
    print("sent" if send_card_safe(title, lines) else "skipped")
