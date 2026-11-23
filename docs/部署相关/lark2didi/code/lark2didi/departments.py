from __future__ import annotations

from dataclasses import dataclass

from .models import Department, OrgSnapshot, User
from .plan import normalize_name, normalize_phone


@dataclass(frozen=True, slots=True)
class DepartmentPlanItem:
    feishu: Department
    action: str  # create / update / unchanged
    didi_id: str | None
    parent_feishu_id: str | None
    parent_didi_id: str | None
    leader_state: str  # matched / none / unmatched
    leader_member_id: str | None
    leader_member_name: str | None
    clear_leader: bool = False


@dataclass(frozen=True, slots=True)
class MemberMove:
    user: User
    member: dict
    from_center_id: str
    to_department: Department
    to_center_id: str | None


@dataclass(frozen=True, slots=True)
class DepartmentSyncPlan:
    root_center: dict | None
    departments: tuple[DepartmentPlanItem, ...]
    moves: tuple[MemberMove, ...]
    unmatched_users: tuple[User, ...]
    leaders_not_found: tuple[Department, ...]
    departments_without_leader: tuple[Department, ...]
    orphaned_departments: tuple[dict, ...]
    users_without_primary: tuple[User, ...]
    multi_department_users: tuple[User, ...]


class _MemberIndex:
    def __init__(self, members: list[dict]) -> None:
        self.by_phone: dict[str, dict] = {}
        self.by_employee_number: dict[str, dict] = {}
        self.by_name: dict[str, list[dict]] = {}
        for member in members:
            phone = normalize_phone(member.get("phone"))
            if phone:
                self.by_phone.setdefault(phone, member)
            employee_number = str(member.get("employee_number") or "").strip()
            if employee_number:
                self.by_employee_number.setdefault(employee_number, member)
            name = normalize_name(member.get("realname"))
            if name:
                self.by_name.setdefault(name, []).append(member)

    def find(self, user: User | None) -> dict | None:
        if user is None:
            return None
        phone = normalize_phone(user.mobile)
        if phone and phone in self.by_phone:
            return self.by_phone[phone]
        employee_number = (user.employee_no or "").strip()
        if employee_number and employee_number in self.by_employee_number:
            return self.by_employee_number[employee_number]
        name = normalize_name(user.name)
        if name:
            group = self.by_name.get(name) or []
            if len(group) == 1:
                return group[0]
        return None


def _root_center(centers: list[dict]) -> dict | None:
    return next(
        (
            center
            for center in centers
            if str(center.get("type") or "1") == "1"
            and str(center.get("parent_id") or "0") == "0"
        ),
        centers[0] if centers else None,
    )


def _existing_center(
    centers: list[dict],
    department: Department,
    parent_didi_id: str | None,
) -> dict | None:
    for center in centers:
        if str(center.get("out_budget_id") or "") == department.open_department_id:
            return center
    if parent_didi_id:
        for center in centers:
            if (
                str(center.get("type") or "1") == "1"
                and str(center.get("name") or "") == (department.name or "")
                and str(center.get("parent_id") or "0") == parent_didi_id
            ):
                return center
    return None


def _orphaned_departments(
    feishu: OrgSnapshot, centers: list[dict]
) -> tuple[dict, ...]:
    feishu_ids = {
        department.open_department_id for department in feishu.departments
    }
    return tuple(
        center
        for center in centers
        if str(center.get("type") or "1") == "1"
        and str(center.get("out_budget_id") or "").startswith("od-")
        and str(center.get("out_budget_id")) not in feishu_ids
    )


