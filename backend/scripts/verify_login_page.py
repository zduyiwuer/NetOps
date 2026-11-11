"""登录页品牌名与厂商列表验证。

覆盖：
  1. 后端公开配置接口返回平台名与厂商列表
  2. 厂商列表与设备管理页同源（含飞塔 Fortinet、Cisco ASA 等）
  3. 登录页展示的厂商标签与后端一致且无遗漏
  4. 平台名称在登录页、侧边栏、浏览器标题均已替换
  5. 页面无控制台错误

运行：python -m scripts.verify_login_page（需先启动后端服务）
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
# 必须出现在登录页的厂商（用户明确要求的飞塔防火墙等）
REQUIRED_VENDORS = [
    "飞塔 Fortinet FortiGate",
    "Cisco ASA 防火墙",
    "Cisco WLC 无线控制器",
]

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 登录页品牌名与厂商列表验证 ===\n")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- 1. 公开接口
    print("[1] 公开配置接口")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        r = c.get("/api/auth/config")
    check("接口可访问", r.status_code == 200, f"HTTP {r.status_code}")
    cfg = r.json() if r.status_code == 200 else {}
    check("返回平台名", cfg.get("app_name") == EXPECTED_APP_NAME, str(cfg.get("app_name")))
    vendors = cfg.get("vendors") or []
    check("返回厂商列表", len(vendors) > 0, f"{len(vendors)} 家")
    check(
        "厂商列表含 key 与 label",
        all("value" in v and "label" in v for v in vendors),
    )
    labels = [v["label"] for v in vendors]
    for need in REQUIRED_VENDORS:
        check(f"含「{need}」", need in labels)
    check("含通用设备兜底项", "通用标准 MIB 设备" in labels)

    # ---------------------------------------------------------- 2. 与设备页同源
    print("\n[2] 与设备管理页同源")
    from app.collectors.vendors import vendor_options

    backend_labels = [v["label"] for v in vendor_options()]
    check("与后端厂商定义一致", backend_labels == labels, f"{len(backend_labels)} vs {len(labels)}")

    # ---------------------------------------------------------- 3. 登录页渲染
    print("\n[3] 登录页渲染")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()
        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(2500)

        body = page.inner_text("body")
        check("显示平台名称", EXPECTED_APP_NAME in body)
        check("已无旧品牌名 NetOps", "NetOps" not in body, "仍存在旧名称" if "NetOps" in body else "")

        for need in REQUIRED_VENDORS:
            check(f"登录页展示「{need}」", need in body)

        # 厂商标签数量应与后端一致
        tag_count = page.locator(".vendors .el-tag").count()
        check("厂商标签数量与后端一致", tag_count == len(vendors), f"{tag_count} vs {len(vendors)}")

        # 浏览器标题
        title = page.title()
        check("浏览器标题含平台名", EXPECTED_APP_NAME in title, title)

        page.screenshot(path=str(SHOT_DIR / "01-login.png"))

        # 登录后侧边栏
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(3000)
        check("登录成功进入首页", "dashboard" in page.url, page.url)
        if "dashboard" in page.url:
            layout = page.inner_text("body")
            check("侧边栏显示平台名", EXPECTED_APP_NAME in layout)
            check("侧边栏无旧品牌名", "NetOps" not in layout)
            page.screenshot(path=str(SHOT_DIR / "login-brand-dashboard.png"))

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
