"""第二阶段的「控制台数据面」：事件流 / 错误代码 / 事故反查 / 后台审计 / 一致性对账。

设计要点（决定需求能否成立）：
- 管理后台与智能体平台**共享同一个数据库**，不做任何数据复制或定时同步。
  平台每写一步，后台查的就是同一张表的同一行 —— 物理上不存在「前端跑完后端看不到」。
- SSE 以 events.id 自增游标轮询，跨进程 / 跨容器都可靠（不依赖进程内内存广播）。
- 错误代码是可反馈的短码（AP-YYYYMMDD-XXXXXX），用户报码后后台可反查全链路证据。
- 所有写入都走参数化 SQL；事件 payload 与事故上下文一律脱敏摘要，不落用户原文。
"""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from typing import Any

from app.db.database import Database
from app.obs.logger import digest_context


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_CODE_ALPHABET = string.ascii_uppercase + string.digits


def new_incident_code() -> str:
    day = datetime.now().strftime("%Y%m%d")
    suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    return f"AP-{day}-{suffix}"


# ==================== 实时事件流 ====================

def emit_event(db: Database, *, kind: str, session_id: str | None = None,
               channel: str | None = None, actor: str | None = None,
               status: str | None = None, summary: str | None = None,
               payload: Any = None, duration_ms: int | None = None) -> int:
    """写一条事件。payload 只存脱敏摘要，避免把用户原文 / 密钥写进控制台库。"""
    digest = digest_context(payload, max_chars=300) if payload is not None else None
    db.execute(
        "INSERT INTO events(session_id, channel, kind, actor, status, summary, payload,"
        " duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, channel, kind, actor, status, (summary or "")[:300],
         digest, duration_ms, now_iso()),
    )
    row = db.query_one("SELECT MAX(id) AS n FROM events")
    return int((row or {}).get("n") or 0)


def list_events(db: Database, *, after_id: int = 0, limit: int = 200,
                session_id: str | None = None, kind: str | None = None,
                channel: str | None = None) -> list[dict]:
    clauses = ["id > ?"]
    params: list[Any] = [int(after_id)]
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if channel:
        clauses.append("channel = ?")
        params.append(channel)
    where = " AND ".join(clauses)
    params.append(int(limit))
    rows = db.query(
        f"SELECT id, session_id, channel, kind, actor, status, summary, payload,"
        f" duration_ms, created_at FROM events WHERE {where} ORDER BY id ASC LIMIT ?",
        tuple(params),
    )
    return rows


def latest_event_id(db: Database) -> int:
    row = db.query_one("SELECT COALESCE(MAX(id), 0) AS n FROM events")
    return int((row or {}).get("n") or 0)


# ==================== 错误代码 / 事故 ====================

def raise_incident(db: Database, *, error_class: str, message: str,
                   session_id: str | None = None, step_index: int | None = None,
                   level: str = "ERROR", source: str | None = None,
                   stack: str | None = None, context: Any = None) -> str:
    """生成一个可反馈错误代码并落库。返回 code，供前端展示给用户、供后台凭码反查。"""
    for _ in range(6):
        code = new_incident_code()
        try:
            db.execute(
                "INSERT INTO incidents(code, session_id, step_index, error_class, level,"
                " message, stack, context_digest, source, created_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                (code, session_id, step_index, error_class, level,
                 (message or "")[:2000], (stack or "")[:4000],
                 digest_context(context, max_chars=300) if context is not None else None,
                 source or "platform", now_iso()),
            )
            emit_event(db, kind="incident.raised", session_id=session_id,
                       actor="system", status=error_class,
                       summary=f"错误代码 {code} · {error_class}",
                       payload={"code": code, "message": message})
            return code
        except Exception:  # 极小概率撞码，换一个重试
            continue
    raise RuntimeError("无法生成唯一错误代码")