def build_department_plan(
    feishu: OrgSnapshot,
    centers: list[dict],
    members: list[dict],
) -> DepartmentSyncPlan:
    root = _root_center(centers)
    root_id = str(root.get("id")) if root else None
    users_by_open_id = {user.open_id: user for user in feishu.users}
    member_index = _MemberIndex(members)

    children: dict[str, list[Department]] = {}
    for department in feishu.departments:
        children.setdefault(department.parent_department_id, []).append(
            department
        )
    for items in children.values():
        items.sort(key=lambda dept: (dept.order or "", dept.name or ""))

    items_by_feishu_id: dict[str, DepartmentPlanItem] = {}
    id_map: dict[str, str | None] = {}
    leaders_not_found: list[Department] = []
    departments_without_leader: list[Department] = []

    queue = list(children.get("0", []))
    while queue:
        department = queue.pop(0)
        parent_feishu_id = (
            None
            if department.parent_department_id == "0"
            else department.parent_department_id
        )
        parent_didi_id = (
            root_id
            if parent_feishu_id is None
            else id_map.get(parent_feishu_id)
        )

        leader_user_id = (department.leader_user_id or "").strip()
        if not leader_user_id:
            leader_state = "none"
            leader_member = None
            departments_without_leader.append(department)
        else:
            leader_user = users_by_open_id.get(leader_user_id)
            leader_member = member_index.find(leader_user)
            if leader_member:
                leader_state = "matched"
            else:
                leader_state = "unmatched"
                leaders_not_found.append(department)

        leader_member_id = (
            str(leader_member.get("id")) if leader_member else None
        )

        existing = _existing_center(centers, department, parent_didi_id)
        didi_id = str(existing.get("id")) if existing else None
        action = "create"
        clear_leader = False
        if existing:
            existing_leader = str(
                existing.get("leader_id") or ""
            ).split(",")[0].strip()
            leader_changed = False
            if leader_state == "matched":
                leader_changed = existing_leader != leader_member_id
            elif leader_state == "none" and existing_leader:
                leader_changed = True
                clear_leader = True
            parent_changed = bool(
                parent_didi_id
                and str(existing.get("parent_id") or "0") != parent_didi_id
            )
            name_changed = str(existing.get("name") or "") != (
                department.name or ""
            )
            action = (
                "update"
                if (leader_changed or parent_changed or name_changed)
                else "unchanged"
            )

        item = DepartmentPlanItem(
            feishu=department,
            action=action,
            didi_id=didi_id,
            parent_feishu_id=parent_feishu_id,
            parent_didi_id=parent_didi_id,
            leader_state=leader_state,
            leader_member_id=leader_member_id,
            leader_member_name=(
                str(leader_member.get("realname"))
                if leader_member
                else None
            ),
            clear_leader=clear_leader,
        )
        items_by_feishu_id[department.open_department_id] = item
        id_map[department.open_department_id] = didi_id
        queue.extend(children.get(department.open_department_id, []))

    moves: list[MemberMove] = []
    unmatched_users: list[User] = []
    users_without_primary: list[User] = []
    for user in feishu.users:
        department_id = user.primary_department_id
        if not department_id:
            users_without_primary.append(user)
            continue
        target = items_by_feishu_id.get(department_id)
        if target is None:
            continue
        member = member_index.find(user)
        if member is None:
            unmatched_users.append(user)
            continue
        current_center_id = str(member.get("budget_center_id") or "")
        if target.didi_id and current_center_id == target.didi_id:
            continue
        moves.append(
            MemberMove(
                user=user,
                member=member,
                from_center_id=current_center_id,
                to_department=target.feishu,
                to_center_id=target.didi_id,
            )
        )

    multi_department_users = tuple(
        user
        for user in feishu.users
        if len(
            {
                dept_id
                for dept_id in user.department_ids
                if dept_id not in ("0", "")
            }
        )
        > 1
    )

    return DepartmentSyncPlan(
        root_center=root,
        departments=tuple(items_by_feishu_id.values()),
        moves=tuple(moves),
        unmatched_users=tuple(unmatched_users),
        leaders_not_found=tuple(leaders_not_found),
        departments_without_leader=tuple(departments_without_leader),
        orphaned_departments=_orphaned_departments(feishu, centers),
        users_without_primary=tuple(users_without_primary),
        multi_department_users=multi_department_users,
    )
