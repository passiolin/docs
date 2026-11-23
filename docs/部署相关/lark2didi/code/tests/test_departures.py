from __future__ import annotations

from pathlib import Path

from lark2didi.departures import (
    detect_departures,
    guard_limit,
    load_state,
    save_state,
)
from lark2didi.models import Department, OrgSnapshot, User


def _user(
    open_id: str,
    name: str,
    phone: str,
    *,
    employee_no: str | None = None,
) -> User:
    return User(
        open_id=open_id,
        user_id=None,
        name=name,
        en_name=None,
        email=None,
        mobile=phone,
        employee_no=employee_no,
        department_ids=("d1",),
    )


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


def _member(
    member_id: str,
    name: str,
    phone: str,
    *,
    employee_number: str | None = None,
    set_dismiss_time: str | None = None,
) -> dict:
    return {
        "id": member_id,
        "realname": name,
        "phone": phone,
        "employee_number": employee_number,
        "status": "1",
        "set_dismiss_time": set_dismiss_time,
    }


def _state(**entries: dict) -> dict:
    return {"version": 1, "members": entries}


def test_baseline_marks_seen_without_candidates() -> None:
    feishu = _snapshot([_user("u1", "张三", "13800000001")])
    members = [_member("m1", "张三", "13800000001")]
    state = {"version": 1, "members": {}}

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )

    assert candidates == []
    assert state["members"]["m1"]["seen_in_feishu"] is True
    assert state["members"]["m1"]["missing_runs"] == 0


def test_absence_requires_grace_runs() -> None:
    feishu = _snapshot([])
    members = [_member("m1", "张三", "13800000001")]
    state = _state(
        m1={"seen_in_feishu": True, "missing_runs": 0}
    )

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=2
    )
    assert candidates == []
    assert state["members"]["m1"]["missing_runs"] == 1

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=2
    )
    assert len(candidates) == 1
    assert candidates[0].member["id"] == "m1"
    assert candidates[0].missing_runs == 2


def test_reappear_resets_missing_runs() -> None:
    feishu = _snapshot([_user("u1", "张三", "13800000001")])
    members = [_member("m1", "张三", "13800000001")]
    state = _state(
        m1={"seen_in_feishu": True, "missing_runs": 2}
    )

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )

    assert candidates == []
    assert state["members"]["m1"]["missing_runs"] == 0


def test_legacy_member_never_auto_disabled() -> None:
    feishu = _snapshot([])
    members = [_member("m1", "历史员工", "13800000001")]
    state = {"version": 1, "members": {}}

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )
    assert candidates == []
    assert state["members"]["m1"]["seen_in_feishu"] is False

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )
    assert candidates == []
    assert state["members"]["m1"]["missing_runs"] == 0


def test_name_match_protects_employee() -> None:
    feishu = _snapshot([_user("u1", "赵小刚Dev", "13400000001")])
    members = [_member("m1", "赵小刚", "13400000002")]
    state = _state(
        m1={"seen_in_feishu": True, "missing_runs": 2}
    )

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )

    assert candidates == []
    assert state["members"]["m1"]["missing_runs"] == 0


def test_already_scheduled_dismissal_is_skipped() -> None:
    feishu = _snapshot([])
    members = [
        _member(
            "m1",
            "张三",
            "13800000001",
            set_dismiss_time="2026-11-07 10:00:00",
        )
    ]
    state = _state(
        m1={"seen_in_feishu": True, "missing_runs": 2}
    )

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )

    assert candidates == []
    assert state["members"]["m1"]["missing_runs"] == 2


def test_disabled_member_is_skipped() -> None:
    feishu = _snapshot([])
    members = [_member("m1", "张三", "13800000001")]
    state = _state(
        m1={
            "seen_in_feishu": True,
            "missing_runs": 2,
            "disabled_at": "2026-11-06T16:00:00",
        }
    )

    candidates, state = detect_departures(
        feishu, members, state, grace_runs=1
    )

    assert candidates == []


def test_guard_limit() -> None:
    assert guard_limit(52) == 10
    assert guard_limit(100) == 20
    assert guard_limit(5, max_count=3) == 3


def test_save_and_load_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "departures.json"
    state = {"version": 1, "members": {"m1": {"missing_runs": 1}}}

    save_state(state, path)
    loaded = load_state(path)

    assert loaded["members"]["m1"]["missing_runs"] == 1
    assert path.stat().st_mode & 0o777 == 0o600
