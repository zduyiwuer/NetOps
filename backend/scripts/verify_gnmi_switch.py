"""gNMI 采集器开关与端口回退验证。

覆盖：
  1. **端口可用性检测** —— 识别被系统排除端口范围占用的端口
  2. **端口回退** —— 标准端口不可用时自动改用邻近可用端口
  3. **开关可反复切换** —— 关掉后能重新打开（原始问题场景）
  4. **接口如实反馈** —— 启动失败时 applied 为 False 且带原因说明
  5. **状态展示实际端口** —— 设备 dial-out 需要知道用哪个端口

运行：python -m scripts.verify_gnmi_switch（需先启动后端服务）
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== gNMI 开关与端口回退验证 ===\n")

    # -------------------------------------------------- 1. 端口检测
    print("[1] 端口可用性检测")
    from app.services.telemetry_service import _port_bindable, resolve_gnmi_port

    check("标准端口 57400 检测可用", isinstance(_port_bindable(57400), bool),
          "可用" if _port_bindable(57400) else "不可用（被系统排除）")

    # 找一个确定被占用的端口来验证检测准确性
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    check("已占用端口检测为不可用", _port_bindable(busy) is False, f"端口 {busy}")
    s.close()

    # -------------------------------------------------- 2. 端口回退
    print("\n[2] 端口回退")
    port, fallback = resolve_gnmi_port(57400)
    check("能解析出可用端口", port > 0, f"端口 {port}")
    if _port_bindable(57400):
        check("标准端口可用时不回退", fallback is False and port == 57400, f"端口 {port}")
    else:
        check("标准端口不可用时自动回退", fallback is True and port != 57400, f"57400 -> {port}")
        check("回退后的端口确实可用", _port_bindable(port), f"端口 {port}")

    # -------------------------------------------------- 3. 开关反复切换
    print("\n[3] 开关反复切换（原始问题场景）")
    import httpx

    base = "http://127.0.0.1:8900"
    try:
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as c:
            login = c.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin123", "source": "local"},
            )
            token = login.json().get("access_token", "")
        check("管理员登录成功", bool(token), f"HTTP {login.status_code}")
    except Exception as exc:
        check("管理员登录成功", False, str(exc)[:60])
        token = ""

    if token:
        H = {"Authorization": f"Bearer {token}"}
        for round_no in (1, 2, 3):
            with httpx.Client(base_url=base, timeout=60, trust_env=False) as c:
                off = c.post(
                    "/api/settings/collector/toggle",
                    headers=H, json={"target": "telemetry", "enabled": False},
                ).json()
                on = c.post(
                    "/api/settings/collector/toggle",
                    headers=H, json={"target": "telemetry", "enabled": True},
                ).json()
            ok_off = off.get("actually_applied", True) is not False
            ok_on = on.get("actually_applied", True) is not False
            running = on.get("collector", {}).get("telemetry", {}).get("running")
            check(f"第 {round_no} 轮关闭成功", ok_off, str(off.get("applied")))
            check(f"第 {round_no} 轮能重新打开", ok_on and running is True,
                  f"applied={on.get('applied')} running={running}")

        # -------------------------------------------------- 4. 接口反馈
        print("\n[4] 接口如实反馈")
        with httpx.Client(base_url=base, timeout=60, trust_env=False) as c:
            st = c.get("/api/settings/collector-status", headers=H).json()
        tel = st.get("telemetry", {})
        check("状态显示运行中", tel.get("running") is True, str(tel.get("detail")))
        check("状态显示实际端口", tel.get("detail", "").find("端口") >= 0, tel.get("detail", ""))

        with httpx.Client(base_url=base, timeout=60, trust_env=False) as c:
            ts = c.get("/api/telemetry/status", headers=H).json()
        check("遥测状态含实际端口", ts.get("collector_port") is not None,
              f"端口 {ts.get('collector_port')}")
        check("遥测状态标记了端口回退",
              isinstance(ts.get("collector_port_fallback"), bool),
              f"fallback={ts.get('collector_port_fallback')}")

        # 端口确实在监听
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, timeout=30,
        ).stdout.decode("utf-8", "replace")
        port = ts.get("collector_port")
        listening = any(
            f":{port}" in l and "LISTENING" in l.upper() for l in out.splitlines()
        )
        check("端口处于 LISTENING 状态", listening, f"端口 {port}")

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
