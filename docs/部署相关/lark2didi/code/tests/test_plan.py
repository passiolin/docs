from __future__ import annotations

from lark2didi.models import Department, OrgSnapshot, User
from lark2didi.plan import build_plan, normalize_name, normalize_phone


def _user(
    open_id: str,
    name: str,
    phone: str,
    *,
    employee_no: str | None = None,
    email: str | None = None,
    department_ids: tuple[str, ...] = ("d1",),
) -> User:
    return User(
        open_id=open_id,
        user_id=None,
        name=name,
        en_name=None,
        email=email,
        mobile=phone,
        employee_no=employee_no,
        department_ids=department_ids,
    )


def _member(
    member_id: str,
    name: str,
    phone: str,
    *,
    status: str = "1",
    dismiss_time: str | None = None,
) -> dict:
    return {
        "id": member_id,
        "realname": name,
        "phone": phone,
        "status": status,
        "dismiss_time": dismiss_time,
    }


def _snapshot(users: list[User]) -> OrgSnapshot:
    return OrgSnapshot(
        departments=(
            Department(
                open_department_id="d1",
                parent_department_id="0",
                name="研发部",
                member_count=len(users),
                leader_user_id=None,
                order=None,
            ),
        ),
        users=tuple(users),
    )


def test_normalize_phone() -> None:
    assert normalize_phone("+86 138-0000-0000") == "13800000000"
    assert normalize_phone("8613800000000") == "13800000000"
    assert normalize_phone("008613800000000") == "13800000000"
    assert normalize_phone("-") == ""


def test_normalize_name() -> None:
    assert normalize_name("赵小刚Dev") == "赵小刚"
    assert normalize_name("王小明Dev") == "王小明"
    assert normalize_name("Ryan") == ""


def test_build_plan_matches_creates_and_flags() -> None:
    feishu = _snapshot(
        [
            _user("u1", "张三A", "13800000001"),
            _user(
                "u2",
                "李四",
                "13800000002",
                employee_no="L002",
                email="lisi@example.com",
            ),
            _user("u3", "王五", "13800000003"),
        ]
    )
    didi = [
        _member("m1", "张三", "13800000001"),
        _member("m3", "王五", "13900000003"),
        _member(
            "m4",
            "赵六",
            "13800000004",
            status="4",
            dismiss_time="2026-10-01 10:00:00",
        ),
    ]

    plan = build_plan(feishu, [], didi)

    assert len(plan.matched) == 1
    assert plan.matched[0].user.open_id == "u1"
    assert plan.matched[0].member["id"] == "m1"

    assert len(plan.create) == 1
    created = plan.create[0]
    assert created.user.open_id == "u2"
    assert created.phone == "13800000002"
    assert created.employee_number == "L002"
    assert created.email == "lisi@example.com"
    assert created.department_names == ("研发部",)

    assert len(plan.phone_conflicts) == 1
    assert plan.phone_conflicts[0].user.open_id == "u3"
    assert plan.phone_conflicts[0].didi_phone == "13900000003"

    assert len(plan.didi_only_active) == 0
    assert len(plan.didi_resigned) == 1
    assert plan.didi_resigned[0]["id"] == "m4"


def test_build_plan_flags_active_feishu_but_resigned_didi() -> None:
    feishu = _snapshot([_user("u1", "张三", "13800000001")])
    didi = [
        _member(
            "m1",
            "张三",
            "13800000001",
            status="4",
            dismiss_time="2026-10-01 10:00:00",
        )
    ]

    plan = build_plan(feishu, [], didi)

    assert len(plan.matched) == 0
    assert len(plan.create) == 0
    assert len(plan.resigned_conflicts) == 1
    assert plan.resigned_conflicts[0].member["id"] == "m1"


def test_build_plan_skips_duplicate_phone_groups() -> None:
    feishu = _snapshot(
        [
            _user("u1", "张三", "13800000001"),
            _user("u2", "张三丰", "13800000001"),
        ]
    )
    didi = [_member("m1", "张三", "13800000001")]

    plan = build_plan(feishu, [], didi)

    assert len(plan.matched) == 0
    assert len(plan.create) == 0
    assert len(plan.ambiguous) == 1
    assert len(plan.didi_only_active) == 0


def test_build_plan_reports_root_center() -> None:
    feishu = _snapshot([_user("u1", "张三", "13800000001")])
    centers = [
        {
            "id": "bc-root",
            "name": "公司",
            "type": 1,
            "parent_id": "0",
        },
        {
            "id": "bc-child",
            "name": "研发部",
            "type": 1,
            "parent_id": "bc-root",
        },
    ]

    plan = build_plan(feishu, centers, [])

    assert plan.root_center is not None
    assert plan.root_center["id"] == "bc-root"


def test_build_plan_skips_outsourced_and_missing_employee_no() -> None:
    feishu = OrgSnapshot(
        departments=(
            Department(
                open_department_id="d1",
                parent_department_id="0",
                name="研发部",
                member_count=0,
                leader_user_id=None,
                order=None,
            ),
            Department(
                open_department_id="d2",
                parent_department_id="0",
                name="财务部（外包）",
                member_count=0,
                leader_user_id=None,
                order=None,
            ),
        ),
        users=(
            _user(
                "u1",
                "外包甲",
                "13800000001",
                employee_no="L001",
                department_ids=("d2",),
            ),
            _user(
                "u2",
                "无工号",
                "13800000002",
                employee_no=None,
                department_ids=("d1",),
            ),
            _user(
                "u3",
                "合格",
                "13800000003",
                employee_no="L003",
                department_ids=("d1",),
            ),
        ),
    )

    plan = build_plan(feishu, [], [])

    assert [item.user.open_id for item in plan.create] == ["u3"]
    assert {item.user.open_id for item in plan.skipped} == {"u1", "u2"}
    reasons = {item.user.open_id: item.reasons for item in plan.skipped}
    assert "部门含“外包”" in reasons["u1"]
    assert "缺工号" in reasons["u2"]


def test_build_plan_ignored_reason() -> None:
    feishu = _snapshot(
        [
            _user(
                "u1",
                "张三",
                "13800000001",
                employee_no="L001",
            )
        ]
    )

    plan = build_plan(
        feishu,
        [],
        [],
        ignored_reasons={
            "13800000001": "自动忽略：员工已存在于其他企业"
        },
    )

    assert len(plan.create) == 0
    assert len(plan.skipped) == 1
    assert plan.skipped[0].user.open_id == "u1"
    assert plan.skipped[0].reasons == (
        "自动忽略：员工已存在于其他企业",
    )
