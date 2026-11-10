"""AI 助手服务：对接国内外大模型 API，分析网络问题。

支持的服务商（均走 OpenAI 兼容的 Chat Completions 协议）：
- **国外**：OpenAI、Azure OpenAI、Anthropic Claude、Gemini（兼容端点）、Ollama（本地）
- **国内**：DeepSeek、Moonshot Kimi、通义千问 Qwen、智谱 GLM、百度千帆、字节豆包、
  SiliconFlow、阿里百炼等

上下文注入：把平台中的设备、告警、日志、接口指标、拓扑等数据整理成结构化文本，
随用户问题一起发给模型，使其具备「看懂当前网络」的能力。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger("netops.ai")

# 服务商预设：base_url + 默认模型
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "需 OpenAI API Key",
    },
    "deepseek": {
        "label": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "国内服务商，OpenAI 兼容协议",
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "国内服务商，长上下文",
    },
    "qwen": {
        "label": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "阿里百炼 / DashScope",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "国内服务商",
    },
    "baidu": {
        "label": "百度千帆",
        "base_url": "https://qianfan.baidubce.com/v2",
        "model": "ernie-4.0-8k",
        "note": "文心一言",
    },
    "doubao": {
        "label": "字节豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro",
        "note": "火山引擎方舟",
    },
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "note": "国内聚合平台",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
        "note": "使用 Anthropic 原生 Messages 协议",
    },
    "ollama": {
        "label": "Ollama 本地模型",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "note": "本地部署，数据不出内网",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "model": "",
        "note": "填写任意兼容 /chat/completions 的地址",
    },
}

DEFAULT_SYSTEM_PROMPT = """你是一名资深网络运维专家，负责分析企业网络的监控数据。

