"""Windows 进程命令行读取（纯 Win32 API，不依赖 PowerShell / WMIC）。

通过 NtQueryInformationProcess + PEB 读取目标进程的完整命令行，
用于判断某个 PID 是否为 NetOps 的 uvicorn 进程。

部分 Windows 版本 / 权限下读取失败会返回 None，调用方需容错。
"""
from __future__ import annotations

import ctypes
import os
from typing import List, Optional

if os.name == "nt":
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.ReadProcessMemory.restype = wintypes.BOOL
    _ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    _ntdll.NtQueryInformationProcess.restype = wintypes.LONG

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ExitStatus", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("AffinityMask", ctypes.c_void_p),
            ("BasePriority", ctypes.c_void_p),
            ("UniqueProcessId", ctypes.c_void_p),
            ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ]

    def _read_memory(handle, address: int, size: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = _kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(buf),
            ctypes.c_size_t(size),
            ctypes.byref(read),
        )
        if not ok or read.value == 0:
            return b""
        return bytes(buf[: read.value])

    def _read_utf16(handle, address: int, max_len: int = 4096) -> str:
        """从进程地址空间读取 UNICODE_STRING 的缓冲区。"""
        if not address:
            return ""
        raw = _read_memory(handle, address, max_len * 2)
        if not raw:
            return ""
        text = raw.decode("utf-16-le", errors="replace")
        return text.split("\x00", 1)[0]

    def _process_params_cmdline(pid: int) -> Optional[str]:
        """经 PEB → ProcessParameters → CommandLine 读取命令行。"""
        handle = _kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
        )
        if not handle:
            return None
        try:
            pbi = _PROCESS_BASIC_INFORMATION()
            ret_len = wintypes.ULONG(0)
            status = _ntdll.NtQueryInformationProcess(
                handle,
                0,
                ctypes.byref(pbi),
                ctypes.sizeof(pbi),
                ctypes.byref(ret_len),
            )
            if status != 0 or not pbi.PebBaseAddress:
                return None

            is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
            # PEB.ProcessParameters 偏移：64 位 0x20 / 32 位 0x10
            params_addr = int(pbi.PebBaseAddress) + (0x20 if is_64 else 0x10)
            raw = _read_memory(handle, params_addr, ctypes.sizeof(ctypes.c_void_p))
            if len(raw) < ctypes.sizeof(ctypes.c_void_p):
                return None
            params_ptr = int.from_bytes(raw, "little")

            # RTL_USER_PROCESS_PARAMETERS.CommandLine（UNICODE_STRING）在偏移 0x70
            # UNICODE_STRING: Length(2) + MaxLen(2) + Padding(4) + Buffer(8)
            us = _read_memory(handle, params_ptr + 0x70, 16)
            if len(us) < 16:
                return None
            length = int.from_bytes(us[0:2], "little")
            buffer_ptr = int.from_bytes(us[8:16], "little")
            if not buffer_ptr or length == 0:
                return None
            return _read_utf16(handle, buffer_ptr, max(1, length // 2)) or None
        finally:
            _kernel32.CloseHandle(handle)

    def process_cmdline(pid: int) -> Optional[str]:
        """获取进程命令行；失败返回 None。"""
        try:
            return _process_params_cmdline(pid)
        except Exception:
            return None

    def process_create_time(pid: int) -> int:
        """进程创建时间（Windows FILETIME 数值）。

        用于在多个同名进程里挑出**最新启动**的那一个。
        读不到返回 0。
        """
        try:
            handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return 0
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                ok = _kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
                if not ok:
                    return 0
                return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            finally:
                _kernel32.CloseHandle(handle)
        except Exception:
            return 0

    def list_python_pids() -> List[int]:
        """列出所有 python.exe / pythonw.exe 进程的 PID。"""
        import subprocess

        pids: List[int] = []
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=30,
            ).stdout.decode("utf-8", errors="replace")
        except Exception:
            return pids
        for line in out.splitlines():
            parts = [p.strip('" ') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
        return pids

else:  # pragma: no cover - 非 Windows 平台

    def process_cmdline(pid: int) -> Optional[str]:
        import subprocess

        try:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True, text=True, timeout=20,
            ).stdout
            return out.strip() or None
        except Exception:
            return None

    def list_python_pids() -> List[int]:
        import subprocess

        pids: List[int] = []
        try:
            out = subprocess.run(
                ["pgrep", "-f", "uvicorn"], capture_output=True, text=True, timeout=20
            ).stdout
        except Exception:
            return pids
        for token in out.split():
            if token.isdigit():
                pids.append(int(token))
        return pids
