from __future__ import annotations

import httpx
import pytest

from lark2didi.config import FeishuConfig
from lark2didi.feishu import (
    FeishuClient,
    FeishuPermissionError,
    ReadOnlyViolation,
)


def _auth_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": 0,
            "msg": "ok",
            "tenant_access_token": "t-test",
            "expire": 7200,
        },
    )


def _client(handler) -> FeishuClient:
    config = FeishuConfig(app_id="cli_test", app_secret="secret")
    return FeishuClient(config, transport=httpx.MockTransport(handler))


def test_get_rejects_unknown_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发出请求")

    with _client(handler) as client:
        with pytest.raises(ReadOnlyViolation):
            client._get("/contact/v3/users/ou_123", {})


def test_client_has_no_write_methods() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _auth_response()

    with _client(handler) as client:
        for name in (
            "create_user",
            "update_user",
            "delete_user",
            "create_department",
            "update_department",
            "delete_department",
        ):
            assert not hasattr(client, name)


def test_list_departments_paginates() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return _auth_response()
        assert request.method == "GET"
        calls.append(dict(request.url.params))
        if "page_token" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "open_department_id": "od_1",
                                "parent_department_id": "0",
                                "name": "研发",
                                "member_count": 3,
                            }
                        ],
                        "has_more": True,
                        "page_token": "p2",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "open_department_id": "od_2",
                            "parent_department_id": "0",
                            "name": "销售",
                            "member_count": 2,
                        }
                    ],
                    "has_more": False,
                },
            },
        )

    with _client(handler) as client:
        departments = client.list_departments()

    assert [dept.open_department_id for dept in departments] == ["od_1", "od_2"]
    assert len(calls) == 2
    assert calls[1]["page_token"] == "p2"


def test_snapshot_dedupes_users_across_departments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/v3/tenant_access_token/internal"):
            return _auth_response()
        if path.endswith("/contact/v3/departments"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "open_department_id": "od_1",
                                "parent_department_id": "0",
                                "name": "研发",
                                "member_count": 1,
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        if path.endswith("/contact/v3/users/find_by_department"):
            user = {
                "open_id": "ou_1",
                "name": "张三",
                "employee_no": "L001",
                "department_ids": ["0", "od_1"],
            }
            return httpx.Response(
                200,
                json={"code": 0, "data": {"items": [user], "has_more": False}},
            )
        raise AssertionError(path)

    with _client(handler) as client:
        snapshot = client.snapshot()

    assert len(snapshot.departments) == 1
    assert len(snapshot.users) == 1
    assert snapshot.users[0].department_ids == ("0", "od_1")


def test_permission_error_is_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return _auth_response()
        return httpx.Response(
            200,
            json={
                "code": 99991672,
                "msg": (
                    "Access denied. One of the following scopes is required: "
                    "[contact:department.base:readonly]"
                ),
            },
        )

    with _client(handler) as client:
        with pytest.raises(FeishuPermissionError):
            client.list_departments()
