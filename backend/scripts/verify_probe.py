"""设备在线探测功能验证。

覆盖：
  1. 探测服务核心逻辑（ProbeResult / 状态写回 / 并发限制）
  2. 单设备探测接口：正常、404、401、权限
  3. 批量探测接口：正常、并发参数校验、状态写回
  4. 前端设备管理页：探测按钮、批量探测入口、结果对话框

运行：python -m scripts.verify_probe（需先启动后端服务）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 设备在线探测验证 ===\n")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------- 1. 服务核心逻辑
    print("[1] 探测服务核心逻辑")
    from app.db.session import SessionLocal
    from app.models.models import Device
    from app.services.probe_service import ProbeResult, probe_many

    r = ProbeResult(1, "测试设备", "1.2.3.4", True, response_ms=12.5)
    d = r.to_dict()
    check("在线结果序列化正确", d["online"] is True and d["status"] == "online")
    check("响应时间保留", d["response_ms"] == 12.5)
    r2 = ProbeResult(2, "离线设备", "1.2.3.5", False, error="超时")
    check("离线结果序列化正确", r2.to_dict()["status"] == "offline")
    check("错误信息保留", r2.to_dict()["error"] == "超时")

    db = SessionLocal()
    try:
        dev = db.query(Device).first()
        if dev is None:
            check("存在测试设备", False, "数据库无设备")
        else:
            # 状态写回
            from app.services.probe_service import _apply_status

            before = dev.status
            _apply_status(db, ProbeResult(dev.id, dev.name, dev.ip, False, error="测试"))
            db.commit()
            db.refresh(dev)
            check("离线结果写回 status", dev.status == "offline", f"{before} -> {dev.status}")
            _apply_status(db, ProbeResult(dev.id, dev.name, dev.ip, True))
            db.commit()
            db.refresh(dev)
            check("在线结果写回 status", dev.status == "online")
            check("在线结果写回 last_seen", dev.last_seen is not None)
    finally:
        db.close()

    # 空设备列表：应正常返回而不是抛异常
    db2 = SessionLocal()
    try:
        result = asyncio_run(probe_many(db2, device_ids=[], concurrency=8))
        check("空设备列表返回 total=0", result["total"] == 0, str(result["total"]))
        check("空设备列表不报错", result["items"] == [])
    finally:
        db2.close()

    # ---------------------------------------------------- 2. 接口验证
    print("\n[2] 单设备探测接口")
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "") if login.status_code == 200 else ""
    check("管理员登录成功", bool(token), f"HTTP {login.status_code}")
    H = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=BASE, timeout=120, trust_env=False) as c:
        devices = c.get("/api/devices", headers=H, params={"page_size": 5})
    check("设备列表可访问", devices.status_code == 200, f"HTTP {devices.status_code}")
    items = devices.json().get("items", []) if devices.status_code == 200 else []

    if items:
        did = items[0]["id"]
        with httpx.Client(base_url=BASE, timeout=120, trust_env=False) as c:
            r = c.post(f"/api/devices/{did}/probe", headers=H)
        check("单设备探测返回 200", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            check("返回设备 ID", body.get("device_id") == did)
            check("返回在线标记", "online" in body)
            check("返回状态字段", body.get("status") in ("online", "offline"))
            check("返回耗时", "elapsed_ms" in body)

        # 探测后状态应被更新
        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            after = c.get(f"/api/devices/{did}", headers=H)
        if after.status_code == 200:
            check("探测后设备状态已更新", after.json().get("status") in ("online", "offline"),
                  after.json().get("status"))
    else:
        check("存在可探测设备", False, "数据库无设备")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        r404 = c.post("/api/devices/999999/probe", headers=H)
    check("探测不存在的设备返回 404", r404.status_code == 404, f"HTTP {r404.status_code}")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        r401 = c.post("/api/devices/1/probe")
    check("未认证访问返回 401", r401.status_code == 401, f"HTTP {r401.status_code}")

    # ---------------------------------------------------- 3. 批量探测接口
    print("\n[3] 批量探测接口")
    with httpx.Client(base_url=BASE, timeout=180, trust_env=False) as c:
        rb = c.post("/api/devices/probe/batch", headers=H, json={"concurrency": 8})
    check("批量探测返回 200", rb.status_code == 200, f"HTTP {rb.status_code}")
    if rb.status_code == 200:
        body = rb.json()
        check("返回总数", "total" in body, str(body.get("total")))
        check("返回在线数", "online" in body)
        check("返回离线数", "offline" in body)
        check("在线+离线=总数", body.get("online", 0) + body.get("offline", 0) == body.get("total"))
        check("返回逐台明细", isinstance(body.get("items"), list))
        if body.get("items"):
            first = body["items"][0]
            check("明细含设备名", "name" in first)
            check("明细含 IP", "ip" in first)
            check("明细含响应时间字段", "response_ms" in first)

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        r422 = c.post("/api/devices/probe/batch", headers=H, json={"concurrency": 999})
    check("非法并发数返回 422", r422.status_code == 422, f"HTTP {r422.status_code}")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        r401b = c.post("/api/devices/probe/batch", json={})
    check("批量探测未认证返回 401", r401b.status_code == 401, f"HTTP {r401b.status_code}")

    # 指定设备列表
    if items:
        ids = [i["id"] for i in items[:2]]
        with httpx.Client(base_url=BASE, timeout=180, trust_env=False) as c:
            rs = c.post("/api/devices/probe/batch", headers=H, json={"device_ids": ids})
        check("按指定 ID 列表探测", rs.status_code == 200 and rs.json().get("total") == len(ids),
              f"total={rs.json().get('total') if rs.status_code == 200 else rs.status_code}")

    # ---------------------------------------------------- 4. 前端页面
    print("\n[4] 设备管理页探测界面")
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

        page.goto(f"{BASE}/#/devices", wait_until="networkidle")
        page.wait_for_timeout(2500)

        body = page.inner_text("body")
        check("有「探测全部设备」按钮", "探测全部设备" in body)
        check("状态列有「探测」按钮", "探测" in body)

        # 单设备探测
        single_clicked = False
        try:
            # 状态列的探测按钮在表格行内
            page.locator(".el-table__row").first.locator("text=探测").first.click(timeout=5000)
            single_clicked = True
        except Exception:
            pass
        check("可点击单设备探测", single_clicked)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(SHOT_DIR / "devices-probe-single.png"))

        # 批量探测
        batch_clicked = False
        try:
            page.click("button:has-text('探测全部设备')", timeout=5000)
            batch_clicked = True
        except Exception:
            pass
        check("可点击批量探测", batch_clicked)
        page.wait_for_timeout(5000)

        dlg = page.inner_text("body")
        check("弹出探测结果对话框", "设备在线探测结果" in dlg)
        check("结果含在线统计", "在线" in dlg and "离线" in dlg)
        page.screenshot(path=str(SHOT_DIR / "devices-probe-batch.png"))

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


def asyncio_run(coro):
    """在同步脚本里跑协程。"""
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    raise SystemExit(main())
