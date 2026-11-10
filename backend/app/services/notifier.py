"""通知分发：Webhook / 企业微信 / 钉钉 / 邮件(SMTP)。"""
from __future__ import annotations

import asyncio
import json
import smtplib
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

SEVERITY_ORDER = {"info": 1, "warning": 2, "crit": 3, "critical": 3, "alert": 4, "emerg": 5}


def _severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get((severity or "info").lower(), 1)


def format_alert_text(alert: Dict[str, Any]) -> str:
    lines = [
        f"【网络告警】{alert.get('title', '')}",
        f"设备: {alert.get('device_name', '')} ({alert.get('device_ip', '')})",
        f"门店: {alert.get('store_name', '-')}",
        f"级别: {alert.get('severity', '')}",
        f"指标: {alert.get('metric', '')} = {alert.get('value')} (阈值 {alert.get('operator','')} {alert.get('threshold')})",
        f"时间: {alert.get('fired_at', '')}",
        f"说明: {alert.get('message', '')}",
    ]
    return "\n".join(lines)


class Notifier:
    """通知发送器。"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.webhook_timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send(self, channel, alert: Dict[str, Any]) -> bool:
        """按渠道类型发送单条告警通知。"""
        try:
            if not channel.enabled:
                return False
            if _severity_rank(alert.get("severity", "info")) < _severity_rank(channel.min_severity):
                return False
            text = format_alert_text(alert)
            ctype = (channel.type or "webhook").lower()
            if ctype == "wecom":
                return await self._send_wecom(channel, text, alert)
            if ctype == "dingtalk":
                return await self._send_dingtalk(channel, text, alert)
            if ctype == "email":
                return await self._send_email(channel, text, alert)
            return await self._send_webhook(channel, text, alert)
        except Exception:
            return False

    async def _send_webhook(self, channel, text: str, alert: Dict[str, Any]) -> bool:
        if not channel.target:
            return False
        client = await self._get_client()
        payload = {"text": text, "title": alert.get("title", ""), "alert": alert}
        resp = await client.post(channel.target, json=payload)
        return resp.status_code < 400

    async def _send_wecom(self, channel, text: str, alert: Dict[str, Any]) -> bool:
        """企业微信机器人 webhook（markdown）。"""
        if not channel.target:
            return False
        client = await self._get_client()
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**网络设备告警**\n{text.replace(chr(10), chr(10) + '> ')}",
            },
        }
        resp = await client.post(channel.target, json=payload)
        return resp.status_code < 400

    async def _send_dingtalk(self, channel, text: str, alert: Dict[str, Any]) -> bool:
        if not channel.target:
            return False
        client = await self._get_client()
        payload = {"msgtype": "text", "text": {"content": text}}
        resp = await client.post(channel.target, json=payload)
        return resp.status_code < 400

    async def _send_email(self, channel, text: str, alert: Dict[str, Any]) -> bool:
        extra = {}
        try:
            extra = json.loads(channel.extra or "{}")
        except Exception:
            extra = {}
        host = extra.get("host", "localhost")
        port = int(extra.get("port", 25))
        sender = extra.get("from", "netops@localhost")
        recipients = [x for x in (extra.get("to") or channel.target or "").split(",") if x]
        if not recipients:
            return False
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = f"[NetOps] {alert.get('title', '告警')}"
        msg["From"] = sender
        msg["To"] = ",".join(recipients)
        return await asyncio.to_thread(self._smtp_send, host, port, sender, extra.get("password", ""), recipients, msg)

    def _smtp_send(self, host, port, sender, password, recipients, msg) -> bool:
        try:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                if password:
                    server.login(sender, password)
                server.sendmail(sender, recipients, msg.as_string())
            return True
        except Exception:
            return False


notifier = Notifier()
