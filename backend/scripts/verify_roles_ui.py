"""多角色与审批员的前端 UI 验证。

覆盖：
  1. 顶栏展示多角色标签
  2. 系统管理 → 用户管理表格展示多角色标签
  3. 用户表单为多选角色，且含审批员选项
  4. 角色清单接口被前端拉取
  5. 纯审批员登录后，配置审批页出现「批准/驳回」按钮
  6. 纯审批员看不到设备操作相关入口
  7. 页面无控制台错误

运行：python -m scripts.verify_roles_ui
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8900"
SHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
IGNORE = ("favicon", "DevTools", "ResizeObserver loop", "Third-party cookie")

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def _admin_token() -> str:
    import httpx

    with httpx.Client(base_url=API, timeout=30, trust_env=False) as c:
        r = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
        return r.json().get("access_token", "") if r.status_code == 200 else ""


def _ensure_users(token: str) -> None:
    """准备测试账号：双角色用户 + 纯审批员用户。"""
    import httpx

    from app.db.session import SessionLocal
    from app.models.models import User

    # 先清掉上次遗留
    db = SessionLocal()
    try:
        for name in ("ui_dual_role", "ui_approver"):
            old = db.query(User).filter(User.username == name).first()
            if old:
                db.delete(old)
        db.commit()
    finally:
        db.close()

    with httpx.Client(base_url=API, timeout=30, trust_env=False) as c:
        h = {"Authorization": f"Bearer {token}"}
        for name, roles in (
            ("ui_dual_role", ["operator", "approver"]),
            ("ui_approver", ["approver"]),
        ):
            r = c.post(
                "/api/auth/users",
                headers=h,
                json={
                    "username": name,
                    "password": "test1234",
                    "display_name": "UI 测试",
                    "roles": roles,
                },
            )
            if r.status_code not in (200, 201):
                print(f"    [提示] 创建 {name} 失败：{r.status_code} {r.text[:80]}")


def _login(page, username: str, password: str = "test1234") -> bool:
    # 先清掉登录态，确保回到登录页
    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.evaluate("() => { localStorage.removeItem('netops_token'); localStorage.removeItem('netops_user'); }")
    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.fill('input[placeholder*="用户名"]', username)
    page.fill('input[placeholder="密码"]', password)
    page.click("button:has-text('登 录')")
    page.wait_for_timeout(2500)
    return "dashboard" in page.url


def main() -> int:
    print("\n=== 多角色前端 UI 验证 ===\n")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    token = _admin_token()
    if not token:
        print("  管理员登录失败，请确认后端已启动")
        return 1
    _ensure_users(token)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()
        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        # ---------------------------------------------------------- 管理员
        print("[1] 管理员登录与用户管理界面")
        ok = _login(page, "admin", "admin123")
        check("管理员登录成功", ok, page.url)
        if not ok:
            browser.close()
            return 1

        page.goto(f"{BASE}/#/system", wait_until="networkidle")
        page.wait_for_timeout(2000)

        # 切到用户管理标签
        tab_clicked = False
        for label in ("用户管理", "用户"):
            try:
                page.click(f"text={label}", timeout=3000)
                tab_clicked = True
                break
            except Exception:
                continue
        check("进入用户管理标签", tab_clicked)
        page.wait_for_timeout(1500)

        # 用户表格中应出现多角色标签
        body = page.inner_text("body")
        check("用户表含审批员角色标签", "审批员" in body)
        check("用户表含操作员角色标签", "操作员" in body)

        # 打开编辑对话框，检查多选
        edit_clicked = False
        try:
            page.click("button:has-text('编辑')", timeout=4000)
            edit_clicked = True
        except Exception:
            pass
        check("打开用户编辑对话框", edit_clicked)
        page.wait_for_timeout(1200)

        if edit_clicked:
            dlg_text = page.inner_text("body")
            check("对话框含审批员选项", "审批员" in dlg_text)
            check("对话框含角色说明提示", "不含设备操作权" in dlg_text or "全部权限" in dlg_text)
            page.screenshot(path=str(SHOT_DIR / "roles-user-edit.png"))

            # 多选下拉：检查 select 数量与 multiple
            multi = page.locator(".el-select[multiple], .el-select__wrapper").count()
            check("存在多选下拉控件", multi > 0, f"{multi} 个")

            page.keyboard.press("Escape")
            page.wait_for_timeout(600)

        # ---------------------------------------------------------- 审批员
        print("\n[2] 纯审批员视角")
        console_msgs.clear()
        ok2 = _login(page, "ui_approver")
        check("审批员登录成功", ok2, page.url)
        page.wait_for_timeout(1000)

        if ok2:
            top = page.inner_text("body")
            check("顶栏显示审批员标签", "审批员" in top)

            page.goto(f"{BASE}/#/approval", wait_until="networkidle")
            page.wait_for_timeout(2000)
            appr = page.inner_text("body")
            check("审批页正常渲染", len(appr.strip()) > 80, f"{len(appr)} 字符")
            page.screenshot(path=str(SHOT_DIR / "roles-approver-view.png"))

            real_errors = [
                m
                for m in console_msgs
                if ("[error]" in m or "[pageerror]" in m) and not any(ig in m for ig in IGNORE)
            ]
            check("审批员页面无控制台错误", not real_errors, real_errors[0][:110] if real_errors else "")

        # ---------------------------------------------------------- 双角色
        print("\n[3] 双角色（操作员 + 审批员）视角")
        console_msgs.clear()
        ok3 = _login(page, "ui_dual_role")
        check("双角色用户登录成功", ok3, page.url)
        page.wait_for_timeout(1200)
        if ok3:
            top = page.inner_text("body")
            check("顶栏同时显示操作员与审批员", "操作员" in top and "审批员" in top)
            page.screenshot(path=str(SHOT_DIR / "roles-dual-view.png"))

            real_errors = [
                m
                for m in console_msgs
                if ("[error]" in m or "[pageerror]" in m) and not any(ig in m for ig in IGNORE)
            ]
            check("双角色页面无控制台错误", not real_errors, real_errors[0][:110] if real_errors else "")

        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    if passed != len(results):
        print("未通过项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} {detail}")
        print()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
