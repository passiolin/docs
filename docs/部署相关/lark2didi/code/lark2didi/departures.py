from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import OrgSnapshot
from .plan import normalize_name, normalize_phone

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "departures.json"
_TZ = ZoneInfo("Asia/Shanghai")


class DepartureStateError(RuntimeError):
    """离职状态文件损坏或格式不正确。"""


@dataclass(frozen=True, slots=True)
class DepartureCandidate:
    member: dict
    missing_runs: int


def load_state(path: Path | str = STATE_PATH) -> dict:
    state_path = Path(path)
    if not state_path.is_file():
        return {"version": 1, "members": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DepartureStateError(f"读取 {state_path} 失败: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(
        data.get("members"), dict
    ):
        raise DepartureStateError(
            f"{state_path} 格式不正确，需要 {{\"members\": {{...}}}}"
        )
    return data


def save_state(state: dict, path: Path | str = STATE_PATH) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(_TZ).isoformat(timespec="seconds")
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.chmod(0o600)
    tmp.replace(state_path)
    state_path.chmod(0o600)


def _feishu_index(feishu: OrgSnapshot) -> tuple[set[str], set[str], set[str]]:
    phones = {
        phone
        for user in feishu.users
        if (phone := normalize_phone(user.mobile))
    }
    employee_numbers = {
        number
        for user in feishu.users
        if (number := (user.employee_no or "").strip())
    }
    names = {
        name
        for user in feishu.users
        if (name := normalize_name(user.name))
    }
    return phones, employee_numbers, names


def _is_still_in_feishu(
    member: dict,
    fs_phones: set[str],
    fs_employee_numbers: set[str],
    fs_names: set[str],
) -> bool:
    phone = normalize_phone(member.get("phone"))
    if phone and phone in fs_phones:
        return True
    employee_number = str(member.get("employee_number") or "").strip()
    if employee_number and employee_number in fs_employee_numbers:
        return True
    name = normalize_name(member.get("realname"))
    # 姓名匹配只用于“保护”，宁可晚停用，也不要把同名在职员工误停。
    return bool(name and name in fs_names)


def detect_departures(
    feishu: OrgSnapshot,
    didi_members: list[dict],
    state: dict,
    *,
    grace_runs: int = 3,
) -> tuple[list[DepartureCandidate], dict]:
    if grace_runs < 1:
        raise ValueError("grace_runs 必须 >= 1")

    fs_phones, fs_employee_numbers, fs_names = _feishu_index(feishu)
    members_state = state.setdefault("members", {})
    candidates: list[DepartureCandidate] = []

    for member in didi_members:
        member_id = str(member.get("id") or "")
        if not member_id:
            continue
        if str(member.get("status") or "") == "4":
            continue
        # 已经设置过离职日期，等滴滴定时任务处理，不重复提交。
        if str(member.get("set_dismiss_time") or "").strip():
            continue

        seen = _is_still_in_feishu(
            member, fs_phones, fs_employee_numbers, fs_names
        )
        entry = members_state.get(member_id)
        if entry is None:
            members_state[member_id] = {
                "seen_in_feishu": seen,
                "missing_runs": 0,
            }
            continue

        if seen:
            entry["seen_in_feishu"] = True
            entry["missing_runs"] = 0
            entry.pop("disabled_at", None)
            continue

        if entry.get("disabled_at"):
            continue

        if not entry.get("seen_in_feishu"):
            # 首次建立基线时就不在飞书里的历史人员，不自动停用。
            entry["missing_runs"] = 0
            continue

        missing_runs = int(entry.get("missing_runs") or 0) + 1
        entry["missing_runs"] = missing_runs
        if missing_runs >= grace_runs:
            candidates.append(
                DepartureCandidate(
                    member=member, missing_runs=missing_runs
                )
            )

    return candidates, state


def guard_limit(
    active_count: int, *, max_count: int = 10, max_ratio: float = 0.2
) -> int:
    return max(int(max_count), int(active_count * max_ratio))