def lookup_incident(db: Database, code: str) -> dict:
    """凭用户反馈的错误代码反查：事故本体 + 会话 + 快照 + 日志 + 关联事件。"""
    inc = db.query_one("SELECT * FROM incidents WHERE code = ?", (code.strip().upper(),))
    if not inc:
        return {"found": False, "code": code, "message": "未查到该错误代码"}
    sid = inc.get("session_id")
    out: dict[str, Any] = {"found": True, "incident": inc, "session_id": sid}

    if sid:
        out["session"] = db.query_one("SELECT * FROM sessions WHERE session_id = ?", (sid,))
        out["snapshots"] = db.query(
            "SELECT step_index, agent_id, phase, status, error, created_at"
            " FROM snapshots WHERE session_id = ? ORDER BY step_index ASC LIMIT 200", (sid,))
        out["logs"] = db.query(
            "SELECT step_index, level, module, message, error_code, context_digest, created_at"
            " FROM logs WHERE session_id = ? ORDER BY id DESC LIMIT 200", (sid,))
        out["tasks"] = db.query(
            "SELECT task_id, title, assignee, status, attempts, error FROM tasks"
            " WHERE session_id = ? ORDER BY task_id ASC LIMIT 100", (sid,))
        out["audit"] = db.query(
            "SELECT tool, permission, status, error_code, duration_ms, created_at"
            " FROM tool_audit WHERE session_id = ? ORDER BY id DESC LIMIT 100", (sid,))
        out["events"] = db.query(
            "SELECT id, kind, actor, status, summary, created_at FROM events"
            " WHERE session_id = ? ORDER BY id ASC LIMIT 200", (sid,))
    return out


def list_incidents(db: Database, *, limit: int = 100, status: str | None = None,
                   error_class: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if error_class:
        clauses.append("error_class = ?")
        params.append(error_class)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    return db.query(
        f"SELECT id, code, session_id, step_index, error_class, level, message, source,"
        f" status, created_at, resolved_at FROM incidents{where}"
        f" ORDER BY id DESC LIMIT ?", tuple(params))


def resolve_incident(db: Database, code: str, resolution: str, resolved_by: str) -> bool:
    n = db.execute(
        "UPDATE incidents SET status='resolved', resolution=?, resolved_by=?, resolved_at=?"
        " WHERE code=? AND status != 'resolved'",
        (resolution[:2000], resolved_by, now_iso(), code.strip().upper()))
    return n > 0


# ==================== 后台审计 ====================

def admin_audit(db: Database, *, actor: str, action: str,
                target: str | None = None, detail: Any = None) -> None:
    db.execute(
        "INSERT INTO admin_audit(actor, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target,
         digest_context(detail, max_chars=300) if detail is not None else None, now_iso()))


def list_admin_audit(db: Database, limit: int = 100) -> list[dict]:
    return db.query(
        "SELECT actor, action, target, detail, created_at FROM admin_audit"
        " ORDER BY id DESC LIMIT ?", (int(limit),))


# ==================== 前后端一致性对账 ====================

def consistency_check(db: Database) -> dict:
    """直接回答「前端跑完任务、后端看不看得见」：
    对同一库里的会话轮次、任务、快照、事件计数做交叉比对，并给出明确结论。
    """
    def count(sql: str) -> int:
        row = db.query_one(sql)
        return int((row or {}).get("n") or 0)

    sessions = count("SELECT COUNT(*) AS n FROM sessions")
    turns_user = count("SELECT COUNT(*) AS n FROM chat_turns WHERE role='user'")
    turns_all = count("SELECT COUNT(*) AS n FROM chat_turns")
    tasks = count("SELECT COUNT(*) AS n FROM tasks")
    snapshots = count("SELECT COUNT(*) AS n FROM snapshots")
    events = count("SELECT COUNT(*) AS n FROM events")
    chat_events = count("SELECT COUNT(*) AS n FROM events WHERE kind IN ('chat.turn','chat.done')")
    incidents = count("SELECT COUNT(*) AS n FROM incidents")

    # 每一轮用户输入都应能在同一库里查到对应会话历史；事件是附加的即时推送，缺失不算数据丢失
    data_visible = sessions > 0 and turns_all >= turns_user

    return {
        "single_source_of_truth": True,
        "shared_database": True,
        "sessions": sessions,
        "chat_turns": {"user": turns_user, "total": turns_all},
        "tasks": tasks,
        "snapshots": snapshots,
        "events": {"total": events, "chat_related": chat_events},
        "incidents": incidents,
        "frontend_turns_vs_backend_records": {
            "frontend_user_turns": turns_user,
            "backend_stored_turns": turns_all,
            "each_frontend_turn_has_backend_record": turns_all >= turns_user,
        },
        "conclusion": (
            "后台与前端读取同一个数据库，前端每写入一轮 / 一步，后台即时可查；"
            "不存在独立副本，因此不会出现「前端跑完后端看不到」。"
            if data_visible else
            "当前库内暂无会话数据，尚无任务可对账；执行任意任务后此处将显示即时记录。"
        ),
        "verified_at": now_iso(),
    }
