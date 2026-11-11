"""巡检报告生成：HTML（可打印/PDF）+ Excel。"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.models import Device, InspectionTask, InspectionTemplate, Store

_STATUS_LABEL = {"pass": "通过", "fail": "不通过", "skip": "跳过"}
_STATUS_COLOR = {"pass": "#16a34a", "fail": "#dc2626", "skip": "#94a3b8"}


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{unit}"


def build_report_context(task_id: int) -> Optional[Dict[str, Any]]:
    """构建报告数据上下文。"""
    db = SessionLocal()
    try:
        task = db.get(InspectionTask, task_id)
        if task is None:
            return None
        template = db.get(InspectionTemplate, task.template_id) if task.template_id else None
        try:
            results = json.loads(task.result or "[]")
        except Exception:
            results = []
        store_names = {s.id: s.name for s in db.query(Store).all()}
        for r in results:
            r["store_name"] = store_names.get(r.get("store_id"), "-")
        # 合规得分 = 通过的检查项 / 已实际评估的检查项总数
        # 跳过的检查项（设备不可达等）不计入分母，避免拉低得分
        evaluated = 0
        passed_checks = 0
        for r in results:
            checks = r.get("checks", [])
            if checks:
                # 仅统计实际判定过的项（pass / fail），跳过项不计入分母
                judged = [c for c in checks if c.get("status") in ("pass", "fail")]
                evaluated += len(judged)
                passed_checks += sum(1 for c in judged if c.get("status") == "pass")
            else:
                evaluated += r.get("summary", {}).get("total", 0)
                passed_checks += r.get("summary", {}).get("passed", 0)
        score = round(passed_checks / evaluated * 100, 2) if evaluated else 0.0
        return {
            "task": task,
            "template": template,
            "results": results,
            "score": score,
            "total_checks": evaluated,
            "passed_checks": passed_checks,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        db.close()


def render_html(ctx: Dict[str, Any]) -> str:
    task = ctx["task"]
    template = ctx["template"]
    results = ctx["results"]

    rows: List[str] = []
    for r in results:
        checks = r.get("checks", [])
        check_html = "<br>".join(
            f'<span style="color:{_STATUS_COLOR.get(c.get("status"), "#333")}">●</span> '
            f'{escape(str(c.get("title", "")))}: {_fmt(c.get("value"), c.get("unit", ""))} '
            f'({escape(str(c.get("message", "")))})'
            for c in checks
        ) or escape(r.get("error", "") or "—")
        rows.append(
            "<tr>"
            f"<td>{escape(str(r.get('store_name', '-')))}</td>"
            f"<td>{escape(str(r.get('device_name', '')))}</td>"
            f"<td>{escape(str(r.get('device_ip', '')))}</td>"
            f"<td>{escape(str(r.get('vendor', '')))}</td>"
            f"<td>{escape(str(r.get('model', '')))}</td>"
            f"<td class='num'>{r.get('summary', {}).get('passed', 0)}</td>"
            f"<td class='num'>{r.get('summary', {}).get('failed', 0)}</td>"
            f"<td class='num'>{r.get('summary', {}).get('skipped', 0)}</td>"
            f"<td class='detail'>{check_html}</td>"
            "</tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>基线巡检报告 - {escape(task.name)}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 32px; color: #1f2937; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 18px; background: #f9fafb; min-width: 110px; }}
  .card .k {{ font-size: 12px; color: #6b7280; }}
  .card .v {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 7px 9px; text-align: left; vertical-align: top; }}
  th {{ background: #f3f4f6; font-weight: 600; }}
  td.num {{ text-align: center; }}
  td.detail {{ font-size: 12px; line-height: 1.7; color: #374151; }}
  .score {{ font-size: 30px; font-weight: 800; color: {"#16a34a" if ctx["score"] >= 90 else "#d97706" if ctx["score"] >= 75 else "#dc2626"}; }}
  footer {{ margin-top: 24px; font-size: 12px; color: #9ca3af; }}
  @media print {{ body {{ margin: 12px; }} .card {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<h1>网络设备基线巡检报告</h1>
<div class="sub">
  任务：{escape(task.name)} &nbsp;|&nbsp; 模板：{escape(template.name if template else '-')} &nbsp;|&nbsp;
  执行人：{escape(task.operator or 'system')} &nbsp;|&nbsp; 生成时间：{escape(ctx['generated_at'])}
</div>

<div class="cards">
  <div class="card"><div class="k">巡检设备</div><div class="v">{task.total}</div></div>
  <div class="card"><div class="k">已判定项</div><div class="v">{ctx['total_checks']}</div></div>
  <div class="card"><div class="k">通过项</div><div class="v" style="color:#16a34a">{ctx['passed_checks']}</div></div>
  <div class="card"><div class="k">不通过项</div><div class="v" style="color:#dc2626">{task.failed}</div></div>
  <div class="card"><div class="k">跳过项</div><div class="v" style="color:#94a3b8">{task.warn}</div></div>
  <div class="card"><div class="k">合规得分</div><div class="v score">{ctx['score']}%</div></div>
</div>

<table>
<thead><tr>
<th>门店</th><th>设备名称</th><th>IP 地址</th><th>厂商</th><th>型号</th>
<th>通过</th><th>不通过</th><th>跳过</th><th>检查明细</th>
</tr></thead>
<tbody>
{''.join(rows) if rows else '<tr><td colspan="9">无巡检数据</td></tr>'}
</tbody>
</table>

<footer>本报告由网络设备运维平台自动生成 · {escape(ctx['generated_at'])}</footer>
</body>
</html>"""
    return html


