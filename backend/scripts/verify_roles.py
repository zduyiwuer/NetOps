"""多角色与审批员权限验证脚本。

覆盖：
  1. 角色定义与层级（admin > operator/approver > viewer）
  2. 角色集合归一化（去重、非法过滤、稳定排序、主角色推导）
  3. 存储层解析与序列化（JSON 数组 / 逗号分隔 / 兼容回退）
  4. 权限判定（层级门槛、审批权、操作权）
  5. 用户模型多角色落库与历史数据回退
  6. 认证 API：创建多角色用户、更新角色、角色清单接口
  7. 审批流程：审批员可审批、纯操作员不可审批、申请人不能自审

运行：python -m scripts.verify_roles
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class _FakeUser:
    """最小用户替身，用于纯函数级断言。"""

    def __init__(self, role: str = "viewer", roles=None):
        self.role = role
        self.roles = roles if roles is not None else "[]"


def main() -> int:
    print("\n=== 多角色与审批员权限验证 ===\n")

    from app.core import roles as R

    # ---------------------------------------------------------- 1. 角色定义
    print("[1] 角色定义与层级")
    check("4 种角色已定义", set(R.ALL_ROLES) == {"admin", "operator", "approver", "viewer"}, str(R.ALL_ROLES))
    check("审批员角色存在", "approver" in R.ALL_ROLES)
    check("管理员层级最高", R.ROLE_LEVEL["admin"] > R.ROLE_LEVEL["operator"])
    check("审批员与操作员同级", R.ROLE_LEVEL["approver"] == R.ROLE_LEVEL["operator"])
    check("只读层级最低", R.ROLE_LEVEL["viewer"] < R.ROLE_LEVEL["operator"])
    check("审批权含审批员与管理员", set(R.APPROVER_ROLES) == {"approver", "admin"})
    check("每个角色都有中文名", all(R.ROLE_META.get(r, {}).get("label") for r in R.ALL_ROLES))
    check("每个角色都有说明", all(R.ROLE_META.get(r, {}).get("desc") for r in R.ALL_ROLES))

    # ---------------------------------------------------------- 2. 归一化
    print("\n[2] 角色集合归一化")
    check("过滤非法角色", R.normalize_roles(["operator", "root"]) == ["operator"])
    check("去重", R.normalize_roles(["operator", "operator"]) == ["operator"])
    check("多角色保留", set(R.normalize_roles(["operator", "approver"])) == {"operator", "approver"})
    check("主角色自动并入集合", "admin" in R.normalize_roles(["viewer"], "admin"))
    check("空集合返回空列表", R.normalize_roles([]) == [])
    check("None 返回空列表", R.normalize_roles(None) == [])
    check(
        "排序稳定：管理员优先",
        R.normalize_roles(["viewer", "admin", "operator"])[0] == "admin",
        str(R.normalize_roles(["viewer", "admin", "operator"])),
    )
    check(
        "排序稳定：操作员先于审批员",
        R.normalize_roles(["approver", "operator"]) == ["operator", "approver"],
        str(R.normalize_roles(["approver", "operator"])),
    )
    check("主角色取权限最高者", R.primary_role(["viewer", "approver", "operator"]) == "operator")
    check("主角色：admin 优先", R.primary_role(["admin", "operator"]) == "admin")
    check("主角色：空集合回退只读", R.primary_role([]) == "viewer")
    check("中文描述", R.describe_roles(["operator", "approver"]) == "操作员、审批员")

    # ---------------------------------------------------------- 3. 存储解析
    print("\n[3] 存储层解析与序列化")
    check("解析 JSON 数组", R.parse_roles('["operator","approver"]') == ["operator", "approver"])
    check("解析空数组", R.parse_roles("[]") == [])
    check("解析空串", R.parse_roles("") == [])
    check("解析 None", R.parse_roles(None) == [])
    check("解析逗号分隔", R.parse_roles("operator,approver") == ["operator", "approver"])
    check("解析非法 JSON 回退", R.parse_roles("{bad json") == [])
    check("解析已是 list", R.parse_roles(["operator"]) == ["operator"])
    check("序列化含全部角色", json.loads(R.dump_roles(["operator", "approver"])) == ["operator", "approver"])
    check("序列化去重", json.loads(R.dump_roles(["operator", "operator"])) == ["operator"])

    # ---------------------------------------------------------- 4. 权限判定
    print("\n[4] 权限判定")
    admin = _FakeUser("admin", '["admin"]')
    operator = _FakeUser("operator", '["operator"]')
    approver = _FakeUser("approver", '["approver"]')
    viewer = _FakeUser("viewer", '["viewer"]')
    both = _FakeUser("operator", '["operator","approver"]')
    legacy = _FakeUser("operator", "[]")  # 历史数据：roles 为空

    check("管理员层级 3", R.user_level(admin) == 3)
    check("操作员层级 2", R.user_level(operator) == 2)
    check("审批员层级 2", R.user_level(approver) == 2)
    check("只读层级 1", R.user_level(viewer) == 1)
    check("多角色取最高层级", R.user_level(both) == 2)
    check("历史数据回退到 role", R.user_level(legacy) == 2)
    check("历史数据角色集合", R.effective_roles(legacy) == ["operator"])

    check("管理员可审批", R.can_approve(admin))
    check("审批员可审批", R.can_approve(approver))
    check("操作员不可审批", not R.can_approve(operator))
    check("只读不可审批", not R.can_approve(viewer))
    check("操作员+审批员可审批", R.can_approve(both))

    check("管理员可操作", R.can_operate(admin))
    check("操作员可操作", R.can_operate(operator))
    check("审批员不可操作设备", not R.can_operate(approver))
    check("只读不可操作", not R.can_operate(viewer))
    check("操作员+审批员可操作", R.can_operate(both))

    check("has_role 命中", R.has_role(both, "approver"))
    check("has_role 未命中", not R.has_role(operator, "approver"))
    check("角色清单可导出", len(R.role_descriptions()) == 4)
    check("角色清单含审批员", any(r["key"] == "approver" for r in R.role_descriptions()))

    # ---------------------------------------------------------- 5. 模型落库
    print("\n[5] 用户模型多角色落库")
    # roles 列是新增字段，先跑一次轻量迁移（应用启动时同样会执行）
    from app.main import _migrate_schema

    _migrate_schema()

    from app.db.session import SessionLocal
    from app.models.models import User

    db = SessionLocal()
    try:
        u = User(username="__v_roles_test__", role="operator", roles=R.dump_roles(["operator", "approver"]))
        db.add(u)
        db.commit()
        db.refresh(u)
        check("roles 已落库", R.parse_roles(u.roles) == ["operator", "approver"], u.roles)
        check("effective_roles 读取正确", R.effective_roles(u) == ["operator", "approver"])
        check("role 字段保持主角色", u.role == "operator")
        check("双角色用户可审批", R.can_approve(u))
        check("双角色用户可操作", R.can_operate(u))
        db.delete(u)
        db.commit()
    finally:
        db.close()

    # ---------------------------------------------------------- 6. API
    print("\n[6] 认证 API 多角色")
    from fastapi.testclient import TestClient

    from app.core.security import hash_password
    from app.main import app

    # 幂等：先清掉上次运行遗留的测试账号
    db0 = SessionLocal()
    try:
        for name in ("role_tester", "approver_tester"):
            old = db0.query(User).filter(User.username == name).first()
            if old:
                db0.delete(old)
        db0.commit()
    finally:
        db0.close()

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123", "source": "local"},
    )
    check("管理员登录成功", login.status_code == 200, f"HTTP {login.status_code}")
    token = login.json().get("access_token", "") if login.status_code == 200 else ""
    headers = {"Authorization": f"Bearer {token}"}

    roles_res = client.get("/api/auth/roles", headers=headers)
    check("角色清单接口可访问", roles_res.status_code == 200, f"HTTP {roles_res.status_code}")
    if roles_res.status_code == 200:
        items = roles_res.json().get("items", [])
        check("角色清单含 4 项", len(items) == 4, f"{len(items)} 项")
        check("角色清单含审批员", any(r["key"] == "approver" for r in items))

    create_res = client.post(
        "/api/auth/users",
        headers=headers,
        json={
            "username": "role_tester",
            "password": "test1234",
            "display_name": "多角色测试",
            "roles": ["operator", "approver"],
        },
    )
    check("创建多角色用户", create_res.status_code == 200, f"HTTP {create_res.status_code} {create_res.text[:120]}")
    user_id = None
    if create_res.status_code == 200:
        body = create_res.json()
        user_id = body.get("id")
        check("返回角色集合", body.get("roles") == ["operator", "approver"], str(body.get("roles")))
        check("返回主角色", body.get("role") == "operator", str(body.get("role")))

    list_res = client.get("/api/auth/users", headers=headers)
    check("用户列表可访问", list_res.status_code == 200, f"HTTP {list_res.status_code}")
    if list_res.status_code == 200 and user_id:
        item = next((u for u in list_res.json()["items"] if u["id"] == user_id), None)
        check("列表项含角色集合", item is not None and item.get("roles") == ["operator", "approver"])

    if user_id:
        upd_res = client.put(
            f"/api/auth/users/{user_id}",
            headers=headers,
            json={"roles": ["approver"]},
        )
        check("更新角色为单角色", upd_res.status_code == 200, f"HTTP {upd_res.status_code}")
        if upd_res.status_code == 200:
            check("更新后角色集合", upd_res.json().get("roles") == ["approver"], str(upd_res.json().get("roles")))
            check("更新后主角色同步", upd_res.json().get("role") == "approver")

        upd2 = client.put(
            f"/api/auth/users/{user_id}",
            headers=headers,
            json={"display_name": "改名字不改角色"},
        )
        check("不传角色时保持原集合", upd2.status_code == 200 and upd2.json().get("roles") == ["approver"],
              str(upd2.json().get("roles")) if upd2.status_code == 200 else f"HTTP {upd2.status_code}")

        bad = client.put(
            f"/api/auth/users/{user_id}",
            headers=headers,
            json={"roles": ["root"]},
        )
        check("非法角色被拒绝", bad.status_code == 400, f"HTTP {bad.status_code}")

        # 新建一个纯审批员，验证审批接口的权限边界
        appr_res = client.post(
            "/api/auth/users",
            headers=headers,
            json={
                "username": "approver_tester",
                "password": "test1234",
                "display_name": "纯审批员",
                "roles": ["approver"],
            },
        )
        check("创建纯审批员用户", appr_res.status_code == 200, f"HTTP {appr_res.status_code}")

    # ---------------------------------------------------------- 7. 审批流程
    print("\n[7] 审批流程与职责分离")
    from app.services import device_access_service as das

    db = SessionLocal()
    try:
        # 申请人提交
        req = das.create_change_request(
            db,
            device_id=1,
            title="多角色审批验证",
            description="验证审批员权限与自审拦截",
            config_content="set test",
            applicant="operator_user",
        )
        check("变更申请创建成功", req is not None and req.status == "pending")

        # 纯操作员审批 → 应被拒绝
        op_user = _FakeUser("operator", '["operator"]')
        check("纯操作员无审批权", not R.can_approve(op_user))

        # 申请人自审 → 应被拒绝
        try:
            das.review_change_request(db, req.id, approve=True, reviewer="operator_user")
            check("申请人不能审批自己的申请", False, "未被拦截")
        except ValueError as exc:
            check("申请人不能审批自己的申请", "自己" in str(exc), str(exc))

        # 审批员审批他人申请 → 通过
        try:
            das.review_change_request(db, req.id, approve=True, reviewer="approver_user", comment="同意")
            check("审批员可审批他人申请", req.status == "approved", req.status)
        except ValueError as exc:
            check("审批员可审批他人申请", False, str(exc))

        # 已审批的申请不可重复审批
        try:
            das.review_change_request(db, req.id, approve=True, reviewer="approver_user2")
            check("重复审批被拒绝", False, "未被拦截")
        except ValueError as exc:
            check("重复审批被拒绝", "不可审批" in str(exc), str(exc))

        # 驳回必填意见
        req2 = das.create_change_request(
            db,
            device_id=1,
            title="驳回校验",
            description="验证驳回意见必填",
            config_content="set test2",
            applicant="operator_user2",
        )
        try:
            das.review_change_request(db, req2.id, approve=False, reviewer="approver_user")
            check("驳回未填意见被拒绝", False, "未被拦截")
        except ValueError as exc:
            check("驳回未填意见被拒绝", "意见" in str(exc), str(exc))
    finally:
        db.close()

    # ---------------------------------------------------------- 8. 权限依赖
    print("\n[8] 接口权限依赖")
    from app.core.security import require_approver, require_operator

    check("require_approver 是依赖函数", callable(require_approver))
    check("require_operator 是依赖函数", callable(require_operator))

    import app.api.device_access as da

    src = Path(da.__file__).read_text(encoding="utf-8")
    check("审批接口已改用审批员权限", "require_approver" in src)
    check("审批接口不再直接用 require_admin", "review_change" in src and
          "require_admin" not in src.split("def review_change")[1].split("async def")[0])

    # ---------------------------------------------------------- 清理
    db2 = SessionLocal()
    try:
        for name in ("role_tester", "approver_tester"):
            row = db2.query(User).filter(User.username == name).first()
            if row:
                db2.delete(row)
        db2.commit()
    finally:
        db2.close()

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    if not all(c for _, c, _ in results):
        print("未通过项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} {detail}")
        print()
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
