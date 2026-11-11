"""后台服务启动器。

由 WMI（``Win32_Process.Create``）直接创建本脚本——**不经过 cmd 包装**。

为什么需要它：
  1. ``cmd /c "python ..."`` 会在桌面留下一个**可见的 cmd 窗口**
     （输出虽重定向到日志，窗口本身仍在）；
     而 uvicorn 是 cmd 的直接子进程，用户关掉那个窗口，服务就跟着没了。
  2. 本脚本以 python 进程身份被创建，不产生 cmd 窗口；
     再由它用 ``DETACHED_PROCESS | CREATE_NO_WINDOW`` 拉起真正的服务进程，
     脚本自身立即退出——服务进程随即成为独立进程，
     与启动它的终端 / 脚本 / 工具的进程树彻底脱钩。

用法：``python service_launcher.py <spec.json>``
spec.json 含 cwd / log / pidfile / args。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
DETACHED_FLAGS = 0x00000008 | 0x00000200 | 0x08000000


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python service_launcher.py <spec.json>", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            spec = json.load(fh)
    except Exception as exc:
        print(f"读取启动配置失败: {exc}", file=sys.stderr)
        return 1

    cwd = spec.get("cwd") or os.getcwd()
    log_path = spec.get("log")
    pid_file = spec.get("pidfile")
    args = spec.get("args") or []
    if not args:
        print("启动配置缺少 args", file=sys.stderr)
        return 1

    try:
        os.chdir(cwd)
    except OSError as exc:
        print(f"切换工作目录失败: {exc}", file=sys.stderr)
        return 1

    # 日志重定向：stdout/stderr 指向日志文件，stdin 丢弃
    out = open(log_path, "ab", buffering=0) if log_path else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=DETACHED_FLAGS,
            close_fds=True,
        )
    except Exception as exc:
        print(f"启动服务进程失败: {exc}", file=sys.stderr)
        return 1

    if pid_file:
        try:
            with open(pid_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(str(proc.pid))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