def build_excel(ctx: Dict[str, Any], file_path: str) -> bool:
    """生成 Excel 版巡检报告。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return False

    task = ctx["task"]
    template = ctx["template"]
    wb = Workbook()

    # 概览页
    ws = wb.active
    ws.title = "巡检概览"
    ws["A1"] = "网络设备基线巡检报告"
    ws["A1"].font = Font(size=16, bold=True)
    meta = [
        ("任务名称", task.name),
        ("巡检模板", template.name if template else "-"),
        ("执行人", task.operator or "system"),
        ("开始时间", task.started_at.strftime("%Y-%m-%d %H:%M:%S") if task.started_at else "-"),
        ("结束时间", task.finished_at.strftime("%Y-%m-%d %H:%M:%S") if task.finished_at else "-"),
        ("生成时间", ctx["generated_at"]),
        ("巡检设备数", task.total),
        ("已判定项", ctx["total_checks"]),
        ("通过项", ctx["passed_checks"]),
        ("不通过项", task.failed),
        ("跳过项", task.warn),
        ("合规得分(%)", ctx["score"]),
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 40

    # 明细页
    ws2 = wb.create_sheet("巡检明细")
    headers = ["门店", "设备名称", "IP 地址", "厂商", "型号", "检查项", "实际值", "单位", "基线", "判定", "说明"]
    ws2.append(headers)
    for cell in ws2[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="374151")
        cell.alignment = Alignment(horizontal="center")
    for r in ctx["results"]:
        checks = r.get("checks", [])
        if not checks:
            ws2.append([
                r.get("store_name", "-"), r.get("device_name"), r.get("device_ip"),
                r.get("vendor"), r.get("model"), "-", "-", "", "", "失败",
                r.get("error", "采集失败"),
            ])
            continue
        for c in checks:
            ws2.append([
                r.get("store_name", "-"), r.get("device_name"), r.get("device_ip"),
                r.get("vendor"), r.get("model"), c.get("title"), c.get("value"),
                c.get("unit", ""), f"{c.get('operator')} {c.get('threshold')}",
                _STATUS_LABEL.get(c.get("status"), c.get("status")), c.get("message", ""),
            ])
    widths = [14, 18, 16, 10, 14, 18, 12, 8, 14, 10, 40]
    for i, w in enumerate(widths, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "A2"

    wb.save(file_path)
    return True


async def generate_inspection_report(task_id: int) -> Tuple[str, str]:
    """生成 HTML + Excel 报告，返回 (html路径, excel文件名)。"""
    ctx = build_report_context(task_id)
    if ctx is None:
        raise ValueError("巡检任务不存在")

    task = ctx["task"]
    os.makedirs(settings.report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"inspection_{task.id}_{stamp}"
    html_path = os.path.join(settings.report_dir, base + ".html")
    xlsx_path = os.path.join(settings.report_dir, base + ".xlsx")

    html = render_html(ctx)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    build_excel(ctx, xlsx_path)
    return html_path, os.path.basename(xlsx_path)
