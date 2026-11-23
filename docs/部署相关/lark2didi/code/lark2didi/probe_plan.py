from __future__ import annotations

import sys

from .config import ConfigError, DidiConfig, FeishuConfig
from .didi import DidiClient, DidiError
from .feishu import FeishuClient, FeishuError
from .ignored import (
    IgnoredStateError,
    ignored_reasons,
    load_ignored,
)
from .plan import build_plan, mask_phone


def main() -> int:
    try:
        feishu_config = FeishuConfig.load()
        didi_config = DidiConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1

    try:
        ignored_state = load_ignored()
    except IgnoredStateError as exc:
        print(f"自动忽略状态文件错误: {exc}", file=sys.stderr)
        return 1
    ignored = ignored_reasons(ignored_state)

    try:
        with FeishuClient(feishu_config) as feishu:
            feishu_snapshot = feishu.snapshot()
        with DidiClient(didi_config) as didi:
            didi.authorize()
            centers, members = didi.snapshot()
    except (FeishuError, DidiError) as exc:
        print(f"拉取失败: {exc}", file=sys.stderr)
        return 2

    plan = build_plan(
        feishu_snapshot,
        centers,
        members,
        ignored_reasons=ignored,
    )
    active = [
        member
        for member in members
        if str(member.get("status") or "") != "4"
    ]

    print("模式: 只读 dry-run（不会写入或删除任何数据）")
    print(
        f"飞书: {len(feishu_snapshot.users)} 人 | "
        f"滴滴: {len(members)} 人（在职 {len(active)} / 离职 "
        f"{len(members) - len(active)}）"
    )
    print(f"自动忽略（已存在/其他企业）: {len(ignored)} 人")
    print(f"手机号匹配: {len(plan.matched)} 人")
    print(f"飞书在职、滴滴已离职: {len(plan.resigned_conflicts)} 人")
    print(f"姓名匹配但手机不同: {len(plan.phone_conflicts)} 人")
    print(f"计划新增（飞书有、滴滴无）: {len(plan.create)} 人")
    print(f"按规则跳过（不同步）: {len(plan.skipped)} 人")
    print(f"滴滴独有在职（只提示，不删除）: {len(plan.didi_only_active)} 人")
    print(f"滴滴独有已离职（只提示，不删除）: {len(plan.didi_resigned)} 人")
    print(f"飞书缺手机号: {len(plan.feishu_missing_phone)} 人")
    if plan.ambiguous:
        print(f"歧义项: {len(plan.ambiguous)} 个")

    if plan.root_center:
        print(
            f"新增人员默认挂靠: {plan.root_center.get('name')} "
            f"(budget_center_id={plan.root_center.get('id')})"
        )

    if plan.create:
        print()
        print("待新增人员（不会立即执行）:")
        for item in plan.create:
            print(
                f"  - {item.user.name} | 手机 {mask_phone(item.phone)} "
                f"| 工号={item.employee_number or '-'} "
                f"| 邮箱={'有' if item.email else '无'} "
                f"| 飞书部门={'/'.join(item.department_names)}"
            )

    if plan.phone_conflicts:
        print()
        print("手机号冲突（需人工确认，不自动建号）:")
        for item in plan.phone_conflicts:
            print(
                f"  - {item.user.name} | 飞书 {mask_phone(item.feishu_phone)} "
                f"vs 滴滴 {mask_phone(item.didi_phone)} "
                f"| 滴滴状态={item.member.get('status')}"
            )

    if plan.skipped:
        print()
        print("按规则跳过（不同步）:")
        for item in plan.skipped:
            print(
                f"  - {item.user.name} "
                f"| 原因={'/'.join(item.reasons)} "
                f"| 飞书部门={'/'.join(item.department_names)}"
            )

    if plan.didi_only_active:
        print()
        print("滴滴有、飞书无（在职，只提示，不删除）:")
        for member in plan.didi_only_active:
            print(
                f"  - {member.get('realname')} "
                f"| 手机={mask_phone(member.get('phone'))} "
                f"| 状态={member.get('status')}"
            )

    if plan.didi_resigned:
        print()
        print("滴滴有、飞书无（已离职，保持原样）:")
        for member in plan.didi_resigned:
            print(
                f"  - {member.get('realname')} "
                f"| 离职时间={member.get('dismiss_time') or '-'}"
            )

    if plan.resigned_conflicts:
        print()
        print("飞书仍在职、滴滴已离职（需人工确认）:")
        for item in plan.resigned_conflicts:
            print(
                f"  - {item.user.name} "
                f"| 滴滴离职时间={item.member.get('dismiss_time') or '-'}"
            )

    if plan.ambiguous:
        print()
        print("歧义项（跳过，不处理）:")
        for item in plan.ambiguous:
            print(f"  - {item}")

    print()
    print("结论: 本次未发送任何写/删请求。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
