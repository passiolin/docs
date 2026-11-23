from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Department:
    open_department_id: str
    parent_department_id: str
    name: str | None
    member_count: int
    leader_user_id: str | None
    order: str | None

    @classmethod
    def from_api(cls, raw: dict) -> "Department":
        return cls(
            open_department_id=str(raw.get("open_department_id") or ""),
            parent_department_id=str(raw.get("parent_department_id") or "0"),
            name=raw.get("name"),
            member_count=int(raw.get("member_count") or 0),
            leader_user_id=raw.get("leader_user_id"),
            order=raw.get("order"),
        )


@dataclass(frozen=True, slots=True)
class User:
    open_id: str
    user_id: str | None
    name: str
    en_name: str | None
    email: str | None
    mobile: str | None
    employee_no: str | None
    department_ids: tuple[str, ...]
    primary_department_id: str | None = None

    @classmethod
    def from_api(cls, raw: dict) -> "User":
        department_ids = tuple(
            str(item) for item in (raw.get("department_ids") or [])
        )
        orders = raw.get("orders") or []
        primary_department_id = next(
            (
                str(order.get("department_id"))
                for order in orders
                if order.get("is_primary_dept")
                and str(order.get("department_id")) != "0"
            ),
            None,
        )
        if not primary_department_id:
            primary_department_id = next(
                (dept for dept in department_ids if dept != "0"), None
            )
        return cls(
            open_id=str(raw.get("open_id") or ""),
            user_id=raw.get("user_id"),
            name=str(raw.get("name") or ""),
            en_name=raw.get("en_name"),
            email=raw.get("email"),
            mobile=raw.get("mobile"),
            employee_no=raw.get("employee_no"),
            department_ids=department_ids,
            primary_department_id=primary_department_id,
        )


@dataclass(frozen=True, slots=True)
class OrgSnapshot:
    departments: tuple[Department, ...]
    users: tuple[User, ...]

    @property
    def departments_without_name(self) -> tuple[Department, ...]:
        return tuple(dept for dept in self.departments if not dept.name)
