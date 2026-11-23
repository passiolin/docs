from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import DidiConfig
from .didi import DidiAuthError, DidiError, sign_params

_AUTH_PATH = "/river/Auth/authorize"
_CREATE_MEMBER_PATH = "/river/Member/single"
_EDIT_MEMBER_PATH = "/river/Member/edit"
_IP_WHITELIST_ERROR = 10002
_SIGNATURE_ERROR_CODES = frozenset({19998, 19999})
_RATE_LIMIT_ERROR = 10003
_MEMBER_EXISTS_ERROR = 50202
_MEMBER_NOT_EDITABLE_ERROR = 50223

_TZ = ZoneInfo("Asia/Shanghai")


class DidiMemberExistsError(DidiError):
    """员工已存在（50202）。"""

    def __init__(
        self,
        errno: int | str,
        errmsg: str,
        *,
        data: Any = None,
        **kwargs: Any,
    ) -> None:
        self.data = data
        super().__init__(errno, errmsg, **kwargs)


class DidiMemberNotEditableError(DidiError):
    """员工不是正常状态，不能编辑（50223）。"""


def _int_errno(data: dict[str, Any]) -> int:
    try:
        return int(data.get("errno", -1))
    except (TypeError, ValueError):
        return -1


def default_dismiss_time(minutes_ahead: int = 5) -> str:
    """滴滴要求离职时间晚于当前时间；留 5 分钟，等待其定时任务处理。"""
    moment = datetime.now(_TZ) + timedelta(minutes=minutes_ahead)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class DidiMemberWriter:
    """员工写入客户端：只支持新增和设置离职时间，不支持删除。"""

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

    def __enter__(self) -> "DidiMemberWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

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

    def _authorize(self, *, force: bool = False) -> None:
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
        failures: list[dict[str, Any]] = []

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
            errno = _int_errno(data)

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
                )
            if errno in _SIGNATURE_ERROR_CODES:
                failures.append(data)
                continue
            raise DidiAuthError(
                errno,
                str(data.get("errmsg") or "授权失败"),
                path=_AUTH_PATH,
                request_id=data.get("request_id"),
            )

        raise DidiAuthError(
            failures[-1].get("errno", -1) if failures else -1,
            "签名失败，请核对 Sign Key 与开放平台签名算法",
            path=_AUTH_PATH,
            request_id=failures[-1].get("request_id") if failures else None,
        )

    def _sign(self, body: dict[str, Any]) -> None:
        body["sign"] = sign_params(
            body, self._config.sign_key, self._sign_method or "sha256"
        )

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        not_editable_error: bool = False,
    ) -> dict[str, Any]:
        for attempt in range(3):
            response = self._http.post(self._url(path), json=body)
            data = self._parse(response, path=path)
            errno = _int_errno(data)

            if errno == 0:
                return data
            if errno == 401 and attempt == 0:
                self._token = None
                self._authorize(force=True)
                body["access_token"] = self._token
                body["timestamp"] = int(time.time())
                self._sign(body)
                continue
            if errno == _RATE_LIMIT_ERROR and attempt < 2:
                time.sleep(0.5 + attempt)
                continue
            if errno == _MEMBER_EXISTS_ERROR:
                raise DidiMemberExistsError(
                    errno,
                    str(data.get("errmsg") or "员工已存在"),
                    data=data.get("data"),
                    path=path,
                    request_id=data.get("request_id"),
                    status_code=response.status_code,
                )
            if not_editable_error and errno == _MEMBER_NOT_EDITABLE_ERROR:
                raise DidiMemberNotEditableError(
                    errno,
                    str(data.get("errmsg") or "员工不是正常状态，不能编辑"),
                    path=path,
                    request_id=data.get("request_id"),
                    status_code=response.status_code,
                )
            raise DidiError(
                errno,
                str(data.get("errmsg") or "请求失败"),
                path=path,
                request_id=data.get("request_id"),
                status_code=response.status_code,
            )

        raise DidiError(-1, "请求重试耗尽", path=path)

    def create_member(
        self,
        *,
        realname: str,
        phone: str,
        employee_number: str | None = None,
        email: str | None = None,
        send_message: int = 0,
    ) -> dict[str, Any]:
        """新增一个员工；不修改、不删除任何现有员工。"""
        self._authorize()

        member: dict[str, Any] = {
            "realname": realname,
            "member_type": 0,
            "phone": phone,
        }
        if employee_number:
            member["employee_number"] = employee_number
        if email:
            member["email"] = email

        body: dict[str, Any] = {
            "client_id": self._config.client_id,
            "access_token": self._token,
            "company_id": self._config.company_id,
            "timestamp": int(time.time()),
            "data": json.dumps(member, ensure_ascii=False),
            "send_message": send_message,
        }
        self._sign(body)
        return self._post(_CREATE_MEMBER_PATH, body)

    def disable_member(
        self,
        *,
        member_id: str,
        dismiss_time: str | None = None,
    ) -> dict[str, Any]:
        """设置离职时间，让滴滴把员工转为离职；不删除员工。"""
        self._authorize()
        if not dismiss_time:
            dismiss_time = default_dismiss_time()

        body: dict[str, Any] = {
            "client_id": self._config.client_id,
            "access_token": self._token,
            "company_id": self._config.company_id,
            "timestamp": int(time.time()),
            "member_id": str(member_id),
            "data": json.dumps(
                {"set_dismiss_time": dismiss_time}, ensure_ascii=False
            ),
        }
        self._sign(body)
        return self._post(
            _EDIT_MEMBER_PATH, body, not_editable_error=True
        )

    def move_member(
        self,
        *,
        member_id: str,
        budget_center_id: str,
    ) -> dict[str, Any]:
        """只修改员工的主部门，不改其他字段。"""
        self._authorize()
        body: dict[str, Any] = {
            "client_id": self._config.client_id,
            "access_token": self._token,
            "company_id": self._config.company_id,
            "timestamp": int(time.time()),
            "member_id": str(member_id),
            "data": json.dumps(
                {"budget_center_id": str(budget_center_id)},
                ensure_ascii=False,
            ),
        }
        self._sign(body)
        return self._post(
            _EDIT_MEMBER_PATH, body, not_editable_error=True
        )


