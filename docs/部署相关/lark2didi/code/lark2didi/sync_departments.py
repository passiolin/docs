from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .config import ConfigError, DidiConfig, FeishuConfig
from .departments import build_department_plan, DepartmentSyncPlan
from .didi import DidiClient, DidiError
from .didi_write import DidiBudgetCenterWriter
from .feishu import FeishuClient, FeishuError
from .notify import send_card_safe

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _write_audit(handle, **record) -> None:
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def _print_plan(plan: DepartmentSyncPlan, *, move_members: bool) -> None:
    counts = {"create": 0, "update": 0, "unchanged": 0}
    for item in plan.departments:
        counts[item.action] = counts.get(item.action, 0) + 1
    department_names = {
        item.feishu.open_department_id: item.feishu.name
        for item in plan.departments
    }
    print(
        f"部门: {len(plan.departments)} 个 "
        f"(创建 {counts['create']} / 更新 {counts['update']} "
        f"/ 不变 {counts['unchanged']})"
    )
    for item in plan.departments:
        parent = (
            "公司根"
            if item.parent_feishu_id is None
            else department_names.get(
                item.parent_feishu_id, item.parent_feishu_id
            )
        )
        if item.leader_state == "matched":
            leader = item.leader_member_name or item.leader_member_id
        elif item.leader_state == "none":
            leader = "飞书未设置"
        else:
            leader = "飞书有、滴滴未匹配"
        print(
            f"  - [{item.action}] {item.feishu.name} "
            f"| parent={parent} "
            f"| 主管={leader} "
            f"| 滴滴ID={item.didi_id or '待创建'}"
        )

    print()
    print(f"员工部门归属变更: {len(plan.moves)} 人")
    for move in plan.moves:
        target = move.to_department.name or move.to_department.open_department_id
        print(
            f"  - {move.user.name}: "
            f"{move.from_center_id or '空'} -> {target}"
        )
    if not move_members:
        print("注意: 未加 --move-members 时不会移动员工。")

    if plan.unmatched_users:
        print()
        print(f"飞书有、滴滴无（不会移动）: {len(plan.unmatched_users)} 人")
        print("  - " + "、".join(user.name for user in plan.unmatched_users))

    if plan.leaders_not_found:
        print()
        print(f"主管未匹配到滴滴成员: {len(plan.leaders_not_found)} 个部门")
        print(
            "  - "
            + "、".join(
                department.name or department.open_department_id
                for department in plan.leaders_not_found
            )
        )
        print("  处理: 保留滴滴现有主管，待负责人进入滴滴后下一轮自动更新。")

    if plan.departments_without_leader:
        print()
        print(f"飞书未设置负责人: {len(plan.departments_without_leader)} 个部门")
        print(
            "  - "
            + "、".join(
                department.name or department.open_department_id
                for department in plan.departments_without_leader
            )
        )
        print("  处理: 清空滴滴侧对应部门的主管。")

    if plan.orphaned_departments:
        print()
        print(
            f"飞书已删除、滴滴保留（不删除）: "
            f"{len(plan.orphaned_departments)} 个部门"
        )
        for center in plan.orphaned_departments:
            print(
                f"  - {center.get('name')} "
                f"| id={center.get('id')} "
                f"| 人数={center.get('member_num')}"
            )

    if plan.users_without_primary:
        print()
        print(f"飞书无主部门（不会移动）: {len(plan.users_without_primary)} 人")
        print(
            "  - "
            + "、".join(user.name for user in plan.users_without_primary)
        )

    if plan.multi_department_users:
        print()
        print("多部门员工（按主部门归属）:")
        for user in plan.multi_department_users:
            print(
                f"  - {user.name} | 主部门={user.primary_department_id}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="飞书部门 → 滴滴成本中心（默认 dry-run）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正创建/修改部门；不加则只输出计划",
    )
    parser.add_argument(
        "--move-members",
        action="store_true",
        help="同时把员工主部门改成飞书主部门（需配合 --apply）",
    )
    parser.add_argument(
        "--allow-mass",
        action="store_true",
        help="员工移动人数超过上限时仍允许执行",
    )
    parser.add_argument(
        "--max-moves",
        type=int,
        default=20,
        help="单轮员工移动人数硬上限，默认 20",
    )
    parser.add_argument(
        "--max-ratio",
        type=float,
        default=0.3,
        help="单轮员工移动人数占在职人数比例上限，默认 0.3",
    )
    args = parser.parse_args()

    try:
        feishu_config = FeishuConfig.load()
        didi_config = DidiConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}")
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

    plan = build_department_plan(feishu_snapshot, centers, members)
    active_count = sum(
        1 for member in members if str(member.get("status") or "") != "4"
    )
    move_limit = max(args.max_moves, int(active_count * args.max_ratio))

    print(f"模式: {'APPLY' if args.apply else 'DRY-RUN（不写入）'}")
    if plan.root_center:
        print(
            f"公司根成本中心: {plan.root_center.get('name')} "
            f"(id={plan.root_center.get('id')})"
        )
    _print_plan(plan, move_members=args.move_members)
    print()
    print(f"员工移动上限: {move_limit} 人/轮")

    if not args.apply:
        print("未执行任何写操作。")
        return 0

    move_allowed = True
    if args.move_members and len(plan.moves) > move_limit and not args.allow_mass:
        move_allowed = False
        print(
            f"警告: 需要移动 {len(plan.moves)} 人，超过上限 {move_limit}。"
            "本轮只同步部门和主管，不移动员工。"
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    dept_log = LOG_DIR / f"departments-{stamp:%Y%m%d}.jsonl"
    move_log = LOG_DIR / f"member-moves-{stamp:%Y%m%d}.jsonl"

    id_map: dict[str, str] = {}
    root_id = (
        str(plan.root_center.get("id")) if plan.root_center else None
    )
    if not root_id:
        print("未找到公司根成本中心，无法创建部门。")
        return 2

    created = 0
    updated = 0
    failed = 0
    created_departments: list[str] = []
    updated_departments: list[str] = []
    dept_failure_notifications: list[str] = []

    with dept_log.open("a", encoding="utf-8") as audit:
        dept_log.chmod(0o600)
        with DidiBudgetCenterWriter(didi_config) as writer:
            for item in plan.departments:
                department = item.feishu
                if item.parent_feishu_id is None:
                    parent_id = root_id
                else:
                    parent_id = id_map.get(item.parent_feishu_id)
                if not parent_id:
                    print(
                        f"失败: {department.name} 的上级部门尚未创建"
                    )
                    failed += 1
                    dept_failure_notifications.append(
                        f"{department.name}: 上级部门尚未创建"
                    )
                    continue

                if item.action == "create":
                    try:
                        result = writer.create_department(
                            name=department.name or "",
                            out_budget_id=department.open_department_id,
                            parent_id=parent_id,
                            leader_id=item.leader_member_id,
                        )
                    except DidiError as exc:
                        failed += 1
                        dept_failure_notifications.append(
                            f"{department.name}: {exc.errmsg}"
                        )
                        print(f"失败: 创建 {department.name} -> {exc}")
                        _write_audit(
                            audit,
                            action="create_department",
                            status="failed",
                            name=department.name,
                            out_budget_id=department.open_department_id,
                            parent_id=parent_id,
                            leader_id=item.leader_member_id,
                            errno=exc.errno,
                            errmsg=exc.errmsg,
                            request_id=exc.request_id,
                        )
                        # 父部门失败后，子部门也无法继续创建。
                        break

                    data = result.get("data") or {}
                    new_id = str(data.get("id") or "")
                    if not new_id:
                        failed += 1
                        dept_failure_notifications.append(
                            f"{department.name}: 未返回部门 ID"
                        )
                        print(
                            f"失败: 创建 {department.name} 未返回部门 ID"
                        )
                        break
                    id_map[department.open_department_id] = new_id
                    created += 1
                    created_departments.append(
                        f"{department.name}（id={new_id}）"
                    )
                    print(f"已创建部门: {department.name} | id={new_id}")
                    _write_audit(
                        audit,
                        action="create_department",
                        status="created",
                        name=department.name,
                        didi_id=new_id,
                        out_budget_id=department.open_department_id,
                        parent_id=parent_id,
                        leader_id=item.leader_member_id,
                    )
                else:
                    id_map[department.open_department_id] = str(
                        item.didi_id or ""
                    )
                    if item.action == "update" and item.didi_id:
                        try:
                            writer.update_department(
                                department_id=item.didi_id,
                                name=department.name or "",
                                out_budget_id=department.open_department_id,
                                parent_id=parent_id,
                                leader_id=item.leader_member_id,
                                clear_leader=item.clear_leader,
                            )
                        except DidiError as exc:
                            failed += 1
                            dept_failure_notifications.append(
                                f"{department.name}: {exc.errmsg}"
                            )
                            print(
                                f"失败: 更新 {department.name} -> {exc}"
                            )
                            _write_audit(
                                audit,
                                action="update_department",
                                status="failed",
                                name=department.name,
                                didi_id=item.didi_id,
                                errno=exc.errno,
                                errmsg=exc.errmsg,
                                request_id=exc.request_id,
                            )
                            continue
                        updated += 1
                        updated_departments.append(department.name or "")
                        print(
                            f"已更新部门: {department.name} "
                            f"| id={item.didi_id}"
                        )
                        _write_audit(
                            audit,
                            action="update_department",
                            status="updated",
                            name=department.name,
                            didi_id=item.didi_id,
                            out_budget_id=department.open_department_id,
                            parent_id=parent_id,
                            leader_id=item.leader_member_id,
                        )
                time.sleep(0.25)

    print()
    print(f"部门完成: 创建 {created} | 更新 {updated} | 失败 {failed}")

    if created_departments or updated_departments:
        lines: list[str] = []
        if created_departments:
            lines.append(
                f"新建部门 {len(created_departments)} 个："
                + "、".join(created_departments)
            )
        if updated_departments:
            lines.append(
                f"更新部门 {len(updated_departments)} 个："
                + "、".join(updated_departments)
            )
        send_card_safe(
            "lark2didi：部门同步变更",
            lines,
            template="blue",
            note=f"失败 {failed}",
        )
    if dept_failure_notifications:
        send_card_safe(
            "lark2didi：部门同步失败",
            dept_failure_notifications,
            template="red",
        )

    if not args.move_members:
        print("员工未移动；确认部门无误后加 --move-members --allow-mass。")
        return 0 if failed == 0 else 3

    if not move_allowed:
        send_card_safe(
            "lark2didi：人员迁移未执行",
            [
                f"需要移动 {len(plan.moves)} 人，超过上限 {move_limit}；"
                "确认后请手动加 --allow-mass 补跑。"
            ],
            template="orange",
        )
        print("员工未移动；确认后可手动加 --allow-mass 补跑人员迁移。")
        return 0 if failed == 0 else 3

    moved = 0
    move_failed = 0
    skipped = 0
    moved_members: list[str] = []
    move_failure_notifications: list[str] = []
    with move_log.open("a", encoding="utf-8") as audit:
        move_log.chmod(0o600)
        with DidiBudgetCenterWriter(didi_config) as writer:
            for move in plan.moves:
                target_id = id_map.get(
                    move.user.primary_department_id or ""
                )
                if not target_id:
                    skipped += 1
                    move_failure_notifications.append(
                        f"{move.user.name}: 目标部门没有滴滴 ID"
                    )
                    print(
                        f"跳过: {move.user.name} 的目标部门没有滴滴 ID"
                    )
                    continue
                if move.from_center_id == target_id:
                    skipped += 1
                    continue
                try:
                    writer.move_member(
                        member_id=str(move.member.get("id")),
                        budget_center_id=target_id,
                    )
                except DidiError as exc:
                    move_failed += 1
                    move_failure_notifications.append(
                        f"{move.user.name}: {exc.errmsg}"
                    )
                    print(f"失败: 移动 {move.user.name} -> {exc}")
                    _write_audit(
                        audit,
                        action="move_member",
                        status="failed",
                        name=move.user.name,
                        member_id=move.member.get("id"),
                        target_center_id=target_id,
                        errno=exc.errno,
                        errmsg=exc.errmsg,
                        request_id=exc.request_id,
                    )
                    continue
                moved += 1
                moved_members.append(
                    f"{move.user.name} → {move.to_department.name}"
                )
                print(
                    f"已移动: {move.user.name} -> "
                    f"{move.to_department.name} ({target_id})"
                )
                _write_audit(
                    audit,
                    action="move_member",
                    status="moved",
                    name=move.user.name,
                    member_id=move.member.get("id"),
                    target_center_id=target_id,
                    target_department=move.to_department.name,
                )
                time.sleep(0.25)

    print()
    print(
        f"员工移动完成: 成功 {moved} | 跳过 {skipped} | 失败 {move_failed}"
    )
    if moved_members:
        preview = "、".join(moved_members[:10])
        suffix = (
            f" 等 {len(moved_members)} 人"
            if len(moved_members) > 10
            else ""
        )
        send_card_safe(
            f"lark2didi：员工部门调整 {len(moved_members)} 人",
            [f"{preview}{suffix}"],
            template="blue",
            note=f"失败 {move_failed}",
        )
    if move_failure_notifications:
        send_card_safe(
            "lark2didi：员工部门调整失败",
            move_failure_notifications,
            template="red",
        )
    return 0 if failed == 0 and move_failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
