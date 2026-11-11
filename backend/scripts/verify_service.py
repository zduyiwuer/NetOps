"""NetOps 后台启停脚本端到端验证。

覆盖：
  1. 脚本文件齐全且语法正确
  2. service_helper 各子命令行为正确
  3. 启动 → 状态 → 停止 → 状态 的完整生命周期
  4. PID 文件、日志文件按约定路径生成
  5. 重复启动有保护、停止后端口释放
  6. 停止后可再次启动（无残留残留）

运行：python -m scripts.verify_service
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PORT = int(os.environ.get("NETOPS_PORT", "8900"))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    """执行命令，返回退出码与输出（GBK/UTF-8 兼容解码）。

    批处理默认带 pause（双击时不闪退），自动化调用需设
    NETOPS_NO_PAUSE=1 跳过等待。
    """
    env = dict(os.environ)
    env.setdefault("NETOPS_NO_PAUSE", "1")
    try:
        r = subprocess.run(cmd, capture_output=True, cwd=str(ROOT), timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, "超时"
    raw = r.stdout or b""
    for enc in ("utf-8", "gbk"):
        try:
            out = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        out = raw.decode("utf-8", errors="replace")
    return r.returncode, out


def helper(*args: str, timeout: int = 120) -> int:
    """调用 service_helper，返回退出码。"""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.service_helper", *args],
        capture_output=True, cwd=str(BACKEND), timeout=timeout,
    )
    return r.returncode


def _find_bash() -> str:
    """定位 Git Bash。

    Windows 的 PATH 里可能有 WSL 存根 bash.exe，调用它会启动 WSL 并失败，
    必须显式找到 Git for Windows / PortableGit 的 bash。
    """
    from scripts.service_helper import find_bash

    path = find_bash()
    return path or "bash"


BASH = _find_bash()


def run_bash(script: str, timeout: int = 300) -> tuple[int, str]:
    """执行 shell 脚本。"""
    return run([BASH, script], timeout=timeout)


def main() -> int:
    print("\n=== NetOps 后台启停脚本验证 ===\n")

    # ---------------------------------------------------------- 1. 文件齐全
    print("[1] 脚本文件")
    scripts = {
        "start.sh": ROOT / "start.sh",
        "stop.sh": ROOT / "stop.sh",
        "restart.sh": ROOT / "restart.sh",
        "status.sh": ROOT / "status.sh",
        "start.bat": ROOT / "start.bat",
        "stop.bat": ROOT / "stop.bat",
        "restart.bat": ROOT / "restart.bat",
        "status.bat": ROOT / "status.bat",
        "service_helper.py": BACKEND / "scripts" / "service_helper.py",
        "winproc.py": BACKEND / "scripts" / "winproc.py",
    }
    for name, path in scripts.items():
        check(f"{name} 存在", path.exists(), str(path.relative_to(ROOT)))

    # shell 语法
    for name in ("start.sh", "stop.sh", "restart.sh", "status.sh"):
        path = ROOT / name
        if path.exists():
            r = subprocess.run([BASH, "-n", str(path)], capture_output=True, timeout=60)
            check(f"{name} 语法正确", r.returncode == 0, r.stderr.decode("utf-8", "replace")[:80])
            text = path.read_text(encoding="utf-8", errors="replace")
            if name in ("start.sh", "status.sh"):
                check(f"{name} 健康检查用组合判定（进程存活 + 端口可连）",
                      "service_helper healthy" in text)

    # bat 编码：cmd.exe 按 GBK 读取，含中文的批处理必须是 GBK + CRLF
    for name in ("start.bat", "stop.bat", "restart.bat", "status.bat"):
        path = ROOT / name
        if path.exists():
            raw = path.read_bytes()
            try:
                raw.decode("gbk")
                gbk_ok = True
            except UnicodeDecodeError:
                gbk_ok = False
            check(f"{name} 为 GBK 编码（cmd 可解析）", gbk_ok)
            check(f"{name} 使用 CRLF 换行", b"\r\n" in raw)
            text = raw.decode("gbk", errors="replace")
            # 双击运行时窗口会一闪而过 -> 必须有 pause 收尾
            check(f"{name} 含 pause 收尾（双击不闪退）", "pause >nul" in text)
            check(f"{name} 支持 NETOPS_NO_PAUSE 跳过暂停",
                  "NETOPS_NO_PAUSE" in text)
            # 启停类脚本才有健康检查；stop/restart 不需要
            if name in ("start.bat", "status.bat"):
                check(f"{name} 健康检查用组合判定（进程存活 + 端口可连）",
                      "service_helper healthy" in text)

    # ---------------------------------------------------------- 2. helper
    print("\n[2] service_helper 子命令")
    check("port-busy 空闲端口返回 1", helper("port-busy", "1") == 1)
    check("reachable 无服务返回 1", helper("reachable", "1") == 1)
    check("alive 不存在进程返回 1", helper("alive", "999999") == 1)
    check("alive 当前进程返回 0", helper("alive", str(os.getpid())) == 0)
    check("is-netops 对自身返回 1", helper("is-netops", str(os.getpid())) == 1)
    # healthy：必须进程存活 + 端口可连才算健康
    check("healthy 进程活但端口不通返回 1",
          helper("healthy", str(os.getpid()), "1") == 1)
    check("healthy 进程死返回 1",
          helper("healthy", "999999", "8900") == 1)

    # ---------------------------------------------------------- 3. 生命周期
    print("\n[3] 启停完整生命周期")
    # 先确保干净
    run_bash("stop.sh", timeout=120)

    rc, out = run_bash("start.sh", timeout=300)
    check("start.sh 执行成功", rc == 0, f"rc={rc}")
    check("启动输出含「启动成功」", "启动成功" in out, out.strip().splitlines()[-1][:70] if out else "")

    pid_file = BACKEND / "data" / "run" / "netops.pid"
    log_file = BACKEND / "data" / "logs" / "netops.log"
    check("PID 文件已生成", pid_file.exists(), str(pid_file))
    check("日志文件已生成", log_file.exists(), str(log_file))

    pid = ""
    if pid_file.exists():
        pid = pid_file.read_text(encoding="utf-8").strip()
        check("PID 文件内容为数字", pid.isdigit(), pid or "(空)")
        check("PID 进程存活", helper("alive", pid) == 0 if pid else False, f"PID {pid}")

    # 端口可达
    check("服务端口可连接", helper("reachable", str(PORT)) == 0, f"端口 {PORT}")

    # 状态
    rc, out = run_bash("status.sh", timeout=120)
    check("status.sh 返回 0", rc == 0, f"rc={rc}")
    check("status.sh 显示运行中", "运行中" in out, out.strip().splitlines()[1][:60] if len(out.splitlines()) > 1 else "")
    check("status.sh 显示 PID", pid and pid in out, pid)

    # 重复启动保护
    rc, out = run_bash("start.sh", timeout=300)
    check("重复启动被拦截", "已在运行" in out, out.strip().splitlines()[-1][:70] if out else "")

    # 停止
    rc, out = run_bash("stop.sh", timeout=180)
    check("stop.sh 执行成功", rc == 0, f"rc={rc}")
    check("停止后 PID 文件已删除", not pid_file.exists())
    check("停止后端口已释放", helper("reachable", str(PORT)) == 1, f"端口 {PORT}")

    # 停止后状态
    rc, out = run_bash("status.sh", timeout=120)
    check("停止后 status.sh 返回 1", rc == 1, f"rc={rc}")
    check("停止后显示未运行", "未运行" in out)

    # 再次启动（验证无残留）
    rc, out = run_bash("start.sh", timeout=300)
    check("停止后可再次启动", rc == 0 and "启动成功" in out, out.strip().splitlines()[-1][:70] if out else "")

    # 重启
    rc, out = run_bash("restart.sh", timeout=400)
    check("restart.sh 执行成功", rc == 0, f"rc={rc}")
    check("restart.sh 停止后重启", "启动成功" in out)

    # 清理
    run_bash("stop.sh", timeout=180)

    # ---------------------------------------------------------- 4. Windows
    print("\n[4] Windows 批处理（cmd.exe）")
    rc, out = run(["cmd.exe", "/c", "status.bat"], timeout=180)
    check("status.bat 可执行", rc in (0, 1), f"rc={rc}")
    check("status.bat 中文输出正常", "NetOps 服务状态" in out, out.strip()[:60])
    check("status.bat 未运行判定正确", "未运行" in out, out.strip().splitlines()[1][:50] if len(out.splitlines()) > 1 else "")

    rc, out = run(["cmd.exe", "/c", "start.bat"], timeout=400)
    check("start.bat 可执行", rc == 0, f"rc={rc}")
    check("start.bat 中文输出正常", "启动成功" in out, out.strip().splitlines()[-1][:70] if out else "")

    rc, out = run(["cmd.exe", "/c", "status.bat"], timeout=180)
    check("start.bat 后状态为运行中", "运行中" in out, out.strip().splitlines()[1][:50] if len(out.splitlines()) > 1 else "")

    rc, out = run(["cmd.exe", "/c", "stop.bat"], timeout=240)
    check("stop.bat 可执行", rc == 0, f"rc={rc}")
    check("stop.bat 后端口释放", helper("reachable", str(PORT)) == 1)

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