工作准则：
1. 基于提供的平台数据客观分析，指出具体设备、接口、时间点与指标数值。
2. 若数据不足，明确说明还需要哪些信息（如具体接口、告警时间窗、变更记录）。
3. 给出可执行的排查步骤与处置建议，按优先级排序。
4. 涉及安全风险（如异常流量、非法外联、ARP 攻击）时明确标注风险等级。
5. 使用简体中文回答，结构化输出：结论 → 依据 → 排查步骤 → 建议。
6. 不要编造平台数据中没有的信息。"""


@dataclass
class AIConfig:
    """AI 服务运行配置。"""

    enabled: bool = False
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: int = 60
    context_enabled: bool = True
    system_prompt: str = ""

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        preset = PROVIDERS.get(self.provider) or {}
        return (preset.get("base_url") or "").rstrip("/")

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        preset = PROVIDERS.get(self.provider) or {}
        return preset.get("model") or "gpt-4o-mini"


def load_config() -> AIConfig:
    """从运行时设置读取 AI 配置。"""
    from app.services.settings_service import effective_settings

    cfg = effective_settings()
    return AIConfig(
        enabled=bool(cfg.get("ai.enabled")),
        provider=cfg.get("ai.provider") or "openai",
        base_url=cfg.get("ai.base_url") or "",
        api_key=cfg.get("ai.api_key") or "",
        model=cfg.get("ai.model") or "",
        temperature=float(cfg.get("ai.temperature") or 0.3),
        max_tokens=int(cfg.get("ai.max_tokens") or 2048),
        timeout=int(cfg.get("ai.timeout") or 60),
        context_enabled=bool(cfg.get("ai.context_enabled", True)),
        system_prompt=cfg.get("ai.system_prompt") or DEFAULT_SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------- 上下文构建
def build_network_context(
    db,
    device_id: Optional[int] = None,
    hours: int = 6,
    include_logs: bool = True,
    include_alerts: bool = True,
    include_metrics: bool = True,
    include_topology: bool = False,
) -> str:
    """把平台数据整理成结构化上下文文本。"""
    from app.models.models import (
        AlertEvent,
        Device,
        DeviceLog,
        DeviceMetric,
        InterfaceMetric,
        Store,
    )

    lines: List[str] = []

    # ---- 设备清单 ----
    q = db.query(Device)
    if device_id:
        q = q.filter(Device.id == device_id)
    devices = q.all()
    stores = {s.id: s.name for s in db.query(Store).all()}
    lines.append("## 设备概览")
    lines.append(f"设备总数：{len(devices)}")
    for d in devices[:40]:
        store_name = stores.get(d.store_id, "未分配")
        lines.append(
            f"- {d.name} | {d.ip} | {d.vendor_label if hasattr(d, 'vendor_label') else d.vendor} | "
            f"{d.model or '-'} | 门店:{store_name} | 状态:{d.status} | 采集通道:"
            f"{'SNMP' if d.use_snmp else ''}"
            f"{'+Telemetry' if d.use_telemetry else ''}"
            f"{'+NetFlow' if d.use_netflow else ''}"
        )
    if len(devices) > 40:
        lines.append(f"- ...（其余 {len(devices) - 40} 台设备省略）")

    # ---- 资源指标 ----
    if include_metrics:
        lines.append("\n## 最近资源指标（CPU / 内存）")
        rows = []
        for d in devices[:40]:
            m = (
                db.query(DeviceMetric)
                .filter(DeviceMetric.device_id == d.id)
                .order_by(DeviceMetric.id.desc())
                .first()
            )
            if m:
                rows.append(
                    f"- {d.name}({d.ip}): CPU={m.cpu_usage if m.cpu_usage is not None else '-'}% "
                    f"内存={m.mem_usage if m.mem_usage is not None else '-'}% "
                    f"温度={m.temperature if m.temperature is not None else '-'}℃ "
                    f"响应={m.response_ms if m.response_ms is not None else '-'}ms "
                    f"可达={m.reachability}"
                )
        lines.extend(rows or ["（暂无指标数据）"])

    # ---- 接口速率 ----
        lines.append("\n## 高流量接口 Top 15")
        since = datetime.now() - timedelta(hours=hours)
        iface_rows = (
            db.query(InterfaceMetric)
            .filter(InterfaceMetric.ts >= since)
            .order_by(InterfaceMetric.utilization.desc())
            .limit(15)
            .all()
        )
        dev_map = {d.id: d for d in devices}
        if iface_rows:
            for r in iface_rows:
                d = dev_map.get(r.device_id)
                lines.append(
                    f"- {d.name if d else r.device_id} / {r.if_name or r.if_descr}: "
                    f"入={r.in_bps / 1e6:.2f}Mbps 出={r.out_bps / 1e6:.2f}Mbps "
                    f"利用率={r.utilization}% 速率={r.speed_bps / 1e6:.0f}Mbps "
                    f"状态={'UP' if r.oper_status == 1 else 'DOWN'} 错包={r.in_errors + r.out_errors}"
                )
        else:
            lines.append("（暂无接口数据）")

    # ---- 告警 ----
    if include_alerts:
        lines.append("\n## 最近告警（未恢复优先）")
        since = datetime.now() - timedelta(hours=max(hours, 24))
        alerts = (
            db.query(AlertEvent)
            .filter(AlertEvent.fired_at >= since)
            .order_by(AlertEvent.fired_at.desc())
            .limit(25)
            .all()
        )
        if alerts:
            for a in alerts:
                lines.append(
                    f"- [{a.severity}] {a.title} | 值={a.value} 阈值={a.threshold} "
                    f"| 状态={a.status} | {a.fired_at:%Y-%m-%d %H:%M:%S} | {a.message[:120]}"
                )
        else:
            lines.append("（无告警记录）")

    # ---- 日志 ----
    if include_logs:
        lines.append("\n## 设备日志（异常级别优先）")
        since = datetime.now() - timedelta(hours=max(hours, 24))
        logs = (
            db.query(DeviceLog)
            .filter(DeviceLog.ts >= since)
            .order_by(DeviceLog.ts.desc())
            .limit(40)
            .all()
        )
        sev_order = {"emerg": 0, "alert": 1, "crit": 2, "err": 3, "warning": 4}
        logs.sort(key=lambda x: sev_order.get(x.severity, 9))
        dev_map = {d.id: d for d in devices}
        if logs:
            for lg in logs[:30]:
                d = dev_map.get(lg.device_id)
                lines.append(
                    f"- [{lg.severity}] {d.name if d else lg.source_ip or '-'} "
                    f"{lg.ts:%Y-%m-%d %H:%M:%S} {lg.message[:180]}"
                )
        else:
            lines.append("（无日志记录）")

    # ---- 拓扑 ----
    if include_topology:
        lines.append("\n## 网络拓扑（邻居关系）")
        from app.models.models import TopologyNeighbor

        neigh = db.query(TopologyNeighbor).order_by(TopologyNeighbor.id.desc()).limit(60).all()
        if neigh:
            for n in neigh:
                lines.append(
                    f"- {n.local_device_name}({n.local_device_ip}) [{n.local_interface}] "
                    f"→ {n.remote_device}({n.remote_ip}) [{n.remote_interface}] "
                    f"协议={n.protocol}"
                )
        else:
            lines.append("（暂无拓扑数据，可执行拓扑采集）")

    return "\n".join(lines)


# ---------------------------------------------------------------- 调用
def _build_headers(config: AIConfig) -> Dict[str, str]:
    if config.provider == "anthropic":
        return {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def _build_payload(config: AIConfig, system: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    if config.provider == "anthropic":
        # Anthropic Messages 协议：system 单独传
        return {
            "model": config.resolved_model(),
            "system": system,
            "messages": messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
        }
    return {
        "model": config.resolved_model(),
        "messages": [{"role": "system", "content": system}] + messages,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stream": True,
    }


async def stream_chat(
    config: AIConfig,
    question: str,
    context: str = "",
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[str, None]:
    """流式对话，产出文本增量。"""
    base_url = config.resolved_base_url()
    if not base_url:
        yield "错误：未配置 API 地址。请在「系统管理 → AI 助手」中填写服务商与 Base URL。\n"
        return
    if not config.api_key and config.provider != "ollama":
        yield "错误：未配置 API Key。请在「系统管理 → AI 助手」中填写。\n"
        return

    system = config.system_prompt or DEFAULT_SYSTEM_PROMPT
    if context:
        system = f"{system}\n\n以下是当前网络环境的实时数据，请基于这些数据分析：\n\n{context}"

    messages: List[Dict[str, str]] = list(history or [])
    messages.append({"role": "user", "content": question})

    if config.provider == "anthropic":
        url = f"{base_url}/messages"
    else:
        url = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=config.timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=_build_headers(config),
                json=_build_payload(config, system, messages),
            ) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    yield f"错误：AI 服务返回 {resp.status_code} — {detail}\n"
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    delta = _extract_delta(chunk, config.provider)
                    if delta:
                        yield delta
    except httpx.TimeoutException:
        yield "错误：请求 AI 服务超时，请检查网络或调大超时时间。\n"
    except Exception as exc:
        yield f"错误：调用 AI 服务失败 — {exc}\n"


def _extract_delta(chunk: Dict[str, Any], provider: str) -> str:
    """从 SSE 分片里提取文本增量。"""
    try:
        if provider == "anthropic":
            if chunk.get("type") == "content_block_delta":
                return (chunk.get("delta") or {}).get("text") or ""
            return ""
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""
    except Exception:
        return ""


async def test_connection(config: AIConfig) -> Dict[str, Any]:
    """测试 AI 服务连通性。"""
    base_url = config.resolved_base_url()
    if not base_url:
        return {"ok": False, "message": "未配置 API 地址"}
    try:
        async with httpx.AsyncClient(timeout=min(config.timeout, 30)) as client:
            if config.provider == "anthropic":
                url = f"{base_url}/messages"
                headers = _build_headers(config)
                payload = {
                    "model": config.resolved_model(),
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                }
            else:
                url = f"{base_url}/chat/completions"
                headers = _build_headers(config)
                payload = {
                    "model": config.resolved_model(),
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                }
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code < 400:
                return {
                    "ok": True,
                    "message": f"连接成功：{config.resolved_model()}",
                }
            detail = resp.text[:300]
            return {"ok": False, "message": f"HTTP {resp.status_code} — {detail}"}
    except Exception as exc:
        return {"ok": False, "message": f"连接失败：{exc}"}


def provider_options() -> List[Dict[str, Any]]:
    """前端可用的服务商列表。"""
    return [
        {"value": key, "label": conf["label"], "base_url": conf["base_url"], "model": conf["model"], "note": conf["note"]}
        for key, conf in PROVIDERS.items()
    ]
