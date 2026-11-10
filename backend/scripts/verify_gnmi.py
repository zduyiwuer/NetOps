"""gNMI Telemetry 端到端验证。

覆盖两个方向：
  A. **collector 模式**（设备订阅、平台供数并回送）
     设备连接平台 gNMI 服务 → 平台确认订阅 → 平台以 SubscribeResponse 回送
     该设备的真实采集指标。验证订阅建立、指标回送。
  B. **client 模式**（平台订阅设备）
     平台作为 gNMI 客户端构造订阅请求并连接设备。

运行：python -m scripts.verify_gnmi
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc

from app.proto import gnmi_pb2, gnmi_pb2_grpc

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def collector_scenario(device_id: int, rounds: int = 1) -> dict:
    """A. collector 模式：设备订阅，平台回送数据。"""
    from app.collectors.telemetry import TelemetryCollectorServer

    server = TelemetryCollectorServer(lambda s, d: None, lambda peer: device_id)
    ok = await server.start(57499)
    if not ok:
        return {"ok": False, "error": "collector 启动失败"}

    responses = []
    got_sync = False
    try:
        async with grpc.aio.insecure_channel("127.0.0.1:57499") as channel:
            stub = gnmi_pb2_grpc.gNMIStub(channel)

            request = gnmi_pb2.SubscribeRequest()
            request.subscribe.CopyFrom(
                gnmi_pb2.SubscriptionRequest(
                    subscription=gnmi_pb2.SubscriptionList(
                        subscription=[
                            gnmi_pb2.Subscription(
                                path=gnmi_pb2.Path(element="interfaces"),
                                mode=gnmi_pb2.SAMPLE,
                                sample_interval=3_000_000_000,
                            )
                        ]
                    )
                )
            )

            async def request_stream():
                yield request
                # 保持请求流存活
                while True:
                    await asyncio.sleep(1.0)
                    yield request

            call = stub.Subscribe(request_stream())

            async def reader():
                nonlocal got_sync
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        response = await asyncio.wait_for(call.read(), timeout=10.0)
                    except Exception:
                        return
                    if response is None:
                        return
                    kind = response.WhichOneof("response")
                    if kind == "sync_response":
                        got_sync = True
                    elif kind == "update":
                        responses.append(response)
                        if len(responses) >= rounds:
                            return

            await reader()
            call.cancel()
    finally:
        await server.stop()

    return {"ok": True, "got_sync": got_sync, "responses": responses}


def main() -> int:
    print("\n=== gNMI Telemetry 端到端验证 ===\n")

    from app.db.session import SessionLocal
    from app.models.models import Device

    db = SessionLocal()
    try:
        # 优先取 mock 模式设备：真实设备可能采集不到数据，
        # 会让验证结果依赖环境状态而非被测逻辑
        device = (
            db.query(Device)
            .filter(Device.mock_mode.is_(True), Device.enabled.is_(True))
            .order_by(Device.id)
            .first()
        )
        if device is None:
            device = db.query(Device).order_by(Device.id).first()
        device_id = device.id if device else None
    finally:
        db.close()

    if not device_id:
        print("  未找到设备，无法验证")
        return 1

    print("[1] collector 模式：设备订阅 → 平台回送数据")
    result = asyncio.run(collector_scenario(device_id))
    check("collector 启动并完成交互", result.get("ok") is True, result.get("error", ""))
    check("收到 sync_response 确认订阅", result.get("got_sync") is True)
    responses = result.get("responses", [])
    check("平台回送 SubscribeResponse 数据", len(responses) > 0, f"{len(responses)} 条")

    if responses:
        resp = responses[0]
        notif = resp.update.notification
        check("通知含 update 列表", len(notif.update) > 0, f"{len(notif.update)} 个 update")
        if notif.update:
            u = notif.update[0]
            check("update 含 TypedValue", u.HasField("val"))
            check("update 路径为叶子名", bool(u.path.element), u.path.element)
        prefix_elements = [p.element for p in notif.prefix]
        check("前缀含 interfaces 层级", "interfaces" in prefix_elements, str(prefix_elements))

    print("\n[2] client 模式：平台订阅设备")
    from app.collectors.telemetry import TelemetryClientSession

    session = TelemetryClientSession(
        device_id=device_id,
        target="127.0.0.1",
        port=57498,  # 无设备监听，应进入连接失败状态
        paths=["interfaces/interface/state/counters/in-octets"],
        sample_interval_ms=10000,
        on_samples=lambda s, d: None,
    )
    req = session._build_request()
    check("订阅请求构造成功", req.WhichOneof("request") == "subscribe", req.WhichOneof("request"))
    subs = req.subscribe.subscription.subscription
    check(
        "订阅路径正确",
        bool(subs) and subs[0].path.element.endswith("in-octets"),
        subs[0].path.element if subs else "-",
    )
    check("采样模式为 SAMPLE", bool(subs) and subs[0].mode == gnmi_pb2.SAMPLE)
    check(
        "采样间隔已设置",
        bool(subs) and subs[0].sample_interval > 0,
        f"{subs[0].sample_interval if subs else 0} ns",
    )
    check("无设备时连接状态为断开", session.status in ("disconnected", "error"), session.status)

    print("\n[3] 遥测路径映射与样本归一化")
    from app.collectors.telemetry import TelemetryPathMapper, TelemetrySample
    from app.services.telemetry_service import store_telemetry_samples

    mapper = TelemetryPathMapper()
    m = mapper.match("openconfig-interfaces:interfaces/interface[name=Ethernet1/1]/state/counters/in-octets")
    check("OpenConfig 路径映射", m is not None and m.metric == "if_in_octets", m.metric if m else "-")

    samples = [
        TelemetrySample(
            device_id=device_id, metric="cpu_usage", value=61.5, timestamp=datetime.now(),
            source_path="system/cpu/state/utilization",
        ),
        TelemetrySample(
            device_id=device_id, metric="if_in_octets", value=2_000_000_000,
            interface="FortyGigE1/1", timestamp=datetime.now(),
            source_path="interfaces/interface[name=FortyGigE1/1]/state/counters/in-octets",
            per_interface=True,
        ),
    ]
    n = store_telemetry_samples(samples, device_id, source="push")
    check("遥测样本入库", n == 2, f"{n} 条")

    from app.models.models import DeviceMetric, InterfaceMetric

    db = SessionLocal()
    dm = (
        db.query(DeviceMetric)
        .filter(DeviceMetric.device_id == device_id)
        .order_by(DeviceMetric.id.desc())
        .first()
    )
    check("CPU 归一化到指标表", dm is not None and dm.cpu_usage == 61.5, f"cpu={dm.cpu_usage if dm else '-'}")
    im = (
        db.query(InterfaceMetric)
        .filter(InterfaceMetric.device_id == device_id, InterfaceMetric.if_name == "FortyGigE1/1")
        .order_by(InterfaceMetric.id.desc())
        .first()
    )
    check(
        "接口计数器归一化",
        im is not None and im.in_octets == 2_000_000_000,
        f"in={im.in_octets if im else '-'}",
    )
    db.close()

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
