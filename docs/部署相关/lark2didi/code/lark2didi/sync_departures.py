from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import ConfigError, DidiConfig, FeishuConfig
from .departures import (
    STATE_PATH,
    DepartureStateError,
    detect_departures,
    guard_limit,
    load_state,
    save_state,
)
from .didi import DidiClient, DidiError
from .didi_write import (
    DidiMemberNotEditableError,
    DidiMemberWriter,
    default_dismiss_time,
)
from .feishu import FeishuClient, FeishuError
from .notify import send_card_safe
from .plan import mask_phone

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _write_audit(handle, **record) -> None:
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="飞书 → 滴滴离职停用（默认 dry-run，只设置离职时间，不删除）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正提交离职时间；不加该参数只输出候选清单",
    )
    parser.add_argument(
        "--grace-runs",
        type=int,
        default=3,
        help="连续缺席多少轮才停用，默认 3（小时任务约 3 小时）",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=10,
        help="单轮停用人数硬上限，默认 10",
    )
    parser.add_argument(
        "--max-ratio",
        type=float,
        default=0.2,
        help="单轮停用人数占在职人数比例上限，默认 0.2",
    )
    args = parser.parse_args()

    if args.grace_runs < 1:
        print("--grace-runs 必须 >= 1")
        return 1

    try:
        feishu_config = FeishuConfig.load()
        didi_config = DidiConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}")
        return 1

    try:
        state = load_state()
    except DepartureStateError as exc:
        print(f"状态文件错误: {exc}")
        return 1

    is_new_state = not STATE_PATH.is_file()

    try:
        with FeishuClient(feishu_config) as feishu:
            feishu_snapshot = feishu.snapshot()
        with DidiClient(didi_config) as didi:
            didi.authorize()
            centers, members = didi.snapshot()
    except (FeishuError, DidiError) as exc:
        print(f"拉取失败: {exc}")
        return 2

    active = [
        member
        for member in members
        if str(member.get("status") or "") != "4"
    ]

    candidates, state = detect_departures(
        feishu_snapshot,
        members,
        state,
        grace_runs=args.grace_runs,
    )
    save_state(state)

    limit = guard_limit(
        len(active),
        max_count=args.max_count,
        max_ratio=args.max_ratio,
    )

    print(
        f"模式: {'APPLY（提交离职时间）' if args.apply else 'DRY-RUN（不写入）'}"
    )
    print(
        f"飞书 {len(feishu_snapshot.users)} 人 | "
        f"滴滴 {len(members)} 人（在职 {len(active)}）"
    )
    print(f"连续缺席阈值: {args.grace_runs} 轮 | 单轮停用上限: {limit} 人")

    if is_new_state:
        print("首次运行: 已建立基线，不会停用任何人。")

    print(f"待停用: {len(candidates)} 人")
    for candidate in candidates:
        member = candidate.member
        print(
            f"  - {member.get('realname')} "
            f"| 手机={mask_phone(member.get('phone'))} "
            f"| 滴滴ID={member.get('id')} "
            f"| 连续缺席={candidate.missing_runs} 轮"
        )

    if not candidates:
        print("没有需要停用的人员。")
        return 0

    if len(candidates) > limit:
        send_card_safe(
            "lark2didi：离职停用未执行",
            [
                f"候选 {len(candidates)} 人超过单轮上限 {limit} 人，"
                "请人工核对飞书通讯录和权限范围。"
            ],
            template="orange",
        )
        print(
            f"中止: 候选 {len(candidates)} 人超过单轮上限 {limit} 人。"
            "请人工核对飞书通讯录和权限范围后再执行。"
        )
        return 3

    if not args.apply:
        print("未提交任何离职时间；确认后加 --apply。")
        return 0

    dismiss_time = default_dismiss_time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"departures-{datetime.now():%Y%m%d}.jsonl"
    done = 0
    already_left = 0
    failed = 0
    submitted_notifications: list[str] = []
    already_left_notifications: list[str] = []
    failure_notifications: list[str] = []

    with log_path.open("a", encoding="utf-8") as audit:
        log_path.chmod(0o600)
        with DidiMemberWriter(didi_config) as writer:
            for candidate in candidates:
                member = candidate.member
                member_id = str(member.get("id") or "")
                entry = state["members"].get(member_id, {})
                try:
                    writer.disable_member(
                        member_id=member_id,
                        dismiss_time=dismiss_time,
                    )
                except DidiMemberNotEditableError as exc:
                    already_left += 1
                    already_left_notifications.append(
                        str(member.get("realname") or member_id)
                    )
                    entry["disabled_at"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
                    entry["missing_runs"] = 0
                    save_state(state)
                    print(
                        f"已是非正常状态: {member.get('realname')} "
                        f"| {exc.errmsg}"
                    )
                    _write_audit(
                        audit,
                        action="disable_member",
                        status="already_left",
                        member_id=member_id,
                        name=member.get("realname"),
                        phone=mask_phone(member.get("phone")),
                        missing_runs=candidate.missing_runs,
                        errno=exc.errno,
                        errmsg=exc.errmsg,
                    )
                    continue
                except DidiError as exc:
                    failed += 1
                    failure_notifications.append(
                        f"{member.get('realname')}: {exc.errmsg}"
                    )
                    print(
                        f"失败: {member.get('realname')} -> {exc}"
                    )
                    _write_audit(
                        audit,
                        action="disable_member",
                        status="failed",
                        member_id=member_id,
                        name=member.get("realname"),
                        phone=mask_phone(member.get("phone")),
                        missing_runs=candidate.missing_runs,
                        errno=exc.errno,
                        errmsg=exc.errmsg,
                        request_id=exc.request_id,
                    )
                    continue

                done += 1
                submitted_notifications.append(
                    str(member.get("realname") or member_id)
                )
                entry["disabled_at"] = datetime.now().isoformat(
                    timespec="seconds"
                )
                entry["missing_runs"] = 0
                save_state(state)
                print(
                    f"已提交离职时间: {member.get('realname')} "
                    f"| {dismiss_time}"
                )
                _write_audit(
                    audit,
                    action="disable_member",
                    status="submitted",
                    member_id=member_id,
                    name=member.get("realname"),
                    phone=mask_phone(member.get("phone")),
                    missing_runs=candidate.missing_runs,
                    dismiss_time=dismiss_time,
                )
                time.sleep(0.25)

    print()
    print(
        f"完成: 提交 {done} | 已是离职 {already_left} | 失败 {failed} "
        f"| 审计日志 {log_path}"
    )
    if submitted_notifications or already_left_notifications:
        lines: list[str] = []
        if submitted_notifications:
            lines.append(
                f"提交离职 {len(submitted_notifications)} 人："
                + "、".join(submitted_notifications)
            )
        if already_left_notifications:
            lines.append(
                f"已是离职状态 {len(already_left_notifications)} 人："
                + "、".join(already_left_notifications)
            )
        send_card_safe(
            f"lark2didi：离职停用 {done + already_left} 人",
            lines,
            template="orange",
            note=f"离职时间 {dismiss_time} | 失败 {failed}",
        )
    if failure_notifications:
        send_card_safe(
            "lark2didi：离职停用失败",
            failure_notifications,
            template="red",
        )
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
