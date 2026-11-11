"""系统管理 → 平台说明页验证。

覆盖：
  1. 平台说明页正常渲染，无控制台错误
  2. 展示平台名称、定位与版本
  3. 支持厂商表格完整（含飞塔、Cisco ASA、WLC），按类别排序
  4. 功能清单含新增能力（配置变更审批、遥测、角色体系）
  5. 厂商数据与后端接口一致

运行：python -m scripts.verify_about_page（需先启动后端服务）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
EXPECTED_APP_NAME = "网络设备运维平台"
REQUIRED_VENDORS = [
    "飞塔 Fortinet FortiGate",
    "Cisco ASA 防火墙",
    "Cisco WLC 无线控制器",
]
# 类别展示顺序
CATEGORY_ORDER = ["交换机", "防火墙", "无线控制器", "通用"]
# 功能清单必须包含的新能力
REQUIRED_FEATURES = ["配置变更审批", "遥测采集", "用户与权限"]

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 系统管理·平台说明页验证 ===\n")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------- 1. 后端厂商数据
    print("[1] 后端厂商数据")
    from app.collectors.vendors import vendor_options

    opts = vendor_options()
    check("厂商数量为 8", len(opts) == 8, f"{len(opts)} 家")
    check(
        "每项都有分类与说明",
        all(o.get("category") and o.get("desc") for o in opts),
    )
    labels = [o["label"] for o in opts]
    for need in REQUIRED_VENDORS:
        check(f"含「{need}」", need in labels)

    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "") if login.status_code == 200 else ""
    check("管理员登录成功", bool(token), f"HTTP {login.status_code}")

    api_vendors = []
    if token:
        with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
            r = c.get("/api/vendors", headers={"Authorization": f"Bearer {token}"})
        check("厂商接口可访问", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code == 200:
            api_vendors = r.json().get("items", [])
            check("接口返回 8 家", len(api_vendors) == 8, f"{len(api_vendors)} 家")
            check("接口含分类字段", all("category" in v for v in api_vendors))

    # ---------------------------------------------------- 2. 页面渲染
    print("\n[2] 平台说明页渲染")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()
        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(3000)
        check("登录成功", "dashboard" in page.url, page.url)

        # 进入系统管理
        page.goto(f"{BASE}/#/system", wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 切到平台说明 tab
        clicked = False
        for label in ("平台说明",):
            try:
                page.click(f"text={label}", timeout=4000)
                clicked = True
                break
            except Exception:
                continue
        check("切换到平台说明标签", clicked)
        page.wait_for_timeout(2000)

        body = page.inner_text("body")
        check("显示平台名称", EXPECTED_APP_NAME in body)
        check("无旧品牌名 NetOps", "NetOps" not in body)
        check("显示平台定位说明", "面向企业网络的多厂商设备" in body)

        # 厂商表格
        rows = page.locator(".el-table__row")
        check("厂商表格有数据", rows.count() >= 8, f"{rows.count()} 行")
        for need in REQUIRED_VENDORS:
            check(f"平台说明展示「{need}」", need in body)

        # 分类顺序：交换机 → 防火墙 → 无线控制器 → 通用
        cats = page.locator(".el-table__row td:nth-child(2) .el-tag")
        cat_list = [cats.nth(i).inner_text().strip() for i in range(cats.count())]
        # 按首次出现顺序去重，得到类别的排列
        seen: list[str] = []
        for c in cat_list:
            if c in CATEGORY_ORDER and c not in seen:
                seen.append(c)
        expected_order = [c for c in CATEGORY_ORDER if c in seen]
        check(
            "厂商按类别排序",
            seen == expected_order,
            f"{seen}",
        )

        # 功能清单
        for feat in REQUIRED_FEATURES:
            check(f"功能清单含「{feat}」", feat in body)

        page.screenshot(path=str(SHOT_DIR / "system-about.png"), full_page=True)

        real_errors = [
            m for m in console_msgs
            if ("[error]" in m or "[pageerror]" in m)
            and not any(ig in m for ig in ("favicon", "DevTools", "ResizeObserver", "Third-party"))
        ]
        check("无控制台错误", not real_errors, real_errors[0][:110] if real_errors else "")
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
