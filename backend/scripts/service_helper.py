"""NetOps 服务管理辅助：跨平台的 PID 查找、端口检测、进程存活检查。

供 start/stop/status 脚本调用，避免在 shell 与批处理里重复实现
易出错的就地逻辑（尤其是 Windows 上按端口反查 PID）。

用法：
    python -m scripts.service_helper find-pid 8900     # 按监听端口找 PID
    python -m scripts.service_helper port-busy 8900    # 端口是否被占用（0=占用）
    python -m scripts.service_helper alive 12345       # 进程是否存活（0=存活）
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否已被占用。

    用「能否连上」判断，而不是尝试绑定——
    Windows 上带 SO_REUSEADDR 的 bind 即使端口被占用也会成功，
    会造成误判（以为空闲，实际已被别的服务占用）。
    """
    return port_reachable(port, host, timeout=1.5)


def port_reachable(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    """能否连上该端口（用于健康检查）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(cmd: list[str], timeout: int = 20) -> str:
    """执行命令并返回输出文本。

    Windows 下的 tasklist/netstat 输出是 GBK/本地代码页，
    强制按 UTF-8 解码会抛 UnicodeDecodeError，因此这里用容错解码。
    """
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=timeout
        )
    except Exception:
        return ""
    raw = out.stdout or b""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def pid_alive(pid: int) -> bool:
    """进程是否存活。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows：用 tasklist 按 PID 查询
        # 输出形如：System    4    Services    0    10,092 K
        # 不能用 "str(pid) in out" 判断——PID 4 会命中 "10,092" 里的数字
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=15)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def pids_by_port(port: int) -> list[int]:
    """找出监听指定端口的进程 PID。

    Windows 上 netstat 在部分环境（如 Git Bash 启动的进程）不输出
    LISTENING 行，因此以 netstat 结果为主、进程表扫描为辅。
    """
    pids: list[int] = []
    if os.name == "nt":
        out = _run(["netstat", "-ano"], timeout=30)
        for line in out.splitlines():
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]
            if not local.rsplit(":", 1)[-1].isdigit():
                continue
            if int(local.rsplit(":", 1)[-1]) != port:
                continue
            pid = parts[-1]
            if pid.isdigit():
                pids.append(int(pid))
        if pids:
            return sorted(set(pids))

        # netstat 不可靠：回退到扫描 NetOps 进程，并校验其是否占用该端口
        candidates = find_netops_pids()
        if candidates and port_reachable(port):
            return candidates
        return []

    # Linux / macOS：优先用 ss，其次 lsof，最后 fuser
    for cmd in (
        ["ss", "-lptnH", f"sport = :{port}"],
        ["netstat", "-lptn"],
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
    ):
        out = _run(cmd, timeout=20)
        if not out.strip():
            continue
        for token in out.replace("(", " ").replace(")", " ").replace("/", " ").split():
            if token.isdigit():
                pids.append(int(token))
        if pids:
            break
    return sorted(set(pids))


def is_netops_process(pid: int) -> bool:
    """判断进程是否为 NetOps 的 uvicorn（避免误杀其他程序）。

    用 scripts.winproc 的纯 Win32 API 读命令行，
    不依赖 PowerShell / WMIC（新版 Windows 已移除 WMIC，
    且从 Bash 调 PowerShell 会被安全策略拦截）。
    """
    try:
        from scripts.winproc import process_cmdline

        cmd = process_cmdline(pid)
        if not cmd:
            return False
        low = cmd.lower()
        return "uvicorn" in low and "app.main" in low
    except Exception:
        return False


def find_netops_pids() -> list[int]:
    """列出所有 NetOps 的 uvicorn 进程 PID。"""
    try:
        from scripts.winproc import list_python_pids, process_cmdline

        pids: list[int] = []
        for pid in list_python_pids():
            cmd = (process_cmdline(pid) or "").lower()
            if "uvicorn" in cmd and "app.main" in cmd:
                pids.append(pid)
        return pids
    except Exception:
        return []


def _process_cmdline_safe(pid: int) -> str:
    """安全读取进程命令行，失败返回空串。"""
    try:
        from scripts.winproc import process_cmdline

        return process_cmdline(pid) or ""
    except Exception:
        return ""


def spawn_detached(cmd: list[str], cwd: str, log_file: str) -> int:
    """启动一个脱离终端的后台进程，返回 PID。

    Windows 使用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW，
    让进程不依附当前控制台，父 shell 退出后仍继续运行；
    Linux / macOS 使用 start_new_session=True 创建新会话。
    """
    out = open(log_file, "ab", buffering=0)
    if os.name == "nt":
        flags = 0
        flags |= 0x00000008  # DETACHED_PROCESS
        flags |= 0x00000200  # CREATE_NEW_PROCESS_GROUP
        flags |= 0x08000000  # CREATE_NO_WINDOW
        # CREATE_BREAKAWAY_FROM_JOB：允许进程脱离父进程的作业对象，
        # 这样即便启动方（如 IDE / CI / 终端）被整体清理，服务仍存活。
        # 作业对象不允许 breakaway 时该标志会被忽略，不影响启动。
        flags |= 0x01000000
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
        except OSError:
            # breakaway 不被允许时回退到不带该标志的启动方式
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags & ~0x01000000,
                close_fds=True,
            )
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid


