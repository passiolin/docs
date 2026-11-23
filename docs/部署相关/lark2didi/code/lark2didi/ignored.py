from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .plan import normalize_phone

STATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "state"
    / "ignored_creates.json"
)
_TZ = ZoneInfo("Asia/Shanghai")


class IgnoredStateError(RuntimeError):
    """自动忽略状态文件损坏或格式不正确。"""


def load_ignored(path: Path | str = STATE_PATH) -> dict:
    state_path = Path(path)
    if not state_path.is_file():
        return {"version": 1, "ignored": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IgnoredStateError(f"读取 {state_path} 失败: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(
        data.get("ignored"), dict
    ):
        raise IgnoredStateError(
            f"{state_path} 格式不正确，需要 {{\"ignored\": {{...}}}}"
        )
    return data


def save_ignored(state: dict, path: Path | str = STATE_PATH) -> None:
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


def add_ignored(
    state: dict,
    *,
    phone: str,
    name: str,
    reason: str,
) -> None:
    normalized = normalize_phone(phone)
    if not normalized:
        raise ValueError("手机号为空，无法加入自动忽略")
    ignored = state.setdefault("ignored", {})
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    entry = ignored.get(normalized) or {}
    entry["name"] = name
    entry["reason"] = reason
    entry["last_seen"] = now
    entry.setdefault("first_seen", now)
    ignored[normalized] = entry


def ignored_reasons(state: dict) -> dict[str, str]:
    return {
        phone: f"自动忽略：{entry.get('reason') or '员工已存在'}"
        for phone, entry in (state.get("ignored") or {}).items()
    }
