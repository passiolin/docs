from __future__ import annotations

from lark2didi.departments import build_department_plan
from lark2didi.models import Department, OrgSnapshot, User


def _dept(
    dept_id: str,
    name: str,
    *,
    parent: str = "0",
    leader: str | None = None,
) -> Department:
    return Department(
        open_department_id=dept_id,
        parent_department_id=parent,
        name=name,
        member_count=0,
        leader_user_id=leader,
        order=None,
    )


def _user(
    open_id: str,
    name: str,
    phone: str,
    department_id: str,
    *,
    employee_no: str | None = None,
    department_ids: tuple[str, ...] | None = None,
) -> User:
    return User(
        open_id=open_id,
        user_id=None,
        name=name,
        en_name=None,
        email=None,
        mobile=phone,
        employee_no=employee_no,
        department_ids=department_ids or (department_id,),
        primary_department_id=department_id,
    )


def _member(
    member_id: str,
    name: str,
    phone: str,
    *,
    center_id: str = "root",
    employee_number: str | None = None,
) -> dict:
    return {
        "id": member_id,
        "realname": name,
        "phone": phone,
        "employee_number": employee_number,
        "status": "1",
        "budget_center_id": center_id,
    }


def _root() -> dict:
    return {
        "id": "root",
        "name": "公司",
        "type": 1,
        "parent_id": "0",
        "out_budget_id": "",
    }


def test_build_department_plan_creates_tree_and_moves() -> None:
    feishu = OrgSnapshot(
        departments=(
            _dept("d1", "研发部", leader="u1"),
            _dept("d2", "AI研发部", parent="d1", leader="u2"),
        ),
        users=(
            _user("u1", "张三", "13800000001", "d1"),
            _user("u2", "李四", "13900000001", "d2"),
        ),
    )
    members = [
        _member("m1", "张三", "13800000001"),
        _member("m2", "李四", "13900000001"),
    ]

    plan = build_department_plan(feishu, [_root()], members)

    assert [item.action for item in plan.departments] == [
        "create",
        "create",
    ]
    d1, d2 = plan.departments
    assert d1.parent_feishu_id is None
    assert d1.parent_didi_id == "root"
    assert d1.leader_member_id == "m1"
    assert d2.parent_feishu_id == "d1"
    assert d2.leader_member_id == "m2"
    assert len(plan.moves) == 2
    assert {move.user.open_id for move in plan.moves} == {"u1", "u2"}


def test_existing_department_and_member_are_unchanged() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "m1",
        },
    ]
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部", leader="u1"),),
        users=(_user("u1", "张三", "13800000001", "d1"),),
    )
    members = [
        _member("m1", "张三", "13800000001", center_id="bc1")
    ]

    plan = build_department_plan(feishu, centers, members)

    assert len(plan.departments) == 1
    assert plan.departments[0].action == "unchanged"
    assert plan.departments[0].didi_id == "bc1"
    assert plan.moves == ()


def test_leader_not_found_is_reported() -> None:
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部", leader="missing"),),
        users=(),
    )

    plan = build_department_plan(feishu, [_root()], [])

    assert len(plan.leaders_not_found) == 1
    assert plan.departments[0].leader_member_id is None


def test_unmatched_user_is_not_moved() -> None:
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部"),),
        users=(_user("u1", "张三", "13800000001", "d1"),),
    )

    plan = build_department_plan(feishu, [_root()], [])

    assert len(plan.moves) == 0
    assert len(plan.unmatched_users) == 1
    assert plan.unmatched_users[0].open_id == "u1"


def test_multi_department_user_uses_primary_department() -> None:
    feishu = OrgSnapshot(
        departments=(
            _dept("d1", "研发部"),
            _dept("d2", "测试部"),
        ),
        users=(
            _user(
                "u1",
                "张三",
                "13800000001",
                "d2",
                department_ids=("d1", "d2"),
            ),
        ),
    )
    members = [_member("m1", "张三", "13800000001")]

    plan = build_department_plan(feishu, [_root()], members)

    assert len(plan.moves) == 1
    assert plan.moves[0].to_department.open_department_id == "d2"
    assert len(plan.multi_department_users) == 1


