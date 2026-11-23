from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from .config import FeishuConfig
from .models import Department, OrgSnapshot, User

# 飞书侧强约束：除换取 tenant_access_token 的 POST 外，只允许 GET。
READ_ONLY = True

_ALLOWED_GET_PATHS = frozenset(
    {
        "/contact/v3/departments",
        "/contact/v3/users",
        "/contact/v3/users/find_by_department",
    }
)
_TOKEN_INVALID_CODES = frozenset({99991661, 99991663, 99991668})
_RATE_LIMIT_CODE = 99991400
_PERMISSION_CODES = frozenset({99991672})
_MAX_PAGES = 1000


class FeishuError(RuntimeError):
    def __init__(
        self,
        code: int | str,
        msg: str,
        *,
        path: str = "",
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.msg = msg
        self.path = path
        self.status_code = status_code
        super().__init__(f"飞书接口错误 code={code} msg={msg} path={path}")


class FeishuPermissionError(FeishuError):
    """应用缺少接口或字段权限。"""


class ReadOnlyViolation(RuntimeError):
    """试图调用未授权的飞书接口时抛出。"""


class FeishuClient:
    """飞书通讯录只读客户端。

    该类不提供任何写入/删除方法；GET 路径也硬编码在 allowlist 内。
    唯一允许的 POST 是换取 tenant_access_token。
    """

    def __init__(
        self,
        config: FeishuConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._config = config
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    def __enter__(self) -> "FeishuClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    # ---- auth ---------------------------------------------------------
    def _authenticate(self) -> None:
        path = "/auth/v3/tenant_access_token/internal"
        response = self._http.post(
            self._url(path),
            json={
                "app_id": self._config.app_id,
                "app_secret": self._config.app_secret,
            },
        )
        data = self._json(response, path=path)
        if data.get("code") != 0:
            raise FeishuError(
                data.get("code", -1),
                data.get("msg", "auth failed"),
                path=path,
                status_code=response.status_code,
            )
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError(-1, "响应缺少 tenant_access_token", path=path)
        self._token = token
        self._token_expires_at = time.monotonic() + max(
            int(data.get("expire") or 7200) - 60, 60
        )

    def _ensure_token(self) -> None:
        if not self._token or time.monotonic() >= self._token_expires_at:
            self._authenticate()

    # ---- read-only GET ------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in _ALLOWED_GET_PATHS:
            raise ReadOnlyViolation(
                f"拒绝访问未授权的飞书接口: {path}；"
                f"只允许 {sorted(_ALLOWED_GET_PATHS)}"
            )

        last_error: FeishuError | None = None
        for attempt in range(3):
            self._ensure_token()
            response = self._http.get(
                self._url(path),
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            data = self._json(response, path=path)
            code = data.get("code")
            if code == 0:
                return data

            msg = str(data.get("msg") or "")
            if code in _TOKEN_INVALID_CODES and attempt < 2:
                self._token = None
                continue
            if (
                code == _RATE_LIMIT_CODE or response.status_code == 429
            ) and attempt < 2:
                time.sleep(1.0 + attempt)
                continue

            last_error = FeishuError(
                code, msg, path=path, status_code=response.status_code
            )
            if code in _PERMISSION_CODES or "Access denied" in msg:
                raise FeishuPermissionError(
                    code, msg, path=path, status_code=response.status_code
                )
            raise last_error

        raise last_error or FeishuError(-1, "请求重试耗尽", path=path)

    @staticmethod
    def _json(response: httpx.Response, *, path: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise FeishuError(
                -1,
                f"响应不是合法 JSON: {response.text[:200]}",
                path=path,
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise FeishuError(
                -1,
                "响应 JSON 不是对象",
                path=path,
                status_code=response.status_code,
            )
        return data

    def _paginate(
        self, path: str, params: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(_MAX_PAGES):
            query = dict(params)
            if page_token:
                query["page_token"] = page_token
            data = self._get(path, query)
            payload = data.get("data") or {}
            yield from payload.get("items") or []
            page_token = payload.get("page_token")
            if (
                not payload.get("has_more")
                or not page_token
                or page_token in seen_tokens
            ):
                return
            seen_tokens.add(page_token)
        raise FeishuError(
            -1, f"分页超过 {_MAX_PAGES} 页，疑似 page_token 循环", path=path
        )

    # ---- public read APIs ---------------------------------------------
    def list_departments(self) -> list[Department]:
        params = {
            "parent_department_id": "0",
            "fetch_child": "true",
            "page_size": 50,
            "department_id_type": "open_department_id",
            "user_id_type": "open_id",
        }
        return [
            Department.from_api(raw)
            for raw in self._paginate("/contact/v3/departments", params)
        ]

    def list_users(self, department_id: str) -> list[User]:
        params = {
            "department_id": department_id,
            "page_size": 50,
            "department_id_type": "open_department_id",
            "user_id_type": "open_id",
        }
        return [
            User.from_api(raw)
            for raw in self._paginate(
                "/contact/v3/users/find_by_department", params
            )
        ]

    def snapshot(self) -> OrgSnapshot:
        departments = self.list_departments()
        users: dict[str, User] = {}
        # 根部门（ID=0）也要拉：只挂在根部门的人不会出现在任何子部门里。
        department_ids = ["0", *(dept.open_department_id for dept in departments)]
        for department_id in department_ids:
            for user in self.list_users(department_id):
                users.setdefault(user.open_id, user)
        return OrgSnapshot(
            departments=tuple(departments), users=tuple(users.values())
        )
