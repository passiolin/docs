from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .config import ConfigError, DidiConfig, FeishuConfig
from .didi import DidiClient, DidiError
from .didi_write import DidiMemberExistsError, DidiMemberWriter
from .feishu import FeishuClient, FeishuError
from .ignored import (
    IgnoredStateError,
    add_ignored,
    ignored_reasons,
    load_ignored,
    save_ignored,
)
from .notify import send_card_safe
from .plan import build_plan, mask_phone, normalize_phone

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _write_audit(handle, **record) -> None:
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="飞书 → 滴滴新增员工（默认 dry-run）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正创建员工；不加该参数只输出 dry-run 清单",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="创建间隔秒数，最低 0.15（滴滴要求 >=150ms）",
    )
    args = parser.parse_args()
    interval = max(args.interval, 0.15)

    try:
        feishu_config = FeishuConfig.load()
        didi_config = DidiConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}")
        return 1

    try:
        ignored_state = load_ignored()
    except IgnoredStateError as exc:
        print(f"自动忽略状态文件错误: {exc}")
        return 1

    try:
        with FeishuClient(feishu_config) as feishu:
            feishu_snapshot = feishu.snapshot()
        with DidiClient(didi_config) as didi:
            didi.authorize()
            centers, members = didi.snapshot()
    except (FeishuError, DidiError) as exc:
        print(f"拉取失败: {exc}")
        return 2

    plan = build_plan(
        feishu_snapshot,
        centers,
        members,
        ignored_reasons=ignored_reasons(ignored_state),
    )

    print(f"模式: {'APPLY（会新增员工）' if args.apply else 'DRY-RUN（不写入）'}")
    print(
        f"飞书 {len(feishu_snapshot.users)} 人 | 滴滴 {len(members)} 人 "
        f"| 已匹配 {len(plan.matched)}"
    )
    print(f"待新增: {len(plan.create)} 人 | 按规则跳过: {len(plan.skipped)} 人")

    if plan.skipped:
        print("跳过名单:")
        for item in plan.skipped:
            print(f"  - {item.user.name} | 原因={'/'.join(item.reasons)}")

    if plan.create:
        print("新增名单:")
        for item in plan.create:
            print(
                f"  - {item.user.name} | 手机 {mask_phone(item.phone)} "
                f"| 工号={item.employee_number or '-'} "
                f"| 邮箱={'有' if item.email else '无'}"
            )
    else:
        print("没有需要新增的员工。")

    if not args.apply:
        print("未执行任何写操作；确认无误后加 --apply。")
        return 0

    if not plan.create:
        return 0

    # 写入前重新拉一次滴滴，避免和人工操作并发导致重复创建。
    try:
        with DidiClient(didi_config) as didi:
            didi.authorize()
            _, current_members = didi.snapshot()
    except DidiError as exc:
        print(f"写入前刷新滴滴数据失败: {exc}")
        return 2

    existing_phones = {
        normalize_phone(member.get("phone"))
        for member in current_members
        if normalize_phone(member.get("phone"))
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"apply-{datetime.now():%Y%m%d}.jsonl"
    created = 0
    skipped = 0
    failed = 0
    created_notifications: list[str] = []
    failure_notifications: list[str] = []

    with log_path.open("a", encoding="utf-8") as audit:
        log_path.chmod(0o600)
        with DidiMemberWriter(didi_config) as creator:
            for item in plan.create:
                if item.phone in existing_phones:
                    skipped += 1
                    print(f"跳过（滴滴已存在）: {item.user.name}")
                    _write_audit(
                        audit,
                        action="create_member",
                        status="skipped_exists",
                        name=item.user.name,
                        employee_number=item.employee_number,
                        phone=mask_phone(item.phone),
                    )
                    continue

                try:
                    result = creator.create_member(
                        realname=item.user.name,
                        phone=item.phone,
                        employee_number=item.employee_number,
                        email=item.email,
                    )
                except DidiMemberExistsError as exc:
                    skipped += 1
                    conflict_status = (
                        exc.data.get("status")
                        if isinstance(exc.data, dict)
                        else None
                    )
                    hint = ""
                    if isinstance(exc.data, dict) and exc.data.get("url"):
                        hint = f" | 引导链接: {exc.data['url']}"
                    try:
                        add_ignored(
                            ignored_state,
                            phone=item.phone,
                            name=item.user.name,
                            reason=exc.errmsg,
                        )
                        save_ignored(ignored_state)
                    except (OSError, ValueError) as state_exc:
                        print(
                            f"警告: 自动忽略记录失败: {state_exc}"
                        )
                    print(
                        f"已自动忽略: {item.user.name} "
                        f"| {exc.errmsg}{hint}"
                        "（以后不再重试）"
                    )
                    _write_audit(
                        audit,
                        action="create_member",
                        status="ignored_exists",
                        name=item.user.name,
                        employee_number=item.employee_number,
                        phone=mask_phone(item.phone),
                        errno=exc.errno,
                        errmsg=exc.errmsg,
                        conflict_status=conflict_status,
                    )
                    continue
                except DidiError as exc:
                    failed += 1
                    failure_notifications.append(
                        f"{item.user.name}: {exc.errmsg}"
                    )
                    print(f"失败: {item.user.name} -> {exc}")
                    _write_audit(
                        audit,
                        action="create_member",
                        status="failed",
                        name=item.user.name,
                        employee_number=item.employee_number,
                        phone=mask_phone(item.phone),
                        errno=exc.errno,
                        errmsg=exc.errmsg,
                        request_id=exc.request_id,
                    )
                    continue

                created += 1
                existing_phones.add(item.phone)
                created_notifications.append(
                    f"{item.user.name}（{item.employee_number or '无工号'}"
                    f"，{'/'.join(item.department_names)}）"
                )
                member_data = result.get("data") or {}
                member_id = (
                    member_data.get("id")
                    if isinstance(member_data, dict)
                    else None
                )
                print(f"已创建: {item.user.name} | id={member_id or '-'}")
                _write_audit(
                    audit,
                    action="create_member",
                    status="created",
                    name=item.user.name,
                    employee_number=item.employee_number,
                    phone=mask_phone(item.phone),
                    didi_member_id=member_id,
                )
                time.sleep(interval)

    print()
    print(
        f"完成: 创建 {created} | 跳过 {skipped} | 失败 {failed} "
        f"| 审计日志 {log_path}"
    )
    if created_notifications:
        send_card_safe(
            f"lark2didi：新增员工 {created} 人",
            created_notifications,
            template="green",
            note=f"跳过 {skipped} | 失败 {failed}",
        )
    if failure_notifications:
        send_card_safe(
            "lark2didi：新增员工失败",
            failure_notifications,
            template="red",
            note=f"失败 {failed} 人，详见 logs/apply 日志",
        )
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