def test_department_rename_and_parent_change_trigger_update() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "旧研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "",
        },
        {
            "id": "bc2",
            "name": "AI研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d2",
            "leader_id": "",
        },
    ]
    feishu = OrgSnapshot(
        departments=(
            _dept("d1", "研发部"),
            _dept("d2", "AI研发部", parent="d1"),
        ),
        users=(),
    )

    plan = build_department_plan(feishu, centers, [])

    by_id = {
        item.feishu.open_department_id: item for item in plan.departments
    }
    assert by_id["d1"].action == "update"
    assert by_id["d2"].action == "update"


def test_leader_change_triggers_update() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "m-old",
        },
    ]
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部", leader="u1"),),
        users=(_user("u1", "新主管", "13800000001", "d1"),),
    )
    members = [
        _member("m-old", "旧主管", "13900000001"),
        _member("m-new", "新主管", "13800000001"),
    ]

    plan = build_department_plan(feishu, centers, members)

    item = plan.departments[0]
    assert item.action == "update"
    assert item.leader_member_id == "m-new"
    assert item.leader_state == "matched"


def test_leader_removed_clears_didi_leader() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "m1",
        },
    ]
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部", leader=None),),
        users=(),
    )

    plan = build_department_plan(feishu, centers, [])

    item = plan.departments[0]
    assert item.action == "update"
    assert item.leader_state == "none"
    assert item.clear_leader is True
    assert len(plan.departments_without_leader) == 1


def test_unmatched_leader_keeps_existing() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "m1",
        },
    ]
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部", leader="ghost"),),
        users=(),
    )

    plan = build_department_plan(feishu, centers, [])

    item = plan.departments[0]
    assert item.action == "unchanged"
    assert item.leader_state == "unmatched"
    assert item.clear_leader is False
    assert len(plan.leaders_not_found) == 1


def test_orphaned_department_is_reported_not_deleted() -> None:
    centers = [
        _root(),
        {
            "id": "bc-old",
            "name": "旧部门",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "od-old",
            "leader_id": "",
            "member_num": 0,
        },
    ]
    feishu = OrgSnapshot(departments=(), users=())

    plan = build_department_plan(feishu, centers, [])

    assert len(plan.orphaned_departments) == 1
    assert plan.orphaned_departments[0]["id"] == "bc-old"
    assert plan.departments == ()


def test_member_moves_when_primary_department_changes() -> None:
    centers = [
        _root(),
        {
            "id": "bc1",
            "name": "研发部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d1",
            "leader_id": "",
        },
        {
            "id": "bc2",
            "name": "测试部",
            "type": 1,
            "parent_id": "root",
            "out_budget_id": "d2",
            "leader_id": "",
        },
    ]
    feishu = OrgSnapshot(
        departments=(
            _dept("d1", "研发部"),
            _dept("d2", "测试部"),
        ),
        users=(_user("u1", "张三", "13800000001", "d2"),),
    )
    members = [_member("m1", "张三", "13800000001", center_id="bc1")]

    plan = build_department_plan(feishu, centers, members)

    assert len(plan.moves) == 1
    assert plan.moves[0].from_center_id == "bc1"
    assert plan.moves[0].to_center_id == "bc2"


def test_user_without_primary_department_is_reported() -> None:
    feishu = OrgSnapshot(
        departments=(_dept("d1", "研发部"),),
        users=(
            User(
                open_id="u1",
                user_id=None,
                name="张三",
                en_name=None,
                email=None,
                mobile="13800000001",
                employee_no=None,
                department_ids=(),
                primary_department_id=None,
            ),
        ),
    )

    plan = build_department_plan(feishu, [_root()], [])

    assert len(plan.users_without_primary) == 1
    assert plan.moves == ()