class DidiBudgetCenterWriter(DidiMemberWriter):
    """成本中心写入：只支持部门新增/修改，不支持删除。"""

    def create_department(
        self,
        *,
        name: str,
        out_budget_id: str,
        parent_id: str,
        leader_id: str | None = None,
    ) -> dict[str, Any]:
        self._authorize()
        body: dict[str, Any] = {
            "client_id": self._config.client_id,
            "access_token": self._token,
            "company_id": self._config.company_id,
            "timestamp": int(time.time()),
            "type": 1,
            "name": name,
            "out_budget_id": out_budget_id,
            "budget_cycle": 0,
            "total_quota": "0",
            "parent_id": str(parent_id),
        }
        if leader_id:
            body["leader_id"] = str(leader_id)
        self._sign(body)
        return self._post("/river/BudgetCenter/add", body)

    def update_department(
        self,
        *,
        department_id: str,
        name: str,
        out_budget_id: str,
        parent_id: str,
        leader_id: str | None = None,
        clear_leader: bool = False,
    ) -> dict[str, Any]:
        self._authorize()
        body: dict[str, Any] = {
            "client_id": self._config.client_id,
            "access_token": self._token,
            "company_id": self._config.company_id,
            "timestamp": int(time.time()),
            "id": str(department_id),
            "type": 1,
            "name": name,
            "out_budget_id": out_budget_id,
            "parent_id": str(parent_id),
        }
        if clear_leader:
            body["leader_id"] = ""
        elif leader_id:
            body["leader_id"] = str(leader_id)
        self._sign(body)
        return self._post("/river/BudgetCenter/edit", body)
