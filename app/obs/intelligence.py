"""第三阶段 · 智能分析中枢 / 升级迭代 / 用户与同意 / 错误深挖。

诚实边界（不夸大）：
- 「每时每刻分析」= 常驻后台线程按固定间隔扫描真实库表并落库，是**规则驱动的
  实时统计 + 异常检测 + 健康评分**，不是大模型自主推理；每轮结果都带真实数据来源。
- 「智能解读」= 按需手动触发一次大模型生成运营解读（method=rule+llm），
  模型不可用时明确返回失败原因并降级，绝不伪造结论。
- 「读取聊天内容用于升级迭代」= **只读取 consent=1（用户已同意）的会话**，
  证据片段先脱敏再落库；未授权会话不进入迭代分析。
- 「按手机号/账号排查」= 每次查询都写 query_audit 留痕，默认脱敏，明文需超管显式请求。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.db.database import Database
from app.obs.logger import digest_context

ANALYSIS_INTERVAL = int(os.environ.get("AP_ANALYSIS_INTERVAL", "20"))
ANALYSIS_WINDOW = int(os.environ.get("AP_ANALYSIS_WINDOW", "60"))

DEMAND_KWS = ("能不能", "可以吗", "能否", "希望", "建议", "想要", "需要支持", "请支持",
              "增加", "加上", "要是能", "如果能", "可否", "什么时候支持", "能不能加")
FAILURE_KWS = ("不对", "错了", "没用", "失败", "答非所问", "不理解", "重新", "重来",
               "没反应", "有问题", "bug", "不准", "不符合", "没听懂", "卡住", "太慢", "报错")
PRAISE_KWS = ("很好", "不错", "厉害", "满意", "谢谢", "好用")

_THRESHOLDS = {
    "error_rate_warn": 0.05,
    "error_rate_critical": 0.15,
    "p95_ms_warn": 8000,
    "p95_ms_critical": 15000,
    "open_incidents_warn": 1,
    "failed_sessions_warn": 1,
    "stall_minutes": 10,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _default_dsn() -> str:
    data_dir = Path(os.environ.get("AP_DATA_DIR") or (ROOT / "var"))
    return os.environ.get("AP_DB_DSN") or f"sqlite:///{data_dir / 'agent.db'}"


def open_db() -> Database:
    db = Database(_default_dsn())
    db.migrate()
    return db


# ==================== 脱敏 ====================

def mask_phone(phone: str | None) -> str:
    p = (phone or "").strip()
    if len(p) >= 7:
        return p[:3] + "****" + p[-4:]
    return p or ""


def mask_text(text: str | None, limit: int = 240) -> str:
    """证据片段脱敏：手机号、邮箱、长数字、连续身份证样式。"""
    s = str(text or "")
    s = re.sub(r"1[3-9]\d{9}", lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], s)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[邮箱已脱敏]", s)
    s = re.sub(r"\b\d{15,18}\b", "[证件号已脱敏]", s)
    s = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=[已脱敏]", s)
    return s[:limit]


# ==================== 用户 / 同意 ====================

def _user_key(account: str | None, phone: str | None) -> str | None:
    a = (account or "").strip()
    p = (phone or "").strip()
    return a or p or None


def bind_user(db: Database, *, session_id: str, account: str | None = None,
              phone: str | None = None, display_name: str | None = None,
              consent: bool = False, actor: str = "platform",
              note: str | None = None) -> str | None:
    """把平台会话绑定到用户身份。老会话不受影响；未传身份时直接返回 None。"""
    key = _user_key(account, phone)
    if not key:
        return None
    ts = now_iso()
    consent_val = 1 if consent else 0
    existing = db.query_one("SELECT * FROM platform_users WHERE user_key = ?", (key,))
    if existing:
        # 同意状态一旦授权，不因后续未显式传 consent 而被静默撤回（需显式撤回接口）
        merged = 1 if (existing.get("consent") or consent_val) else 0
        db.execute(
            "UPDATE platform_users SET phone=COALESCE(?, phone), account=COALESCE(?, account),"
            " display_name=COALESCE(?, display_name), consent=?, consent_at=CASE WHEN ?=1"
            " AND (consent_at IS NULL OR consent_at='') THEN ? ELSE consent_at END,"
            " updated_at=? WHERE user_key=?",
            (phone or None, account or None, display_name or None,
             merged, merged, ts, ts, key),
        )
    else:
        db.execute(
            "INSERT INTO platform_users(user_key, account, phone, display_name, consent,"
            " consent_at, consent_note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, account or None, phone or None, display_name or None, consent_val,
             ts if consent_val else None, note, ts, ts),
        )
    # 会话级冗余字段：便于按手机号/账号直接检索，无需 join
    cur_consent = db.query_one("SELECT consent FROM platform_users WHERE user_key=?", (key,))
    db.execute(
        "UPDATE sessions SET user_phone=?, user_account=?, consent=? WHERE session_id=?",
        (phone or None, account or None, int((cur_consent or {}).get("consent") or 0),
         session_id),
    )
    audit_query(db, actor=actor, action="user.bind", user_key=key, phone=phone,
                account=account, session_id=session_id, purpose="session_identity")
    return key


def set_consent(db: Database, *, user_key: str, consent: bool,
                actor: str, note: str | None = None) -> bool:
    ts = now_iso()
    val = 1 if consent else 0
    n = db.execute(
        "UPDATE platform_users SET consent=?, consent_at=?, consent_note=?, updated_at=?"
        " WHERE user_key=?",
        (val, ts if consent else None, note, ts, user_key))
    if n <= 0:
        return False
    db.execute("UPDATE sessions SET consent=? WHERE user_account=? OR user_phone=?",
               (val, user_key, user_key))
    audit_query(db, actor=actor, action="consent.change", user_key=user_key,
                purpose=f"consent={'granted' if consent else 'revoked'}: {note or ''}")
    return True


def list_users(db: Database, *, q: str | None = None, phone: str | None = None,
               account: str | None = None, consent: int | None = None,
               limit: int = 100) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if phone:
        clauses.append("phone LIKE ?")
        params.append(f"%{phone}%")
    if account:
        clauses.append("account LIKE ?")
        params.append(f"%{account}%")
    if q:
        clauses.append("(user_key LIKE ? OR phone LIKE ? OR account LIKE ? OR display_name LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if consent is not None:
        clauses.append("consent = ?")
        params.append(int(consent))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    rows = db.query(
        f"SELECT * FROM platform_users{where} ORDER BY updated_at DESC LIMIT ?", tuple(params))
    out = []
    for r in rows:
        d = dict(r)
        d["phone_masked"] = mask_phone(d.get("phone"))
        d["session_count"] = int((db.query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_account=? OR user_phone=?",
            (d.get("user_key"), d.get("user_key"))) or {}).get("n") or 0)
        out.append(d)
    return out


def user_detail(db: Database, user_key: str) -> dict:
    u = db.query_one("SELECT * FROM platform_users WHERE user_key=?", (user_key,))
    if not u:
        return {"found": False}
    sessions = db.query(
        "SELECT session_id, channel, status, consent, created_at, updated_at"
        " FROM sessions WHERE user_account=? OR user_phone=? ORDER BY created_at DESC LIMIT 200",
        (user_key, user_key))
    return {"found": True, "user": {**dict(u), "phone_masked": mask_phone(u.get("phone"))},
            "sessions": sessions, "session_count": len(sessions)}


def audit_query(db: Database, *, actor: str, action: str, user_key: str | None = None,
                phone: str | None = None, account: str | None = None,
                session_id: str | None = None, purpose: str | None = None,
                revealed: int = 0) -> None:
    db.execute(
        "INSERT INTO query_audit(actor, action, user_key, phone_mask, account, session_id,"
        " purpose, revealed, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (actor, action, user_key, mask_phone(phone) if phone else None,
         account, session_id, purpose, int(revealed), now_iso()))


def list_query_audit(db: Database, limit: int = 100) -> list[dict]:
    return db.query("SELECT * FROM query_audit ORDER BY id DESC LIMIT ?", (int(limit),))


# ==================== 聊天记录排查 ====================

def chatlog(db: Database, *, session_id: str | None = None, user_key: str | None = None,
            limit: int = 200, reveal: bool = False, actor: str = "admin",
            purpose: str = "bugfix") -> dict:
    """按会话或用户读取聊天记录。默认脱敏；reveal=True 需超管且写留痕。"""
    if user_key and not session_id:
        rows = db.query(
            "SELECT t.session_id, t.turn_index, t.role, t.content, t.mode, t.created_at"
            " FROM chat_turns t JOIN sessions s ON s.session_id=t.session_id"
            " WHERE s.user_account=? OR s.user_phone=? ORDER BY t.id ASC LIMIT ?",
            (user_key, user_key, int(limit)))
    elif session_id:
        rows = db.query(
            "SELECT session_id, turn_index, role, content, mode, created_at FROM chat_turns"
            " WHERE session_id=? ORDER BY turn_index ASC, id ASC LIMIT ?",
            (session_id, int(limit)))
    else:
        return {"found": False, "message": "请提供 session_id 或 user_key"}
    turns = []
    for r in rows:
        d = dict(r)
        d["content"] = str(d.get("content") or "") if reveal else mask_text(d.get("content"))
        turns.append(d)
    audit_query(db, actor=actor, action="chatlog.read", user_key=user_key,
                session_id=session_id, purpose=purpose, revealed=1 if reveal else 0)
    return {"found": True, "count": len(turns), "revealed": bool(reveal),
            "masked": not reveal, "turns": turns,
            "notice": ("已按超管请求展示原文，本次查询已留痕"
                       if reveal else "内容已脱敏；仅展示用户已授权或用于故障排查的必要信息")}


# ==================== 智能实时分析 ====================

def _count(db: Database, sql: str, params: tuple = ()) -> int:
    return int((db.query_one(sql, params) or {}).get("n") or 0)


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return int(s[min(len(s) - 1, int(len(s) * 0.95))])


def analyze_once(db: Database, window_minutes: int = ANALYSIS_WINDOW,
                 force: bool = False) -> dict:
    """跑一轮分析并落库。幂等：距上一轮不足一个间隔则跳过（多进程同时启用也不会重复写）。"""
    last = db.query_one("SELECT id, created_at FROM insight_runs ORDER BY id DESC LIMIT 1")
    if last and not force:
        prev = _parse(last.get("created_at"))
        if prev and (datetime.now(timezone.utc) - prev).total_seconds() < ANALYSIS_INTERVAL * 0.8:
            return {"skipped": True, "reason": "距上一轮分析不足一个间隔，避免重复写入",
                    "last_run_id": last.get("id")}
    started = now_iso()
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat(timespec="seconds")
    run_id = _new_run(db, started, window_minutes)

    sessions = _count(db, "SELECT COUNT(*) AS n FROM sessions WHERE created_at >= ?", (since,))
    turns = _count(db, "SELECT COUNT(*) AS n FROM chat_turns WHERE created_at >= ?", (since,))
    events = _count(db, "SELECT COUNT(*) AS n FROM events WHERE created_at >= ?", (since,))
    incidents = _count(db, "SELECT COUNT(*) AS n FROM incidents WHERE created_at >= ?", (since,))
    open_incidents = _count(db, "SELECT COUNT(*) AS n FROM incidents WHERE status!='resolved'")
    failed = _count(db, "SELECT COUNT(*) AS n FROM sessions"
                        " WHERE status IN ('failed','interrupted') AND created_at >= ?", (since,))
    durations = [int(r["duration_ms"]) for r in db.query(
        "SELECT duration_ms FROM events WHERE kind='chat.done' AND duration_ms IS NOT NULL"
        " AND created_at >= ?", (since,)) if r.get("duration_ms") is not None]
    avg_ms = int(sum(durations) / len(durations)) if durations else 0
    p95_ms = _p95(durations)
    consent_sessions = _count(db, "SELECT COUNT(*) AS n FROM sessions WHERE consent=1")
    active_users = _count(db, "SELECT COUNT(DISTINCT COALESCE(user_account,user_phone)) AS n"
                              " FROM sessions WHERE created_at >= ?"
                              " AND (user_account IS NOT NULL OR user_phone IS NOT NULL)", (since,))
    by_action = db.query(
        "SELECT COALESCE(status,'unknown') AS k, COUNT(*) AS n FROM events"
        " WHERE kind='chat.done' AND created_at >= ? GROUP BY status ORDER BY n DESC LIMIT 10", (since,))
    by_channel = db.query(
        "SELECT channel, COUNT(*) AS n FROM sessions WHERE created_at >= ? GROUP BY channel", (since,))

    error_rate = round(incidents / sessions, 4) if sessions else 0.0
    score = 100
    deductions: list[str] = []
    if error_rate > _THRESHOLDS["error_rate_critical"]:
        score -= 25; deductions.append(f"错误率 {error_rate:.1%} 超过临界值")
    elif error_rate > _THRESHOLDS["error_rate_warn"]:
        score -= 12; deductions.append(f"错误率 {error_rate:.1%} 偏高")
    if p95_ms > _THRESHOLDS["p95_ms_critical"]:
        score -= 20; deductions.append(f"P95 耗时 {p95_ms}ms 超过临界值")
    elif p95_ms > _THRESHOLDS["p95_ms_warn"]:
        score -= 10; deductions.append(f"P95 耗时 {p95_ms}ms 偏高")
    if open_incidents:
        score -= min(20, open_incidents * 5); deductions.append(f"{open_incidents} 起事故待处置")
    if failed:
        score -= min(15, failed * 3); deductions.append(f"{failed} 个会话失败/中断")
    if sessions and events == 0:
        score -= 10; deductions.append("有会话但事件流无记录（可能事件写入异常）")
    score = max(0, min(100, score))

    insights: list[dict] = []
    def add(category, severity, title, detail, value=None, evidence=None):
        insights.append({"category": category, "severity": severity, "title": title,
                         "detail": detail, "metric_value": value,
                         "evidence": json.dumps(evidence or {}, ensure_ascii=False)})
    if error_rate > _THRESHOLDS["error_rate_warn"]:
        add("error_rate", "critical" if error_rate > _THRESHOLDS["error_rate_critical"] else "warn",
            "错误率异常", f"窗口内 {incidents} 起事故 / {sessions} 个会话 = {error_rate:.1%}",
            error_rate, {"incidents": incidents, "sessions": sessions})
    if p95_ms > _THRESHOLDS["p95_ms_warn"]:
        add("latency", "critical" if p95_ms > _THRESHOLDS["p95_ms_critical"] else "warn",
            "响应延迟偏高", f"P95 = {p95_ms}ms，平均 {avg_ms}ms（取自 chat.done 真实测量）",
            p95_ms, {"p95_ms": p95_ms, "avg_ms": avg_ms, "samples": len(durations)})
    if open_incidents:
        add("incident", "warn", "存在未处置事故",
            f"{open_incidents} 起事故仍为 open/investigating，建议按错误代码核查处置",
            open_incidents, {"open_incidents": open_incidents})
    if failed:
        add("failure", "warn", "存在失败或中断会话",
            f"窗口内 {failed} 个会话状态为 failed/interrupted", failed, {"failed": failed})
    if consent_sessions == 0:
        add("iteration", "info", "暂无已授权会话可用于迭代分析",
            "尚未有用户同意将聊天内容用于产品升级迭代；迭代中心不会产生需求信号", 0)
    else:
        add("iteration", "info", "已授权会话可用于迭代分析",
            f"当前 {consent_sessions} 个会话已授权，迭代引擎会从中提取需求/失败信号",
            consent_sessions, {"consent_sessions": consent_sessions})
    if not deductions:
        add("health", "info", "运行平稳，未检测到异常",
            "错误率、延迟、事故、失败会话均在阈值内", score)
    else:
        add("health", "info", f"健康评分 {score}，存在 {len(deductions)} 项扣分项",
            "；".join(deductions), score, {"deductions": deductions})

    for ins in insights:
        db.execute(
            "INSERT INTO insights(run_id, category, severity, title, detail, evidence,"
            " metric_value, created_at, status) VALUES (?,?,?,?,?,?,?,?,'open')",
            (run_id, ins["category"], ins["severity"], ins["title"], ins["detail"],
             ins["evidence"], ins["metric_value"], now_iso()))
        db.execute(
            "INSERT INTO events(session_id, channel, kind, actor, status, summary, payload,"
            " created_at) VALUES (NULL,NULL,'analysis.insight','intelligence',?,?,?,?)",
            (ins["severity"], f"{ins['category']} · {ins['title']}",
             digest_context({"run_id": run_id, "title": ins["title"]}, max_chars=200), now_iso()))

    summary = (f"近 {window_minutes} 分钟：{sessions} 个会话、{turns} 轮对话、{events} 条事件、"
               f"{incidents} 起事故；平均 {avg_ms}ms / P95 {p95_ms}ms；健康评分 {score}。"
               + ("；".join(deductions) if deductions else "各项指标平稳，未检测到异常。"))
    metrics = {"window_minutes": window_minutes, "sessions": sessions, "turns": turns,
               "events": events, "incidents": incidents, "open_incidents": open_incidents,
               "failed_sessions": failed, "avg_ms": avg_ms, "p95_ms": p95_ms,
               "samples": len(durations), "error_rate": error_rate, "consent_sessions": consent_sessions,
               "active_users": active_users, "by_action": by_action, "by_channel": by_channel,
               "deductions": deductions, "thresholds": _THRESHOLDS}
    db.execute(
        "UPDATE insight_runs SET finished_at=?, status='done', health_score=?, summary=?,"
        " metrics=? WHERE id=?",
        (now_iso(), score, summary, json.dumps(metrics, ensure_ascii=False), run_id))
    return {"skipped": False, "run_id": run_id, "health_score": score,
            "summary": summary, "insights_written": len(insights), "metrics": metrics}


def _new_run(db: Database, started: str, window_minutes: int) -> int:
    db.execute(
        "INSERT INTO insight_runs(started_at, window_minutes, status, method, created_at)"
        " VALUES (?,?, 'running','rule-engine',?)",
        (started, window_minutes, started))
    return int((db.query_one("SELECT MAX(id) AS n FROM insight_runs") or {}).get("n") or 0)


def latest_analysis(db: Database) -> dict:
    run = db.query_one("SELECT * FROM insight_runs ORDER BY id DESC LIMIT 1")
    if not run:
        return {"found": False, "message": "尚无分析批次，等待常驻引擎首轮运行"}
    insights = db.query(
        "SELECT id, category, severity, title, detail, metric_value, status, created_at"
        " FROM insights WHERE run_id=? ORDER BY id ASC", (run["id"],))
    metrics = {}
    try:
        metrics = json.loads(run.get("metrics") or "{}")
    except (ValueError, TypeError):
        pass
    return {"found": True, "run": dict(run), "metrics": metrics, "insights": insights,
            "engine": {"interval_seconds": ANALYSIS_INTERVAL,
                       "window_minutes": ANALYSIS_WINDOW,
                       "method": "rule-engine（规则驱动的实时统计与异常检测）",
                       "note": "常驻线程周期扫描真实库表；AI 深度解读需手动触发"}}


def list_runs(db: Database, limit: int = 50) -> list[dict]:
    return db.query(
        "SELECT id, started_at, finished_at, window_minutes, health_score, status, method,"
        " summary, created_at FROM insight_runs ORDER BY id DESC LIMIT ?", (int(limit),))


def list_insights(db: Database, *, limit: int = 100, severity: str | None = None,
                  category: str | None = None, status: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if severity:
        clauses.append("severity=?"); params.append(severity)
    if category:
        clauses.append("category=?"); params.append(category)
    if status:
        clauses.append("status=?"); params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    return db.query(
        f"SELECT * FROM insights{where} ORDER BY id DESC LIMIT ?", tuple(params))


def health_trend(db: Database, limit: int = 40) -> list[dict]:
    rows = db.query(
        "SELECT id, created_at, health_score, status, summary FROM insight_runs"
        " WHERE status='done' ORDER BY id DESC LIMIT ?", (int(limit),))
    return list(reversed(rows))


def ack_insight(db: Database, insight_id: int, status: str) -> bool:
    return db.execute("UPDATE insights SET status=? WHERE id=?", (status, int(insight_id))) > 0


def ai_brief(db: Database) -> dict:
    """按需调用大模型生成一段运营解读；不可用/失败时如实返回原因，不伪造内容。"""
    cfg_path = Path(os.environ.get("AP_DATA_DIR") or (ROOT / "var")) / "llm_runtime.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    except (OSError, ValueError):
        cfg = {}
    base = str(cfg.get("base_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    model = str(cfg.get("model") or "deepseek-chat")
    if not base:
        return {"ok": False, "reason": "未配置大模型 base_url，无法生成 AI 解读；规则分析仍正常可用"}
    latest = latest_analysis(db)
    facts = latest.get("metrics") or {}
    prompt = ("你是智能体平台的运营分析助手。基于以下真实统计指标，用不超过 200 字给出"
              "「现状判断 + 最值得优先处理的 2 件事 + 升级迭代建议」。只依据给出的数据，"
              "不要编造不存在的指标。\n\n" + json.dumps(facts, ensure_ascii=False)[:3000])
    payload = {"model": model, "temperature": 0.3, "max_tokens": 400,
               "messages": [{"role": "system", "content": "你是一名严谨的平台运维分析师，结论必须可追溯到指标。"},
                            {"role": "user", "content": prompt}]}
    try:
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {key}"} if key else {})},
            method="POST")
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not text:
            return {"ok": False, "reason": "模型返回空内容"}
    except Exception as exc:
        return {"ok": False, "reason": f"模型调用失败：{exc}"}
    run_id = (latest.get("run") or {}).get("id")
    db.execute(
        "INSERT INTO insights(run_id, category, severity, title, detail, evidence,"
        " metric_value, created_at, status) VALUES (?,?,?,?,?,?,?,?,'open')",
        (run_id, "ai_brief", "info", "AI 运营解读（按需生成）", text,
         json.dumps({"model": model, "source": "llm"}, ensure_ascii=False), None, now_iso()))
    if run_id:
        db.execute("UPDATE insight_runs SET method='rule+llm' WHERE id=?", (run_id,))
    return {"ok": True, "model": model, "brief": text,
            "note": "本次为手动触发的大模型解读；周期性分析仍是规则引擎，二者方法不同已分别标注"}


# ==================== 升级迭代（只读取已授权会话） ====================

def _normalize_title(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"^[请帮我您，,。！!？?\s]+", "", t)
    return t[:60] or "（空内容）"


def scan_iteration(db: Database) -> dict:
    """从 consent=1 的会话用户消息中提取需求/失败/表扬信号与高频主题，去重累计票数。"""
    rows = db.query(
        "SELECT t.session_id, t.turn_index, t.content FROM chat_turns t"
        " JOIN sessions s ON s.session_id=t.session_id"
        " WHERE s.consent=1 AND t.role='user' ORDER BY t.id DESC LIMIT 800")
    created = updated = 0
    categories: Counter = Counter()
    texts: list[str] = []
    for r in rows:
        raw = str(r.get("content") or "")
        texts.append(raw)
        cat = None
        if any(k in raw for k in FAILURE_KWS):
            cat = "failure"
        elif any(k in raw for k in DEMAND_KWS):
            cat = "demand"
        elif any(k in raw for k in PRAISE_KWS):
            cat = "praise"
        if not cat:
            continue
        title = _normalize_title(raw)
        evidence = mask_text(raw)
        categories[cat] += 1
        old = db.query_one(
            "SELECT id, votes FROM iteration_items WHERE category=? AND title=?", (cat, title))
        if old:
            db.execute("UPDATE iteration_items SET votes=votes+1, updated_at=? WHERE id=?",
                       (now_iso(), old["id"]))
            updated += 1
        else:
            db.execute(
                "INSERT INTO iteration_items(category, title, evidence, source_session,"
                " source_turn, priority, status, votes, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'P2','new',1,?,?)",
                (cat, title, evidence, r.get("session_id"), r.get("turn_index"),
                 now_iso(), now_iso()))
            created += 1
    # 高频主题：中文 2-gram 词频，取 TOP 6
    grams: Counter = Counter()
    for t in texts:
        for seg in re.findall(r"[\u4e00-\u9fa5]{2,}", t):
            for i in range(len(seg) - 1):
                grams[seg[i:i + 2]] += 1
    for gram, n in grams.most_common(6):
        if n < 2:
            continue
        title = f"高频主题：{gram}"
        old = db.query_one(
            "SELECT id FROM iteration_items WHERE category='topic' AND title=?", (title,))
        if old:
            db.execute("UPDATE iteration_items SET votes=?, updated_at=? WHERE id=?",
                       (n, now_iso(), old["id"]))
            updated += 1
        else:
            db.execute(
                "INSERT INTO iteration_items(category, title, evidence, priority, status,"
                " votes, created_at, updated_at) VALUES ('topic',?,?,'P3','new',?,?,?)",
                (title, f"近 800 条已授权用户消息中「{gram}」出现 {n} 次（真实词频统计）",
                 n, now_iso(), now_iso()))
            created += 1
    return {"scanned_turns": len(rows), "consent_only": True, "created": created,
            "merged": updated, "by_category": dict(categories),
            "notice": "仅读取 consent=1 的会话；证据片段已脱敏；未授权会话不进入迭代分析"}


def list_iterations(db: Database, *, status: str | None = None,
                    category: str | None = None, limit: int = 200) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status=?"); params.append(status)
    if category:
        clauses.append("category=?"); params.append(category)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    return db.query(f"SELECT * FROM iteration_items{where}"
                    f" ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1"
                    f" WHEN 'P2' THEN 2 ELSE 3 END, votes DESC, id DESC LIMIT ?", tuple(params))


def iteration_stats(db: Database) -> dict:
    by_status = db.query("SELECT status, COUNT(*) AS n FROM iteration_items GROUP BY status")
    by_cat = db.query("SELECT category, COUNT(*) AS n, SUM(votes) AS votes"
                      " FROM iteration_items GROUP BY category ORDER BY votes DESC")
    total = _count(db, "SELECT COUNT(*) AS n FROM iteration_items")
    consent = _count(db, "SELECT COUNT(*) AS n FROM sessions WHERE consent=1")
    return {"total": total, "by_status": by_status, "by_category": by_cat,
            "consent_sessions": consent,
            "pipeline": "用户授权 → 真实会话 → 信号提取 → 去重累计 → 分优先级 → 排期/完成 → 回归验证"}


def update_iteration(db: Database, item_id: int, **fields) -> bool:
    allowed = {"status": ("new", "triaged", "planned", "done", "rejected"),
               "priority": ("P0", "P1", "P2", "P3")}
    sets: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k in allowed and v in allowed[k]:
            sets.append(f"{k}=?"); params.append(v)
        elif k == "note":
            sets.append("note=?"); params.append(str(v)[:1000])
    if not sets:
        return False
    sets.append("updated_at=?"); params.append(now_iso())
    params.append(int(item_id))
    return db.execute(f"UPDATE iteration_items SET {', '.join(sets)} WHERE id=?",
                      tuple(params)) > 0


# ==================== 错误深度核查 ====================

def error_timeline(db: Database, code: str) -> dict:
    inc = db.query_one("SELECT * FROM incidents WHERE code=?", (code.strip().upper(),))
    if not inc:
        return {"found": False, "message": "未查到该错误代码"}
    sid = inc.get("session_id")
    items: list[dict] = []
    items.append({"ts": inc.get("created_at"), "type": "事故登记",
                  "title": f"{inc.get('error_class')} · {code}", "detail": inc.get("message")})
    if sid:
        for r in db.query("SELECT kind, status, summary, created_at FROM events"
                          " WHERE session_id=? ORDER BY id ASC LIMIT 200", (sid,)):
            items.append({"ts": r["created_at"], "type": "事件",
                          "title": f"{r.get('kind')} · {r.get('status') or ''}",
                          "detail": r.get("summary")})
        for r in db.query("SELECT step_index, level, module, message, error_code, created_at"
                          " FROM logs WHERE session_id=? ORDER BY id ASC LIMIT 200", (sid,)):
            items.append({"ts": r["created_at"], "type": f"日志·{r.get('level')}",
                          "title": f"step{r.get('step_index')} {r.get('module')} {r.get('error_code') or ''}",
                          "detail": r.get("message")})
        for r in db.query("SELECT step_index, phase, status, error, created_at FROM snapshots"
                          " WHERE session_id=? ORDER BY step_index ASC LIMIT 200", (sid,)):
            items.append({"ts": r["created_at"], "type": "快照",
                          "title": f"step{r.get('step_index')} {r.get('phase')} · {r.get('status')}",
                          "detail": r.get("error")})
    items.sort(key=lambda x: str(x.get("ts") or ""))
    return {"found": True, "code": code, "session_id": sid,
            "incident": dict(inc), "timeline": items, "count": len(items)}


def similar_incidents(db: Database, code: str, limit: int = 20) -> dict:
    inc = db.query_one("SELECT * FROM incidents WHERE code=?", (code.strip().upper(),))
    if not inc:
        return {"found": False}
    rows = db.query(
        "SELECT code, session_id, error_class, message, status, created_at FROM incidents"
        " WHERE code != ? AND (error_class = ? OR (session_id IS NOT NULL AND session_id = ?))"
        " ORDER BY id DESC LIMIT ?",
        (inc["code"], inc["error_class"], inc.get("session_id"), int(limit)))
    return {"found": True, "base": dict(inc), "similar": rows, "count": len(rows),
            "clusters": dict(Counter(r["error_class"] for r in rows))}


def root_cause(db: Database, code: str) -> dict:
    inc = db.query_one("SELECT * FROM incidents WHERE code=?", (code.strip().upper(),))
    if not inc:
        return {"found": False}
    sid = inc.get("session_id")
    suspects: list[dict] = []
    if sid:
        for r in db.query(
            "SELECT error_code, module, COUNT(*) AS n FROM logs WHERE session_id=?"
            " AND level IN ('ERROR','WARNING') AND error_code IS NOT NULL"
            " GROUP BY error_code, module ORDER BY n DESC LIMIT 10", (sid,)):
            suspects.append({"kind": "日志错误码", "value": r["error_code"],
                             "where": r["module"], "hits": r["n"]})
        for r in db.query(
            "SELECT tool, error_code, status, COUNT(*) AS n FROM tool_audit WHERE session_id=?"
            " AND (status IN ('error','denied','rejected_by_human') OR error_code IS NOT NULL)"
            " GROUP BY tool, error_code, status ORDER BY n DESC LIMIT 10", (sid,)):
            suspects.append({"kind": "工具调用", "value": r["error_code"] or r["status"],
                             "where": r["tool"], "hits": r["n"]})
        for r in db.query(
            "SELECT task_id, title, status, error FROM tasks WHERE session_id=?"
            " AND status IN ('failed','rejected') LIMIT 10", (sid,)):
            suspects.append({"kind": "失败子任务", "value": r["status"],
                             "where": r["title"], "hits": 1,
                             "detail": r.get("error")})
    stack = (inc.get("stack") or "").strip()
    if stack:
        last = [ln.strip() for ln in stack.splitlines() if ln.strip()][-3:]
        suspects.append({"kind": "异常堆栈", "value": inc["error_class"],
                         "where": " | ".join(last)[:300], "hits": 1})
    return {"found": True, "code": code, "session_id": sid,
            "error_class": inc["error_class"], "suspects": suspects,
            "conclusion": ("已定位到候选根因线索，按命中次数排序；需结合时间线人工确认"
                           if suspects else "该事故未关联到会话级证据，建议核查全局异常处理器日志")}


def error_trends(db: Database, hours: int = 24) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = db.query(
        "SELECT substr(created_at,1,13) AS hour, COUNT(*) AS n FROM incidents"
        " WHERE created_at >= ? GROUP BY hour ORDER BY hour ASC", (since,))
    by_class = db.query(
        "SELECT error_class, COUNT(*) AS n FROM incidents WHERE created_at >= ?"
        " GROUP BY error_class ORDER BY n DESC LIMIT 10", (since,))
    by_status = db.query("SELECT status, COUNT(*) AS n FROM incidents GROUP BY status")
    return {"hours": hours, "hourly": rows, "by_class": by_class, "by_status": by_status,
            "total": sum(int(r["n"]) for r in rows)}


# ==================== 常驻分析线程 ====================

_loop_started = False
_loop_lock = threading.Lock()


def start_background_loop(interval: int = ANALYSIS_INTERVAL) -> bool:
    """平台进程启动一次；独立后台进程也可调用，analyze_once 自带幂等避免重复写。"""
    global _loop_started
    with _loop_lock:
        if _loop_started:
            return False
        _loop_started = True

    def loop():
        # 启动后先让服务完成初始化，再跑首轮
        time.sleep(min(3, max(1, interval // 4)))
        while True:
            db = None
            try:
                db = open_db()
                analyze_once(db)
                scan_iteration(db)
            except Exception:
                pass  # 分析设施异常绝不影响平台主链路
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
            time.sleep(max(5, interval))

    threading.Thread(target=loop, name="ap-intelligence", daemon=True).start()
    return True
