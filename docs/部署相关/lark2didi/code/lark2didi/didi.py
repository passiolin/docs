from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from .config import DidiConfig

# 当前阶段只读：只允许查询部门/成本中心和员工，不提供任何写/删方法。
READ_ONLY = True

_READ_ONLY_GET_PATHS = frozenset(
    {
        "/river/BudgetCenter/get",
        "/river/Member/get",
    }
)
_AUTH_PATH = "/river/Auth/authorize"
_SIGNATURE_ERROR_CODES = frozenset({19998, 19999})
_IP_WHITELIST_ERROR = 10002
_MAX_PAGES = 200
_PAGE_SIZE_LIMIT = 100


class DidiError(RuntimeError):
    def __init__(
        self,
        errno: int | str,
        errmsg: str,
        *,
        path: str = "",
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.errno = errno
        self.errmsg = errmsg
        self.path = path
        self.request_id = request_id
        self.status_code = status_code
        super().__init__(
            f"滴滴接口错误 errno={errno} errmsg={errmsg} path={path} "
            f"request_id={request_id}"
        )


class DidiAuthError(DidiError):
    """授权或签名失败。"""


class DidiReadOnlyViolation(RuntimeError):
    """试图调用未授权的滴滴接口时抛出。"""


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def sign_params(
    params: dict[str, Any], sign_key: str, method: str = "sha256"
) -> str:
    """滴滴签名：加入 sign_key，按 key 升序拼接，MD5/SHA256 小写 hex。"""
    if method not in ("md5", "sha256"):
        raise ValueError(f"不支持的签名算法: {method}")

    payload: dict[str, str] = {}
    for key, value in params.items():
        if key in ("sign", "sign_key") or value is None:
            continue
        payload[key] = _stringify(value)
    payload["sign_key"] = sign_key

    raw = "&".join(f"{key}={payload[key]}" for key in sorted(payload))
    return hashlib.new(method, raw.encode("utf-8")).hexdigest()


def _errno(data: dict[str, Any]) -> int:
    try:
        return int(data.get("errno", -1))
    except (TypeError, ValueError):
        return -1


class DidiClient:
    """滴滴企业版开放平台只读客户端。

    当前只允许 GET `/river/BudgetCenter/get` 和 `/river/Member/get`，
    唯一允许的 POST 是换取 access_token。类中没有任何写入/删除方法。
    """

    def __init__(
        self,
        config: DidiConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._config = config
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._sign_method: str | None = config.sign_method
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def __enter__(self) -> "DidiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def sign_method(self) -> str | None:
        return self._sign_method

    def _url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    @staticmethod
    def _parse(response: httpx.Response, *, path: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise DidiError(
                -1,
                f"响应不是合法 JSON: {response.text[:200]}",
                path=path,
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DidiError(
                -1,
                "响应 JSON 不是对象",
                path=path,
                status_code=response.status_code,
            )
        return data

    def authorize(self, *, force: bool = False) -> None:
        if (
            not force
            and self._token
            and time.monotonic() < self._token_expires_at
        ):
            return

        methods = (
            [self._config.sign_method]
            if self._config.sign_method
            else ([self._sign_method] if self._sign_method else ["sha256", "md5"])
        )
        failures: list[tuple[str, dict[str, Any]]] = []

        for method in methods:
            body: dict[str, Any] = {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "grant_type": "client_credentials",
                "timestamp": int(time.time()),
            }
            body["sign"] = sign_params(body, self._config.sign_key, method)
            response = self._http.post(self._url(_AUTH_PATH), json=body)
            data = self._parse(response, path=_AUTH_PATH)
            errno_value = data.get("errno")
            errno = _errno(data)

            # 授权成功时滴滴可能只返回 access_token，不带 errno。
            if data.get("access_token") and (
                errno_value is None or errno == 0
            ):
                self._token = str(data["access_token"])
                expires_in = int(data.get("expires_in") or 1800)
                self._token_expires_at = time.monotonic() + max(
                    expires_in - 60, 60
                )
                self._sign_method = method
                return

            if errno == _IP_WHITELIST_ERROR:
                raise DidiAuthError(
                    errno,
                    "IP 白名单校验失败，请把服务器出口 IP 加入开放平台"
                    "「开发配置 → 安全设置 → IP 白名单」",
                    path=_AUTH_PATH,
                    request_id=data.get("request_id"),
                    status_code=response.status_code,
                )
            if errno in _SIGNATURE_ERROR_CODES:
                failures.append((method, data))
                continue
            raise DidiAuthError(
                errno,
                str(data.get("errmsg") or "授权失败"),
                path=_AUTH_PATH,
                request_id=data.get("request_id"),
                status_code=response.status_code,
            )

        tried = ", ".join(
            f"{method}(errno={_errno(data)})" for method, data in failures
        )
        raise DidiAuthError(
            failures[-1][1].get("errno", -1) if failures else -1,
            f"签名失败，已尝试 {tried}；请核对 Sign Key 与开放平台签名算法",
            path=_AUTH_PATH,
            request_id=failures[-1][1].get("request_id") if failures else None,
        )

    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if path not in _READ_ONLY_GET_PATHS:
            raise DidiReadOnlyViolation(
                f"拒绝访问未授权的滴滴接口: {path}；"
                f"只允许 {sorted(_READ_ONLY_GET_PATHS)}"
            )
        if not self._sign_method:
            self.authorize()

        for attempt in range(2):
            self.authorize()
            query: dict[str, Any] = dict(params or {})
            query.update(
                {
                    "client_id": self._config.client_id,
                    "company_id": self._config.company_id,
                    "access_token": self._token,
                    "timestamp": int(time.time()),
                }
            )
            query["sign"] = sign_params(
                query, self._config.sign_key, self._sign_method or "sha256"
            )
            response = self._http.get(
                self._url(path),
                params=query,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            data = self._parse(response, path=path)
            errno = _errno(data)
            if errno == 0:
                return data
            if errno == 401 and attempt == 0:
                self._token = None
                self.authorize(force=True)
                continue
            raise DidiError(
                errno,
                str(data.get("errmsg") or "请求失败"),
                path=path,
                request_id=data.get("request_id"),
                status_code=response.status_code,
            )

        raise DidiError(-1, "请求重试耗尽", path=path)

    def _paginate(
        self,
        path: str,
        base_params: dict[str, Any] | None = None,
        *,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        length = max(1, min(int(page_size), _PAGE_SIZE_LIMIT))
        offset = 0
        records: list[dict[str, Any]] = []

        for _ in range(_MAX_PAGES):
            params = dict(base_params or {})
            params.update({"offset": offset, "length": length})
            data = self._get(path, params)
            payload = data.get("data") or {}
            batch = payload.get("records") or []
            if not isinstance(batch, list):
                raise DidiError(
                    -1, "响应 data.records 不是数组", path=path
                )
            records.extend(
                item for item in batch if isinstance(item, dict)
            )

            total = int(payload.get("total") or 0)
            offset += length
            if len(batch) < length:
                break
            if total and offset >= total:
                break
        else:
            raise DidiError(
                -1, f"分页超过 {_MAX_PAGES} 页，疑似 offset 不增长", path=path
            )

        return records

    def list_budget_centers(
        self, *, department_only: bool = True, page_size: int = 100
    ) -> list[dict[str, Any]]:
        base = {"type": 1} if department_only else {}
        return self._paginate(
            "/river/BudgetCenter/get", base, page_size=page_size
        )

    def list_members(
        self,
        *,
        page_size: int = 100,
        status: str | int | None = None,
        phone: str | None = None,
        employee_number: str | None = None,
        email: str | None = None,
        realname: str | None = None,
    ) -> list[dict[str, Any]]:
        base: dict[str, Any] = {}
        for key, value in (
            ("status", status),
            ("phone", phone),
            ("employee_number", employee_number),
            ("email", email),
            ("realname", realname),
        ):
            if value not in (None, ""):
                base[key] = value
        return self._paginate(
            "/river/Member/get", base or None, page_size=page_size
        )

    def snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return self.list_budget_centers(), self.list_members()