def kill_pid(pid: int, force: bool = False) -> bool:
    """终止进程，返回是否成功发出终止请求。

    - Windows：``taskkill`` 只有加 ``/F`` 才能真正结束进程
      （不带 /F 会返回「无法终止，只有 /F 才能强制终止」），
      因此 Windows 上统一带 /F，避免无谓等待 10 秒超时。
    - Linux / macOS：未指定 force 时发 SIGTERM 走优雅退出，
      超时后由调用方用 force 模式发 SIGKILL。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            return False
        return True
    try:
        os.kill(pid, 9 if force else 15)
        return True
    except OSError:
        return False
    except Exception:
        return False


def find_bash() -> Optional[str]:
    """定位可用的 bash 可执行文件。

    Windows 上 PATH 里可能有多个 bash：
    - ``C:\\Windows\\System32\\bash.exe`` / WindowsApps 下的
      是 **WSL 存根**，会尝试启动 WSL 并失败；
    - Git for Windows / PortableGit 的 bash 才是我们要的。

    因此优先找 Git Bash，找不到再退回 PATH 里的 bash。
    """
    import glob
    import shutil

    if os.name != "nt":
        return shutil.which("bash")

    candidates: List[str] = []
    # PortableGit / Git for Windows 常见安装位置
    patterns = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files\WorkBuddy\resources\vendor\PortableGit\usr\bin\bash.exe",
    ]
    for base in (
        os.path.expanduser(r"~\.workbuddy\binaries\PortableGit"),
        os.path.expanduser(r"~\AppData\Local\Programs\Git"),
    ):
        patterns.append(os.path.join(base, "*", "bin", "bash.exe"))
        patterns.append(os.path.join(base, "*", "usr", "bin", "bash.exe"))

    for pat in patterns:
        for hit in glob.glob(pat):
            if hit.lower().endswith("bash.exe"):
                candidates.append(hit)

    for hit in candidates:
        if os.path.isfile(hit):
            return hit

    found = shutil.which("bash")
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    return None


def write_pid_file(path: str, pid: int) -> None:
    """写 PID 文件。

    统一用 LF 换行——Windows 的 bat 用 ``for /f`` 读取时，
    若文件是 CRLF 会把尾部的 ``\\r`` 一并读进变量，
    导致 taskkill 拿到错误 PID。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(str(pid))


def read_pid_file(path: str) -> int:
    """读 PID 文件，容错空白与非法内容。"""
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return 0
    text = text.strip().replace("\r", "").replace("\n", "").strip()
    return int(text) if text.isdigit() else 0


def service_healthy(pid: int, port: int) -> bool:
    """服务是否真正健康。

    必须**同时**满足：
    1. 目标进程还活着；
    2. 端口能连上。

    只查端口会被「端口上有别的服务」或「连接残留」误导，
    出现「报告启动成功、实际进程已死」的假成功。
    """
    if pid and not pid_alive(pid):
        return False
    return port_reachable(port)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    # 无参数类子命令
    if cmd == "bash-path":
        path = find_bash()
        if path:
            print(path)
            return 0
        return 1
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    try:
        if cmd == "find-pid":
            port = int(sys.argv[2])
            # 先按监听端口找，再按命令行匹配兜底
            for pid in pids_by_port(port):
                if is_netops_process(pid):
                    print(pid)
                    return 0
            # 兜底：扫描 NetOps 进程，取命令行里端口匹配的那个
            for pid in find_netops_pids():
                cmdline = _process_cmdline_safe(pid)
                if f":{port}" in cmdline or f" {port}" in cmdline:
                    print(pid)
                    return 0
            return 1
        if cmd == "port-busy":
            port = int(sys.argv[2])
            return 0 if port_busy(port) else 1
        if cmd == "reachable":
            port = int(sys.argv[2])
            return 0 if port_reachable(port) else 1
        if cmd == "alive":
            pid = int(sys.argv[2])
            return 0 if pid_alive(pid) else 1
        if cmd == "is-netops":
            pid = int(sys.argv[2])
            return 0 if is_netops_process(pid) else 1
        if cmd == "bash-path":
            path = find_bash()
            if path:
                print(path)
                return 0
            return 1
        if cmd == "bash-path":
            path = find_bash()
            if path:
                print(path)
                return 0
            return 1
        if cmd == "healthy":
            # healthy <pid> <port>
            pid = int(sys.argv[2])
            port = int(sys.argv[3])
            return 0 if service_healthy(pid, port) else 1
        if cmd == "write-pid":
            # write-pid <path> <pid>
            write_pid_file(sys.argv[2], int(sys.argv[3]))
            return 0
        if cmd == "read-pid":
            # read-pid <path>
            pid = read_pid_file(sys.argv[2])
            if pid:
                print(pid)
                return 0
            return 1
        if cmd == "kill":
            # kill <pid> [--force]
            pid = int(sys.argv[2])
            force = "--force" in sys.argv[3:]
            kill_pid(pid, force)
            return 0
        if cmd == "kill-netops-on-port":
            # 清理监听该端口的 NetOps 残留进程
            port = int(sys.argv[2])
            for pid in pids_by_port(port):
                if is_netops_process(pid):
                    if kill_pid(pid, force=True):
                        print(pid)
            return 0
        if cmd == "spawn":
            # spawn <log_file> <cwd> <cmd...>
            log_file = sys.argv[2]
            cwd = sys.argv[3]
            args = sys.argv[4:]
            if not args:
                return 2
            print(spawn_detached(args, cwd, log_file))
            return 0
    except (ValueError, IndexError):
        return 2
    print(f"未知命令：{cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
