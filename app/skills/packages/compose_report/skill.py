"""技能实现：把摘要 + 统计组装成 Markdown 报告。

刻意保持"纯函数"：不读文件、不调工具、不写盘。
落盘由管理者在流水线里用 fs.write_text 完成（写操作要单独过人工确认）。
"""

from __future__ import annotations

from datetime import datetime


def run(params: dict, ctx: dict | None = None) -> dict:
    summary = params.get("summary") or []
    if isinstance(summary, str):
        summary = [summary]
    if not summary:
        raise ValueError("summary 为空，报告没有内容可写")

    title = str(params.get("title") or "自动生成报告")
    stats = params.get("stats") or {}
    output_path = str(params.get("output_path") or "output/report.md")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = [
        f"# {title}",
        "",
        f"> 生成时间：{generated_at}　|　由 Agent 编排平台自动产出（草稿，需人工复核）",
        "",
        "## 一、核心要点",
        "",
    ]
    for i, point in enumerate(summary, start=1):
        lines.append(f"{i}. {point}")

    lines += ["", "## 二、素材规模", ""]
    if stats:
        for k, v in stats.items():
            lines.append(f"- {k}：{v}")
    else:
        lines.append("- 未提供统计数据")

    lines += [
        "",
        "## 三、后续建议",
        "",
        "- 复核上述要点是否覆盖关键结论；",
        "- 补充数据来源与责任人；",
        "- 确认无敏感信息后对外分发。",
        "",
    ]
    report = "\n".join(lines)
    return {
        "ok": True,
        "title": title,
        "report": report,
        "report_len": len(report),
        "output_path": output_path,
        "sections": 3,
    }
