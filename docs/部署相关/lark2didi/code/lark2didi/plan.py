from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .models import OrgSnapshot, User


def normalize_phone(value: str | None) -> str:
    """把 +86 / 86 / 空格 / 横线统一成 11 位大陆手机号。"""
    if not value:
        return ""
    text = re.sub(r"[\s\-()+]", "", str(value))
    if text.startswith("0086"):
        text = text[4:]
    elif text.startswith("86") and len(text) == 13:
        text = text[2:]
    return text


def normalize_name(value: str | None) -> str:
    """只保留汉字，用于识别“英文名/中文名”差异和换手机号的同一人。"""
    if not value:
        return ""
    return re.sub(r"[^\u4e00-\u9fff]", "", str(value))


def mask_phone(value: str | None) -> str:
    phone = normalize_phone(value)
    if not phone:
        return "<无>"
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


@dataclass(frozen=True, slots=True)
class MatchedPair:
    user: User
    member: dict
    phone: str
    name_matches: bool


@dataclass(frozen=True, slots=True)
class PlannedCreate:
    user: User
    phone: str
    employee_number: str | None
    email: str | None
    department_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhoneConflict:
    user: User
    member: dict
    feishu_phone: str
    didi_phone: str


@dataclass(frozen=True, slots=True)
class SkippedUser:
    user: User
    reasons: tuple[str, ...]
    department_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyncPlan:
    matched: tuple[MatchedPair, ...]
    create: tuple[PlannedCreate, ...]
    skipped: tuple[SkippedUser, ...]
    phone_conflicts: tuple[PhoneConflict, ...]
    didi_only_active: tuple[dict, ...]
    didi_resigned: tuple[dict, ...]
    resigned_conflicts: tuple[MatchedPair, ...]
    feishu_missing_phone: tuple[User, ...]
    ambiguous: tuple[str, ...]
    root_center: dict | None


def _department_names(user: User, departments: dict[str, str]) -> tuple[str, ...]:
    names = [
        departments[dept_id]
        for dept_id in user.department_ids
        if dept_id in departments and departments[dept_id]
    ]
    return tuple(names) if names else ("根部门",)


def build_plan(
    feishu: OrgSnapshot,
    didi_centers: list[dict],
    didi_members: list[dict],
    *,
    skip_department_keywords: tuple[str, ...] = ("外包",),
    require_employee_no: bool = True,
    ignored_reasons: Mapping[str, str] | None = None,
) -> SyncPlan:
    departments = {
        dept.open_department_id: dept.name or ""
        for dept in feishu.departments
    }
    root_center = next(
        (
            center
            for center in didi_centers
            if str(center.get("parent_id") or "0") == "0"
            and str(center.get("type") or "1") == "1"
        ),
        didi_centers[0] if didi_centers else None,
    )

    fs_by_phone: dict[str, list[User]] = {}
    missing_phone: list[User] = []
    for user in feishu.users:
        phone = normalize_phone(user.mobile)
        if not phone:
            missing_phone.append(user)
            continue
        fs_by_phone.setdefault(phone, []).append(user)

    dd_by_phone: dict[str, list[dict]] = {}
    for member in didi_members:
        phone = normalize_phone(member.get("phone"))
        if phone:
            dd_by_phone.setdefault(phone, []).append(member)

    ambiguous: list[str] = []
    matched: list[MatchedPair] = []
    resigned_conflicts: list[MatchedPair] = []
    used_users: set[str] = set()
    used_members: set[int] = set()

    for phone in sorted(set(fs_by_phone) & set(dd_by_phone)):
        users = fs_by_phone[phone]
        members = dd_by_phone[phone]
        if len(users) != 1 or len(members) != 1:
            ambiguous.append(f"手机号 {mask_phone(phone)} 在飞书或滴滴重复")
            used_users.update(user.open_id for user in users)
            used_members.update(id(member) for member in members)
            continue
        user, member = users[0], members[0]
        pair = MatchedPair(
            user=user,
            member=member,
            phone=phone,
            name_matches=normalize_name(user.name)
            == normalize_name(member.get("realname")),
        )
        if str(member.get("status") or "") == "4":
            resigned_conflicts.append(pair)
        else:
            matched.append(pair)
        used_users.add(user.open_id)
        used_members.add(id(member))

    fs_unmatched = [
        user for user in feishu.users if user.open_id not in used_users
    ]
    dd_unmatched = [
        member for member in didi_members if id(member) not in used_members
    ]

    fs_name_counts: dict[str, int] = {}
    for user in fs_unmatched:
        name = normalize_name(user.name)
        if name:
            fs_name_counts[name] = fs_name_counts.get(name, 0) + 1

    dd_name_groups: dict[str, list[dict]] = {}
    for member in dd_unmatched:
        if str(member.get("status") or "") == "4":
            continue
        name = normalize_name(member.get("realname"))
        if name:
            dd_name_groups.setdefault(name, []).append(member)

    phone_conflicts: list[PhoneConflict] = []
    for user in list(fs_unmatched):
        name = normalize_name(user.name)
        if not name or fs_name_counts.get(name) != 1:
            continue
        members = dd_name_groups.get(name) or []
        if len(members) != 1:
            continue
        member = members[0]
        feishu_phone = normalize_phone(user.mobile)
        didi_phone = normalize_phone(member.get("phone"))
        phone_conflicts.append(
            PhoneConflict(
                user=user,
                member=member,
                feishu_phone=feishu_phone,
                didi_phone=didi_phone,
            )
        )
        used_users.add(user.open_id)
        used_members.add(id(member))

    create: list[PlannedCreate] = []
    skipped: list[SkippedUser] = []
    for user in feishu.users:
        if user.open_id in used_users:
            continue
        phone = normalize_phone(user.mobile)
        if not phone:
            continue
        department_names = _department_names(user, departments)
        employee_number = (user.employee_no or "").strip() or None
        reasons: list[str] = []
        if any(
            keyword in name
            for keyword in skip_department_keywords
            for name in department_names
        ):
            reasons.append("部门含“外包”")
        if require_employee_no and not employee_number:
            reasons.append("缺工号")
        manual_reason = (ignored_reasons or {}).get(phone)
        if manual_reason:
            reasons.append(manual_reason)
        if reasons:
            skipped.append(
                SkippedUser(
                    user=user,
                    reasons=tuple(reasons),
                    department_names=department_names,
                )
            )
            continue
        create.append(
            PlannedCreate(
                user=user,
                phone=phone,
                employee_number=employee_number,
                email=(user.email or "").strip() or None,
                department_names=department_names,
            )
        )

    didi_only_active: list[dict] = []
    didi_resigned: list[dict] = []
    for member in didi_members:
        if id(member) in used_members:
            continue
        if str(member.get("status") or "") == "4":
            didi_resigned.append(member)
        else:
            didi_only_active.append(member)

    return SyncPlan(
        matched=tuple(matched),
        create=tuple(create),
        skipped=tuple(skipped),
        phone_conflicts=tuple(phone_conflicts),
        didi_only_active=tuple(didi_only_active),
        didi_resigned=tuple(didi_resigned),
        resigned_conflicts=tuple(resigned_conflicts),
        feishu_missing_phone=tuple(missing_phone),
        ambiguous=tuple(ambiguous),
        root_center=root_center,
    )
