"""一键诊断：输入 session_id，输出最后一次成功步骤 / 首个失败点 / 错误归类 / 建议动作。

只读，不修改任何状态——诊断本身必须能安全地反复执行。
"""

from __future__ import annotations

import json
from typing import Any

from app.db.repository import Repository
from app.errors import ERROR_CLASSES


def _parse(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def diagnose(repo: Repository, session_id: str, settings=None) -> dict:
    session = repo.get_session(session_id)
    if not session:
        return {"session_id": session_id, "found": False, "message": "会话不存在"}

    snaps = repo.list_snapshots(session_id)
    logs = repo.list_logs(session_id)
    archive = repo.list_archive(session_id)
    tasks = repo.list_tasks(session_id)

    ok_steps = [s for s in snaps if s["status"] == "ok"]
    bad_steps = [s for s in snaps if s["status"] in ("failed", "interrupted")]

    last_ok = ok_steps[-1] if ok_steps else None
    first_bad = bad_steps[0] if bad_steps else None

    # 错误归类：优先取失败快照上的 error_code，其次取该步 ERROR 日志
    error_code = None
    error_message = None
    if first_bad:
        err = _parse(first_bad["error"])
        if isinstance(err, dict):
            error_code = err.get("error_code")
            error_message = err.get("message")
    if not error_code:
        err_logs = [l for l in logs if l["level"] == "ERROR"]
        if err_logs:
            error_code = err_logs[-1]["error_code"] or "UNKNOWN"
            error_message = err_logs[-1]["message"]

    # 未处理异常：最后一个成功步骤之后仍有 ERROR 日志，且会话未回到 completed
    tail_errors = [
        l for l in logs
        if l["level"] == "ERROR" and (last_ok is None or l["step_index"] > last_ok["step_index"])
    ]
    unhandled = bool(tail_errors) and session["status"] != "completed"

    actions = (settings.p("recovery", "actions", default={}) if settings else {}) or {}
    suggestion_action = actions.get(error_code or "UNKNOWN", "escalate")

    unfinished = [t for t in tasks if t["status"] in ("pending", "running", "failed")]

    return {
        "session_id": session_id,
        "found": True,
        "session_status": session["status"],
        "mode": session["mode"],
        "total_steps": session["step_index"],
        "snapshot_rows": len(snaps),
        "archived_rows": len(archive),
        "log_rows": len(logs),
        "last_success": None if not last_ok else {
            "step_index": last_ok["step_index"],
            "agent_id": last_ok["agent_id"],
            "phase": last_ok["phase"],
            "created_at": last_ok["created_at"],
        },
        "first_failure": None if not first_bad else {
            "step_index": first_bad["step_index"],
            "agent_id": first_bad["agent_id"],
            "phase": first_bad["phase"],
            "status": first_bad["status"],
            "created_at": first_bad["created_at"],
        },
        "error_class": error_code or "NONE",
        "error_class_desc": ERROR_CLASSES.get(error_code or "NONE", ""),
        "error_message": error_message,
        "unhandled_error": unhandled,
        "tail_error_steps": [l["step_index"] for l in tail_errors][:10],
        "suggested_action": suggestion_action,
        "unfinished_tasks": [
            {"task_id": t["task_id"], "title": t["title"], "status": t["status"]} for t in unfinished
        ],
        "resumable": session["status"] != "completed" and bool(unfinished),
    }


def format_diagnosis(d: dict) -> str:
    if not d.get("found"):
        return f"诊断失败：{d.get('message')}"
    lines = [
        f"=== 诊断报告 session={d['session_id']} ===",
        f"会话状态     : {d['session_status']}（mode={d['mode']}，已分配步号 {d['total_steps']}）",
        f"快照/日志行数: 快照 {d['snapshot_rows']} 行，归档 {d['archived_rows']} 行，日志 {d['log_rows']} 行",
    ]
    ls = d["last_success"]
    lines.append(
        "最后成功步骤 : "
        + (f"step {ls['step_index']} · {ls['agent_id']} · {ls['phase']} @ {ls['created_at']}" if ls else "无（从未成功执行过任何步骤）")
    )
    ff = d["first_failure"]
    lines.append(
        "首个失败点   : "
        + (f"step {ff['step_index']} · {ff['agent_id']} · {ff['phase']}（{ff['status']}）" if ff else "无失败记录")
    )
    lines.append(f"错误归类     : {d['error_class']} — {d['error_class_desc']}")
    if d.get("error_message"):
        lines.append(f"错误信息     : {d['error_message']}")
    lines.append(f"未处理异常   : {'是' if d['unhandled_error'] else '否'}"
                 + (f"（尾部错误步号 {d['tail_error_steps']}）" if d["tail_error_steps"] else ""))
    lines.append(f"建议动作     : {d['suggested_action']}")
    if d["unfinished_tasks"]:
        lines.append("未完成任务   :")
        for t in d["unfinished_tasks"]:
            lines.append(f"  - {t['task_id']} [{t['status']}] {t['title']}")
    lines.append(f"可恢复       : {'是' if d['resumable'] else '否'}")
    return "\n".join(lines)
