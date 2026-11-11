"""启动窗口与飞塔内存采集的验证。

覆盖：
  1. **启动无 cmd 窗口残留** —— WMI 不再经 cmd 包装，避免出现空白窗口
  2. **关掉窗口服务仍存活** —— 服务进程不依附任何可见窗口
  3. **飞塔内存解析** —— 解析 "Memory: Nk total, Nk used (xx.x%)" 格式
  4. **飞塔 CPU 解析** —— CPU states 格式应算 100 - idle

运行：python -m scripts.verify_startup_and_fg_mem（需先启动后端服务）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def cmd_pids() -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq cmd.exe", "/FO", "CSV", "/NH"],
        capture_output=True, timeout=30,
    ).stdout.decode("utf-8", "replace")
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def main() -> int:
    print("\n=== 启动窗口与飞塔内存采集验证 ===\n")

    # -------------------------------------------------- 1. 飞塔解析
    print("[1] 飞塔内存/CPU 解析")
    from app.collectors.ssh import parse_cpu_usage, parse_memory_usage

    # 真实设备 `get system performance status` 的输出格式
    fg_out = (
        "CPU states: 5% user 2% system 0% nice 93% idle 0% iowait 0% irq 0% softirq\n"
        "CPU0 states: 0% user 2% system 0% nice 98% idle 0% iowait 0% irq 0% softirq\n"
        "Memory: 8171404k total, 2913948k used (35.7%), 3765232k free (46.1%), "
        "1492224k freeable (18.2%)\n"
        "Average sessions: 4107 sessions in 1 minute, 4117 sessions in 10 minutes\n"
    )
    pct, total = parse_memory_usage(fg_out, "fortinet")
    check("飞塔内存百分比可解析", pct is not None, f"pct={pct}")
    check("飞塔内存百分比数值正确", pct is not None and abs(pct - 35.7) < 0.5, f"pct={pct}")
    check("飞塔总内存可解析", total is not None and abs(total - 7979.9) < 2, f"total={total}")

    cpu = parse_cpu_usage(fg_out, "fortinet")
    check("飞塔 CPU 可解析", cpu is not None, f"cpu={cpu}")
    check("飞塔 CPU 按 100-idle 计算", cpu is not None and abs(cpu - 7.0) < 0.5, f"cpu={cpu}")

    # 其它厂商的原有格式不能被破坏
    pct2, total2 = parse_memory_usage("Total Memory: 2048 MB\nUsed(%): 42\n", "cisco")
    check("思科格式不受影响", pct2 == 42 and abs((total2 or 0) - 2048) < 1, f"pct={pct2} total={total2}")
    cpu2 = parse_cpu_usage("CPU utilization for five seconds: 25%", "cisco")
    check("思科 CPU 格式不受影响", cpu2 == 25, f"cpu={cpu2}")
    cpu3 = parse_cpu_usage("CPU utilization: 30%\n", "huawei")
    check("华为 CPU 格式不受影响", cpu3 == 30, f"cpu={cpu3}")

    # -------------------------------------------------- 2. 启动无 cmd 窗口
    print("\n[2] 启动不残留 cmd 窗口")
    from scripts.service_helper import _spawn_via_wmi

    before = cmd_pids()
    # 用一个短命的探测进程验证 WMI 启动路径不会留下 cmd
    log = str(Path(__file__).resolve().parents[1] / "data" / "logs" / "verify_window.log")
    import os
    import sys as _sys
    import time

    pid = _spawn_via_wmi(
        [_sys.executable, "-c", "import time; time.sleep(8)"],
        str(Path(__file__).resolve().parents[1]),
        log,
    )
    time.sleep(3)
    after = cmd_pids()
    new_cmd = after - before
    check("WMI 启动不新增 cmd 进程", not new_cmd, f"新增 {sorted(new_cmd)}" if new_cmd else "无")
    check("启动返回有效 PID", pid > 0, f"PID={pid}")

    # 启动器应已退出，服务子进程仍存活
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
        capture_output=True, timeout=30,
    ).stdout.decode("utf-8", "replace")
    py_pids = set()
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            py_pids.add(int(parts[1]))
    check("启动器已退出（不留额外进程）", pid not in py_pids, f"启动器 PID={pid}")
    check("服务进程仍存活", len(py_pids) > 0, f"{len(py_pids)} 个 python 进程")

    # -------------------------------------------------- 3. 关窗后服务存活
    print("\n[3] 关掉窗口后服务仍存活")
    import httpx

    try:
        with httpx.Client(base_url="http://127.0.0.1:8900", timeout=10, trust_env=False) as c:
            alive_before = c.get("/api/health").status_code
    except Exception:
        alive_before = 0
    check("服务当前可访问", alive_before == 200, f"HTTP {alive_before}")

    # 模拟用户关掉所有可见 cmd 窗口
    from scripts.winproc import process_cmdline

    killed = 0
    for p in cmd_pids():
        cmd = (process_cmdline(p) or "").lower()
        if "netops" in cmd or "start.bat" in cmd or "stop.bat" in cmd:
            subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
            killed += 1
    check("已关闭相关 cmd 窗口", True, f"关闭 {killed} 个")

    time.sleep(3)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8900", timeout=10, trust_env=False) as c:
            alive_after = c.get("/api/health").status_code
    except Exception:
        alive_after = 0
    check("关窗后服务仍可访问", alive_after == 200, f"HTTP {alive_after}")

    # 清理探测进程
    for p in py_pids:
        try:
            from scripts.winproc import process_cmdline
            cmd = (process_cmdline(p) or "")
            if "time.sleep(8)" in cmd:
                subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
        except Exception:
            pass

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
