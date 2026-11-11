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

    Windows 优先用 **WMI Win32_Process.Create**：
    进程由 WMI 服务创建，完全脱离调用方的进程树与会话，
    父进程退出后依然存活——这是最可靠的「后台常驻」方式。
    失败时回退到 DETACHED_PROCESS 分离标志。

    Linux / macOS 使用 start_new_session=True 创建新会话。
    """
    if os.name == "nt":
        pid = _spawn_via_wmi(cmd, cwd, log_file)
        if pid:
            return pid
        return _spawn_via_detach_flags(cmd, cwd, log_file)

    out = open(log_file, "ab", buffering=0)
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


def _spawn_via_wmi(cmd: list[str], cwd: str, log_file: str) -> int:
    """通过 WMI Win32_Process.Create 启动进程，返回 PID（失败返回 0）。

    WMI 由系统服务托管，创建的进程不属于调用方的作业对象/会话，
    因此父进程（终端、脚本、工具）退出都不会带走它。

    **不经过 cmd 包装**：``cmd /c "python ..."`` 会在桌面留下一个可见的
    cmd 窗口（输出虽重定向到日志，窗口本身仍在），而且真正的服务进程是
    cmd 的直接子进程——用户关掉那个窗口，服务就跟着停了。

    这里改为直接创建 ``service_launcher.py``：
    启动器再以 DETACHED_PROCESS | CREATE_NO_WINDOW 拉起真正的服务进程，
    自己立即退出。于是既没有多余窗口，服务进程也彻底脱离进程树。

    注意：WMI 启动的进程环境是**系统 PATH**，可能与当前解释器不同
    （例如当前用的是虚拟环境里的 python，WMI 却找到系统 python）。
    因此这里把解释器换成当前正在运行的这个绝对路径。
    """
    try:
        import json
        import tempfile

        # 解释器换成绝对路径，确保用的是当前这个（依赖已装好的）
        parts = list(cmd)
        if parts and os.path.basename(parts[0]).lower().startswith("python"):
            parts[0] = sys.executable

        launcher = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "service_launcher.py"
        )
        if not os.path.exists(launcher):
            return 0

        # 用临时文件传参，避免命令行引号地狱
        fd, spec_path = tempfile.mkstemp(suffix=".json", prefix="netops_spawn_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "cwd": cwd,
                        "log": log_file,
                        "pidfile": spec_path + ".pid",
                        "args": parts,
                    },
                    fh,
                )
            command_line = f'"{sys.executable}" "{launcher}" "{spec_path}"'
            ps = (
                "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                f"-Arguments @{{CommandLine='{_escape_ps(command_line)}'}} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, timeout=60,
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
            pid = 0
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    break
            if not pid:
                return 0

            # 启动器会立刻退出，等它退出后再回收临时文件
            import time

            for _ in range(20):
                if not pid_alive(pid):
                    break
                time.sleep(0.1)
            return pid
        finally:
            for p in (spec_path, spec_path + ".pid"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
    except Exception:
        return 0


def _escape_ps(text: str) -> str:
    """转义 PowerShell 单引号字符串里的单引号。"""
    return text.replace("'", "''")


def _quote_win(path: str) -> str:
    """Windows 命令行路径加引号（含空格时必需）。"""
    if not path:
        return '""'
    if " " in path or "\t" in path:
        return f'"{path}"'
    return path


def _spawn_via_detach_flags(cmd: list[str], cwd: str, log_file: str) -> int:
    """用 DETACHED_PROCESS 等分离标志启动进程（备用方案）。"""
    out = open(log_file, "ab", buffering=0)
    flags = 0
    flags |= 0x00000008  # DETACHED_PROCESS
    flags |= 0x00000200  # CREATE_NEW_PROCESS_GROUP
    flags |= 0x08000000  # CREATE_NO_WINDOW
    # CREATE_BREAKAWAY_FROM_JOB：允许进程脱离父进程的作业对象
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

    同时清洗入参：bat 的 ``for /f`` 捕获的输出可能残留 ``\\r``，
    直接写进 PID 文件会导致后续读取出错。
    """
    clean = str(pid).strip().replace("\r", "").replace("\n", "").strip()
    if not clean.isdigit():
        raise ValueError(f"非法 PID: {pid!r}")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(clean)


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


