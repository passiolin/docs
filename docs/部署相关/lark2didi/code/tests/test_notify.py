from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from lark2didi.notify import (
    WebhookError,
    load_webhook_url,
    send_card,
)


def test_load_webhook_url_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://example.com/hook")

    assert load_webhook_url() == "https://example.com/hook"


def test_load_webhook_url_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        'FEISHU_WEBHOOK="https://example.com/file-hook"\n',
        encoding="utf-8",
    )

    assert load_webhook_url(path) == "https://example.com/file-hook"


def test_missing_webhook_url_returns_false() -> None:
    assert send_card("标题", ["内容"], webhook_url="") is False


def test_send_card_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    ok = send_card(
        "lark2didi 测试",
        ["第一行", "第二行"],
        webhook_url="https://example.com/hook",
        transport=httpx.MockTransport(handler),
        note="测试备注",
    )

    assert ok is True
    assert seen["url"] == "https://example.com/hook"
    payload = seen["json"]
    assert payload["msg_type"] == "interactive"
    card = payload["card"]
    assert card["header"]["title"]["content"] == "lark2didi 测试"
    assert "第一行" in card["elements"][0]["text"]["content"]
    assert "测试备注" in card["elements"][1]["elements"][0]["content"]


def test_send_card_error_code_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 19001, "msg": "invalid webhook"}
        )

    with pytest.raises(WebhookError):
        send_card(
            "标题",
            ["内容"],
            webhook_url="https://example.com/hook",
            transport=httpx.MockTransport(handler),
        )


def test_send_card_truncates_long_lists() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    lines = [f"第 {index} 行" for index in range(20)]
    send_card(
        "标题",
        lines,
        webhook_url="https://example.com/hook",
        transport=httpx.MockTransport(handler),
    )

    content = seen["json"]["card"]["elements"][0]["text"]["content"]
    assert "其余 5 条见日志" in content
