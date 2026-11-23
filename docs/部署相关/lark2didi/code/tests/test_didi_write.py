from __future__ import annotations

import json

import httpx
import pytest

from lark2didi.config import DidiConfig
from lark2didi.didi_write import (
    DidiBudgetCenterWriter,
    DidiMemberExistsError,
    DidiMemberNotEditableError,
    DidiMemberWriter,
)


def _config() -> DidiConfig:
    return DidiConfig(
        client_id="cid",
        client_secret="secret",
        sign_key="sk",
        company_id="company",
        sign_method="sha256",
    )


def _auth_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "tok",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": None,
        },
    )


def _writer(handler) -> DidiMemberWriter:
    return DidiMemberWriter(
        _config(), transport=httpx.MockTransport(handler)
    )


def test_create_member_body_shape() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.method == "POST"
        assert request.url.path.endswith("/river/Member/single")
        body = json.loads(request.content)
        seen.append(body)
        assert body["client_id"] == "cid"
        assert body["access_token"] == "tok"
        assert body["company_id"] == "company"
        assert body["send_message"] == 0
        assert "sign" in body
        assert "sign_key" not in body
        member = json.loads(body["data"])
        assert member["realname"] == "测试员工"
        assert member["member_type"] == 0
        assert member["phone"] == "13800000001"
        assert member["employee_number"] == "E001"
        assert "email" not in member
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "errmsg": "success",
                "data": {"id": "m-new", "phone": "13800000001"},
            },
        )

    with _writer(handler) as creator:
        result = creator.create_member(
            realname="测试员工",
            phone="13800000001",
            employee_number="E001",
        )

    assert result["data"]["id"] == "m-new"
    assert len(seen) == 1


def test_create_member_existing_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        return httpx.Response(
            200,
            json={
                "errno": 50202,
                "errmsg": "员工已存在",
                "data": {"status": 1},
                "request_id": "req-exists",
            },
        )

    with _writer(handler) as creator:
        with pytest.raises(DidiMemberExistsError):
            creator.create_member(realname="张三", phone="13800000001")


def test_create_member_retries_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr("lark2didi.didi_write.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                200, json={"errno": 10003, "errmsg": "请求过于频繁"}
            )
        return httpx.Response(
            200, json={"errno": 0, "data": {"id": "m-new"}}
        )

    with _writer(handler) as creator:
        result = creator.create_member(realname="张三", phone="13800000001")

    assert len(calls) == 2
    assert result["data"]["id"] == "m-new"


def test_creator_has_no_update_or_delete_methods() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _auth_ok()

    with _writer(handler) as creator:
        assert hasattr(creator, "disable_member")
        for name in (
            "update_member",
            "delete_member",
            "create_budget_center",
            "update_budget_center",
            "delete_budget_center",
        ):
            assert not hasattr(creator, name)


def test_disable_member_body_shape() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.method == "POST"
        assert request.url.path.endswith("/river/Member/edit")
        body = json.loads(request.content)
        seen.append(body)
        assert body["member_id"] == "1126000000000001"
        assert "sign" in body
        assert "sign_key" not in body
        data = json.loads(body["data"])
        assert data["set_dismiss_time"] == "2026-11-07 10:00:00"
        return httpx.Response(200, json={"errno": 0, "data": {}})

    with _writer(handler) as writer:
        writer.disable_member(
            member_id="1126000000000001",
            dismiss_time="2026-11-07 10:00:00",
        )

    assert len(seen) == 1


def test_disable_member_not_editable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        return httpx.Response(
            200,
            json={
                "errno": 50223,
                "errmsg": "只有正常状态员工可以编辑",
                "request_id": "req-50223",
            },
        )

    with _writer(handler) as writer:
        with pytest.raises(DidiMemberNotEditableError):
            writer.disable_member(
                member_id="1126000000000001",
                dismiss_time="2026-11-07 10:00:00",
            )


def test_move_member_body_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.url.path.endswith("/river/Member/edit")
        body = json.loads(request.content)
        assert body["member_id"] == "1126000000000001"
        data = json.loads(body["data"])
        assert data == {"budget_center_id": "1126000000000099"}
        return httpx.Response(200, json={"errno": 0, "data": {}})

    with _writer(handler) as writer:
        writer.move_member(
            member_id="1126000000000001",
            budget_center_id="1126000000000099",
        )


def test_create_department_body_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.url.path.endswith("/river/BudgetCenter/add")
        body = json.loads(request.content)
        assert body["type"] == 1
        assert body["name"] == "研发部"
        assert body["out_budget_id"] == "od-123"
        assert body["parent_id"] == "1126000000000000"
        assert body["budget_cycle"] == 0
        assert body["total_quota"] == "0"
        assert body["leader_id"] == "1126000000000001"
        return httpx.Response(200, json={"errno": 0, "data": {"id": "bc-new"}})

    with DidiBudgetCenterWriter(
        _config(), transport=httpx.MockTransport(handler)
    ) as writer:
        result = writer.create_department(
            name="研发部",
            out_budget_id="od-123",
            parent_id="1126000000000000",
            leader_id="1126000000000001",
        )

    assert result["data"]["id"] == "bc-new"


def test_update_department_body_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.url.path.endswith("/river/BudgetCenter/edit")
        body = json.loads(request.content)
        assert body["id"] == "bc-1"
        assert body["type"] == 1
        assert body["name"] == "研发部"
        assert body["out_budget_id"] == "od-123"
        assert body["parent_id"] == "1126000000000000"
        assert body["leader_id"] == "1126000000000001"
        return httpx.Response(200, json={"errno": 0, "data": {}})

    with DidiBudgetCenterWriter(
        _config(), transport=httpx.MockTransport(handler)
    ) as writer:
        writer.update_department(
            department_id="bc-1",
            name="研发部",
            out_budget_id="od-123",
            parent_id="1126000000000000",
            leader_id="1126000000000001",
        )


def test_update_department_clear_leader() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.url.path.endswith("/river/BudgetCenter/edit")
        body = json.loads(request.content)
        assert body["leader_id"] == ""
        return httpx.Response(200, json={"errno": 0, "data": {}})

    with DidiBudgetCenterWriter(
        _config(), transport=httpx.MockTransport(handler)
    ) as writer:
        writer.update_department(
            department_id="bc-1",
            name="研发部",
            out_budget_id="od-123",
            parent_id="root",
            clear_leader=True,
        )


def test_budget_center_writer_has_no_delete_methods() -> None:
    with DidiBudgetCenterWriter(_config()) as writer:
        assert not hasattr(writer, "delete_department")
        assert not hasattr(writer, "delete_budget_center")