def candidate_service_pids(port: int) -> List[dict]:
    """列出命令行含 ``--port <端口>`` 的 python 进程及其创建时间。

    用 CIM（Win32_Process）一次性取全，带 CreationDate——
    这样能准确区分「新启动的服务」与「上一轮遗留的进程」，
    避免把死 PID 写进 PID 文件。
    """
    out: List[dict] = []
    if os.name != "nt":
        return out
    ps = (
        "Get-CimInstance -ClassName Win32_Process | "
        "Where-Object { $_.Name -like 'python*' -and "
        f"$_.CommandLine -like '*--port {port}*' }} | "
        "ForEach-Object { Write-Output ($_.ProcessId.ToString() + '|' + "
        "$_.CreationDate.ToString('o')) }"
    )
    try:
        raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=45)
    except Exception:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid_s, _, ctime_s = line.partition("|")
        pid_s = pid_s.strip()
        if not pid_s.isdigit():
            continue
        # CreationDate 是 ISO 字符串，转成可比较的数值
        try:
            from datetime import datetime

            ctime = datetime.fromisoformat(ctime_s.strip().replace("Z", "+00:00")).timestamp()
        except Exception:
            ctime = 0.0
        out.append({"pid": int(pid_s), "ctime": ctime})
    return out


def listener_pid(port: int) -> int:
    """找出真正**监听**指定端口的进程 PID（Windows）。

    判定顺序：
    1. **CIM 按命令行查**（``Get-CimInstance Win32_Process``）——
       在部分环境下 netstat 的 LISTENING 行会报 PID=0
       （WMI 创建的进程尤其如此），CIM 则能稳定拿到真实 PID；
    2. netstat 作为备选。

    多个候选时取**创建时间最新**的那个，避免认到上一轮遗留的进程。
    返回的 PID 必须仍然存活，否则返回 0。
    """
    if os.name != "nt":
        return 0

    # 1) CIM 按命令行匹配（可靠），取最新的
    cands = candidate_service_pids(port)
    alive = [c for c in cands if pid_alive(c["pid"])]
    if alive:
        alive.sort(key=lambda c: c["ctime"])
        return alive[-1]["pid"]

    # 2) netstat 备选
    try:
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
            if pid.isdigit() and int(pid) != 0 and pid_alive(int(pid)):
                return int(pid)
    except Exception:
        pass
    return 0


