"""验证：端口被占用 / 不可用时，系统日志能记录下来。

做法：先用一个 socket 占住 gNMI 端口，再触发 gNMI 重启，
此时绑定必然失败（Address already in use），
应当产生一条 error 级别的系统日志，并带上诊断信息。
"""
from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

import httpx

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    if not token:
        print("登录失败")
        return 1
    H = {"Authorization": f"Bearer {token}"}

    # 找一个当前可用的端口并**真正占住**它
    # 注意：不设 SO_REUSEADDR——Windows 上该标志允许抢占已绑定的端口，
    # 设了就占不住，测不出「端口被占用」的真实场景
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    busy_port = blocker.getsockname()[1]
    print(f"已占用端口 {busy_port}")

    try:
        # 把 gNMI 端口改成被占用的端口，然后触发重启
        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            c.put(
                "/api/settings",
                headers=H,
                json={"collector.gnmi_port": busy_port},
            )
        time.sleep(2)

        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            r = c.post(
                "/api/settings/collector/toggle",
                headers=H, json={"target": "telemetry", "enabled": False},
            )
            time.sleep(1)
            r = c.post(
                "/api/settings/collector/toggle",
                headers=H, json={"target": "telemetry", "enabled": True},
            )
        print("切换结果:", r.json().get("applied"))

        time.sleep(2)
        # 查询系统日志
        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            logs = c.get(
                "/api/logs/system", headers=H, params={"hours": 1, "page_size": 20}
            ).json()

        print(f"\n=== 系统日志 {logs['total']} 条 ===")
        hit = None
        for i in logs["items"]:
            print(f"  [{i['level']:8}] [{i['category']:9}] {i['source']:20}")
            print(f"      {i['message'][:100]}")
            if i["detail"]:
                print(f"      详情: {i['detail'][:130]}")
            if i["context"]:
                print(f"      上下文: {i['context']}")
            if str(busy_port) in i["message"]:
                hit = i
                break

        print("\n--- 断言 ---")
        check("端口不可用时写入了系统日志", hit is not None,
              hit["message"][:80] if hit else "未找到相关记录")
        if hit:
            check("级别为 warning/error", hit["level"] in ("warning", "error"), hit["level"])
            check("分类为 port", hit["category"] == "port", hit["category"])
            check("消息含实际回退端口", str(busy_port) in hit["message"], hit["message"][:80])
            check("上下文记录了首选与实际端口",
                  bool(hit["context"].get("actual_port")),
                  str(hit["context"]))
            check("同一次问题只记一条",
                  sum(1 for i in logs["items"] if str(busy_port) in i["message"]) == 1,
                  f"{sum(1 for i in logs['items'] if str(busy_port) in i['message'])} 条")

        passed = sum(1 for _, c, _ in results if c)
        print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
        for name, ok, detail in results:
            if not ok:
                print(f"  未通过: {name} {detail}")
        return 0 if passed == len(results) else 1
    finally:
        blocker.close()
        # 恢复默认端口并重启 gNMI
        try:
            with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
                c.put("/api/settings", headers=H, json={"collector.gnmi_port": 57400})
                c.post(
                    "/api/settings/collector/toggle",
                    headers=H, json={"target": "telemetry", "enabled": False},
                )
                time.sleep(1)
                c.post(
                    "/api/settings/collector/toggle",
                    headers=H, json={"target": "telemetry", "enabled": True},
                )
            print("\n已恢复默认端口")
        except Exception as exc:
            print("恢复失败:", exc)


if __name__ == "__main__":
    raise SystemExit(main())
