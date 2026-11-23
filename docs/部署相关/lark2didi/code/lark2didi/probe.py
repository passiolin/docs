from __future__ import annotations

import sys

from .config import ConfigError, FeishuConfig
from .feishu import FeishuClient, FeishuError, FeishuPermissionError

DEPARTMENT_NAME_SCOPE = "contact:department.base:readonly"


def _permission_url(app_id: str, scope: str) -> str:
    return (
        f"https://open.feishu.cn/app/{app_id}/auth"
        f"?q={scope}&op_from=openapi&token_type=tenant"
    )


def main() -> int:
    try:
        config = FeishuConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1

    try:
        with FeishuClient(config) as client:
            snapshot = client.snapshot()
    except FeishuPermissionError as exc:
        print(f"飞书权限不足: {exc}", file=sys.stderr)
        print(
            f"权限申请入口: https://open.feishu.cn/app/{config.app_id}/auth",
            file=sys.stderr,
        )
        return 2
    except FeishuError as exc:
        print(f"飞书接口失败: {exc}", file=sys.stderr)
        return 2

    root_only = sum(
        1 for user in snapshot.users if set(user.department_ids) <= {"0", ""}
    )
    print("飞书鉴权: OK")
    print(f"部门: {len(snapshot.departments)} 个")
    print(f"员工: {len(snapshot.users)} 人（按 open_id 去重）")
    print(f"仅挂在根部门: {root_only} 人")

    missing = snapshot.departments_without_name
    if missing:
        print()
        print(
            f"警告: {len(missing)}/{len(snapshot.departments)} 个部门没有返回 name 字段。"
        )
        print(
            f"原因: 应用缺少字段权限 {DEPARTMENT_NAME_SCOPE}（获取部门基础信息）。"
        )
        print(
            "处理: 在飞书开放平台给应用添加该权限，创建并发布新版本，"
            "管理员审批通过后重跑。"
        )
        print(f"申请链接: {_permission_url(config.app_id, DEPARTMENT_NAME_SCOPE)}")

    print()
    print("部门明细（前 20 个）:")
    for dept in snapshot.departments[:20]:
        label = dept.name or "<name 未授权>"
        print(
            f"  - {label} | id={dept.open_department_id} "
            f"| 直属 {dept.member_count} 人 | parent={dept.parent_department_id}"
        )

    print()
    print("员工样例（前 10 人，不展示手机号/邮箱）:")
    for user in snapshot.users[:10]:
        print(
            f"  - {user.name} | 工号={user.employee_no or '-'} "
            f"| open_id={user.open_id} | 部门数={len(user.department_ids)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
