from __future__ import annotations

import json

import httpx
import pytest

from lark2didi.config import DidiConfig
from lark2didi.didi import (
    DidiAuthError,
    DidiClient,
    DidiError,
    DidiReadOnlyViolation,
    sign_params,
)


def _config(sign_method: str | None = "sha256") -> DidiConfig:
    return DidiConfig(
        client_id="cid",
        client_secret="secret",
        sign_key="sk",
        company_id="company",
        sign_method=sign_method,
    )


def _auth_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "errno": 0,
            "errmsg": "success",
            "access_token": "tok",
            "expires_in": 1800,
            "request_id": "req-auth",
        },
    )


def _client(handler, *, sign_method: str | None = "sha256") -> DidiClient:
    return DidiClient(
        _config(sign_method), transport=httpx.MockTransport(handler)
    )


def test_sign_vectors() -> None:
    params = {"a": "x", "b": 2, "timestamp": 1700000000}
    assert (
        sign_params(params, "sk", "md5")
        == "66fc3167b2b4268b32c8c3ea958bcad5"
    )
    assert (
        sign_params(params, "sk", "sha256")
        == "29e24b57ae386862be42839e9b71e6351d2cc94d0534b45a74d41ca1501ba23d"
    )


def test_authorize_signs_body() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/river/Auth/authorize")
        body = json.loads(request.content)
        seen.append(body)
        assert body["grant_type"] == "client_credentials"
        assert body["client_id"] == "cid"
        assert "sign" in body
        assert "sign_key" not in body
        return _auth_ok()

    with _client(handler) as client:
        client.authorize()

    assert client.sign_method == "sha256"
    assert len(seen) == 1


def test_authorize_accepts_success_without_errno() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "expires_in": 1800,
                "token_type": "Bearer",
                "scope": None,
            },
        )

    with _client(handler) as client:
        client.authorize()

    assert client.sign_method == "sha256"


def test_authorize_falls_back_to_md5() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                200, json={"errno": 19999, "errmsg": "sign failed"}
            )
        return _auth_ok()

    with _client(handler, sign_method=None) as client:
        client.authorize()

    assert client.sign_method == "md5"
    assert len(calls) == 2


def test_authorize_ip_whitelist_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errno": 10002, "errmsg": "ip invalid"}
        )

    with _client(handler) as client:
        with pytest.raises(DidiAuthError, match="IP 白名单"):
            client.authorize()


def test_list_budget_centers_signs_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        assert request.method == "GET"
        assert request.url.path.endswith("/river/BudgetCenter/get")
        params = dict(request.url.params)
        assert params["client_id"] == "cid"
        assert params["company_id"] == "company"
        assert params["access_token"] == "tok"
        assert params["type"] == "1"
        assert "sign" in params
        assert "sign_key" not in params
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "errmsg": "success",
                "request_id": "req-1",
                "data": {
                    "total": 1,
                    "records": [
                        {
                            "id": "bc-1",
                            "name": "研发部",
                            "type": 1,
                            "parent_id": "0",
                        }
                    ],
                },
            },
        )

    with _client(handler) as client:
        centers = client.list_budget_centers()

    assert [center["id"] for center in centers] == ["bc-1"]


def test_list_members_paginates() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        params = dict(request.url.params)
        offset = int(params["offset"])
        length = int(params["length"])
        offsets.append(offset)
        if offset == 0:
            records = [
                {"id": "m-1", "realname": "张三"},
                {"id": "m-2", "realname": "李四"},
            ]
            total = 3
        else:
            records = [{"id": "m-3", "realname": "王五"}]
            total = 3
        assert offset % length == 0
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "errmsg": "success",
                "request_id": "req-page",
                "data": {"total": total, "records": records},
            },
        )

    with _client(handler) as client:
        members = client.list_members(page_size=2)

    assert [member["id"] for member in members] == ["m-1", "m-2", "m-3"]
    assert offsets == [0, 2]


def test_read_only_rejects_write_paths() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发出请求")

    with _client(handler) as client:
        for path in (
            "/river/Member/single",
            "/river/Member/edit",
            "/river/Member/del",
            "/river/BudgetCenter/add",
        ):
            with pytest.raises(DidiReadOnlyViolation):
                client._get(path, {})


def test_client_has_no_write_methods() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _auth_ok()

    with _client(handler) as client:
        for name in (
            "create_member",
            "update_member",
            "delete_member",
            "create_budget_center",
            "update_budget_center",
            "delete_budget_center",
        ):
            assert not hasattr(client, name)


def test_api_error_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/river/Auth/authorize"):
            return _auth_ok()
        return httpx.Response(
            200,
            json={
                "errno": 500,
                "errmsg": "server error",
                "request_id": "req-err",
            },
        )

    with _client(handler) as client:
        with pytest.raises(DidiError, match="server error"):
            client.list_members()
