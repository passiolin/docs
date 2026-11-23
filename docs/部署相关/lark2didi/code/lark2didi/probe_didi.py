from __future__ import annotations

import sys
from collections import Counter

from .config import ConfigError, DidiConfig
from .didi import DidiClient, DidiError


def main() -> int:
    try:
        config = DidiConfig.load()
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1

    try:
        with DidiClient(config) as client:
            client.authorize()
            centers, members = client.snapshot()
            sign_method = client.sign_method
    except DidiError as exc:
        print(f"滴滴接口失败: {exc}", file=sys.stderr)
        return 2

    print("滴滴鉴权: OK")
    print(f"签名算法: {sign_method}")
    print("模式: 只读（未发送任何写/删请求）")
    print(f"部门/成本中心: {len(centers)} 个")
    print(f"员工: {len(members)} 人")

    dismissed = [
        member for member in members if str(member.get("dismiss_time") or "")
    ]
    missing_employee_no = [
        member for member in members if not member.get("employee_number")
    ]
    employee_numbers = Counter(
        str(member.get("employee_number"))
        for member in members
        if member.get("employee_number")
    )
    duplicates = {
        number: count for number, count in employee_numbers.items() if count > 1
    }

    print(f"带离职时间: {len(dismissed)} 人")
    print(f"缺工号: {len(missing_employee_no)} 人")
    print(f"重复工号: {duplicates or '无'}")

    print()
    print("部门样例（前 10 个）:")
    for center in centers[:10]:
        print(
            f"  - id={center.get('id')} | {center.get('name')} "
            f"| parent={center.get('parent_id')} "
            f"| 成员={center.get('member_num')} "
            f"| 外部编号={center.get('out_budget_id') or '-'}"
        )

    print()
    print("员工样例（前 10 人，不展示手机号/邮箱）:")
    for member in members[:10]:
        print(
            f"  - {member.get('realname')} "
            f"| 工号={member.get('employee_number') or '-'} "
            f"| 部门={member.get('department') or '-'} "
            f"| budget_center_id={member.get('budget_center_id') or '-'} "
            f"| 离职时间={member.get('dismiss_time') or '-'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
