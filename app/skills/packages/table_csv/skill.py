"""技能包：生成标准 CSV 表格。真实输出可导入 Excel / WPS 的表格文本。"""

import csv
import io


def run(params: dict, ctx: dict | None = None) -> dict:
    title = str(params.get("title") or "工作跟踪表").strip()
    headers = params.get("headers") or ["序号", "事项", "责任人", "状态", "完成时间"]
    rows = params.get("rows") or [
        [1, "需求梳理与范围确认", "负责人", "已完成", "本周一"],
        [2, "方案设计与评审", "产品组", "进行中", "本周三"],
        [3, "实施与自检", "执行组", "待开始", "下周一"],
        [4, "验收与归档", "负责人", "待开始", "下周三"],
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(headers))
    for r in rows:
        writer.writerow(list(r))
    content = buf.getvalue()
    return {"ok": True, "title": title, "rows": len(rows), "cols": len(headers),
            "chars": len(content), "content": content}