def find_service_pid(port: int, timeout: float = 30.0, stable_rounds: int = 2) -> int:
    """找出监听指定端口的 NetOps uvicorn 进程 PID。

    判定顺序：
    1. **端口监听者**（``listener_pid``）——最权威，端口的监听者就是服务本身；
    2. 命令行匹配（``--port <端口>`` + uvicorn）作为兜底，
       且要求候选**连续多轮都存在**——服务启动过程中会有临时进程闪现又退出，
       只出现一次的不能认。

    关键是**先等端口可连接**再判定：端口通了说明服务已就绪，
    此时反查出来的 PID 才是真正在服务的那一个。
    """
    import time

    deadline = time.time() + timeout
    seen: dict[int, int] = {}  # pid -> 连续出现次数
    best_pid = 0
    best_time = -1

    while time.time() < deadline:
        # 端口还没通就继续等（服务尚未就绪，此时反查容易认到旧进程）
        if not port_reachable(port):
            time.sleep(0.5)
            continue

        # 1) 先看端口监听者
        lp = listener_pid(port)
        if lp and pid_alive(lp):
            return lp

        # 2) 命令行匹配兜底
        try:
            from scripts.winproc import list_python_pids, process_cmdline, process_create_time

            needle = f"--port {port}"
            current: dict[int, int] = {}
            for pid in list_python_pids():
                cmd = (process_cmdline(pid) or "").lower()
                if "uvicorn" not in cmd or needle not in cmd:
                    continue
                current[pid] = process_create_time(pid)

            for pid in list(seen):
                if pid not in current:
                    seen.pop(pid, None)
            for pid in current:
                seen[pid] = seen.get(pid, 0) + 1

            for pid, count in seen.items():
                if count < stable_rounds:
                    continue
                ctime = current.get(pid, 0)
                # 读不到创建时间（0）时不要抢占已有更优候选
                if ctime and ctime < best_time:
                    continue
                if ctime >= best_time:
                    best_time = ctime
                    best_pid = pid
            if best_pid:
                return best_pid
        except Exception:
            pass
        time.sleep(0.5)
    return best_pid


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]

    # bat 的 `for /f` 捕获的命令输出会残留 \r，
    # 所有参数统一清洗一遍，避免 PID 之类的数值带上杂字符
    argv = [a.strip().replace("\r", "").replace("\n", "") for a in sys.argv]

    # 无参数类子命令
    if cmd == "bash-path":
        path = find_bash()
        if path:
            print(path)
            return 0
        return 1
    if len(argv) < 3:
        print(__doc__)
        return 2
    try:
        if cmd == "find-pid":
            port = int(argv[2])
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
            port = int(argv[2])
            return 0 if port_busy(port) else 1
        if cmd == "reachable":
            port = int(argv[2])
            return 0 if port_reachable(port) else 1
        if cmd == "alive":
            pid = int(argv[2])
            return 0 if pid_alive(pid) else 1
        if cmd == "is-netops":
            pid = int(argv[2])
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
            pid = int(argv[2])
            port = int(argv[3])
            return 0 if service_healthy(pid, port) else 1
        if cmd == "start-service":
            # start-service <log_file> <cwd> <pid_file> <port> <cmd...>
            # 一步完成：启动后台进程 + 反查真实 PID + 写 PID 文件
            # 全部在 Python 内部完成，避免 bat 层的变量展开/编码问题
            log_file = argv[2]
            cwd = argv[3]
            pid_file = argv[4]
            port = int(argv[5])
            args = argv[6:]
            if not args:
                return 2
            spawn_detached(args, cwd, log_file)
            real_pid = find_service_pid(port, timeout=60)
            if not real_pid:
                print("无法反查服务进程 PID", file=sys.stderr)
                return 1
            try:
                write_pid_file(pid_file, real_pid)
            except (ValueError, OSError) as exc:
                print(f"写入 PID 文件失败: {exc}", file=sys.stderr)
                return 1
            print(real_pid)
            return 0
        if cmd == "write-pid":
            # write-pid <path> <pid>
            try:
                write_pid_file(argv[2], int(str(argv[3]).strip().replace("\r", "")))
            except (ValueError, IndexError) as exc:
                print(f"写入 PID 文件失败: {exc}", file=sys.stderr)
                return 1
            return 0
        if cmd == "read-pid":
            # read-pid <path>
            pid = read_pid_file(argv[2])
            if pid:
                print(pid)
                return 0
            return 1
        if cmd == "kill":
            # kill <pid> [--force]
            pid = int(argv[2])
            force = "--force" in argv[3:]
            kill_pid(pid, force)
            return 0
        if cmd == "kill-netops-on-port":
            # 清理监听该端口的 NetOps 残留进程
            port = int(argv[2])
            for pid in pids_by_port(port):
                if is_netops_process(pid):
                    if kill_pid(pid, force=True):
                        print(pid)
            return 0
        if cmd == "spawn":
            # spawn <log_file> <cwd> <port> <cmd...>
            # 返回真正运行服务的进程 PID（而非 WMI 的 cmd 包装进程）
            log_file = argv[2]
            cwd = argv[3]
            port = int(argv[4])
            args = argv[5:]
            if not args:
                return 2
            spawn_detached(args, cwd, log_file)
            # 反查真正的 uvicorn 进程 PID
            real_pid = find_service_pid(port, timeout=40)
            if not real_pid:
                # 反查失败时退而求其次：至少把包装进程 PID 返回去
                print(0)
                return 1
            print(real_pid)
            return 0
    except (ValueError, IndexError):
        return 2
    print(f"未知命令：{cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())