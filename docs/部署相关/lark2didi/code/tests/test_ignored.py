from __future__ import annotations

from pathlib import Path

import pytest

from lark2didi.ignored import (
    add_ignored,
    ignored_reasons,
    load_ignored,
    save_ignored,
)


def test_missing_file_returns_empty_state(tmp_path: Path) -> None:
    assert load_ignored(tmp_path / "missing.json") == {
        "version": 1,
        "ignored": {},
    }


def test_add_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "state" / "ignored_creates.json"
    state = load_ignored(path)

    add_ignored(
        state,
        phone="+86 138-0000-0001",
        name="测试员工",
        reason="员工已存在于其他企业",
    )
    save_ignored(state, path)

    loaded = load_ignored(path)
    assert "13800000001" in loaded["ignored"]
    assert loaded["ignored"]["13800000001"]["name"] == "测试员工"
    assert path.stat().st_mode & 0o777 == 0o600


def test_ignored_reasons_are_prefixed(tmp_path: Path) -> None:
    state = load_ignored(tmp_path / "missing.json")
    add_ignored(
        state,
        phone="13800000001",
        name="张三",
        reason="员工已存在于其他企业",
    )

    reasons = ignored_reasons(state)

    assert reasons["13800000001"] == (
        "自动忽略：员工已存在于其他企业"
    )


def test_empty_phone_raises(tmp_path: Path) -> None:
    state = load_ignored(tmp_path / "missing.json")

    with pytest.raises(ValueError):
        add_ignored(state, phone="", name="张三", reason="x")
