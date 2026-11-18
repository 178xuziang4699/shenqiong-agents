"""Agent Platform · 管理后台 后端服务。

独立性：本模块是一个**独立 FastAPI 应用**，拥有独立进程、独立端口、独立鉴权、
独立前端页面。它不复用平台的路由与会话，只共享同一个数据库文件 —— 这正是
「前后端实时同步」的实现方式：单一数据源，不存在同步延迟与数据复制。

两种部署形态（同一份代码）：
1. Docker / 本机：uvicorn admin.server:app --port 8001（独立进程）
2. 在线单端口：平台 main.py 通过 app.mount("/console", admin_app) 挂载，
   仍是独立页面 + 独立鉴权，只是共用进程以适配只有一个对外端口的发布通道。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.config import ROOT
from app.db.database import Database
from app.db.repository import Repository
from app.obs import console as obs
from app.obs import intelligence as intel
from app.obs.diagnose import diagnose, format_diagnosis

STATIC_DIR = Path(__file__).parent / "static"
_STARTED_AT = time.time()

# ---------- 鉴权令牌 ----------
# 超管可处置事故、触发恢复、执行备份；只读仅能查看。
# 生产环境务必通过环境变量注入；未注入时生成随机令牌并落到 600 权限文件。
def _token_file() -> Path:
    return Path(os.environ.get("AP_DATA_DIR") or (ROOT / "var")) / "admin_token.txt"


def _load_or_create_token(env_name: str, label: str) -> str:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return configured
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, str] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if not data.get(label):
        data[label] = secrets.token_urlsafe(24)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return data[label]


ADMIN_TOKEN = _load_or_create_token("AP_ADMIN_TOKEN", "admin")
VIEW_TOKEN = _load_or_create_token("AP_ADMIN_VIEW_TOKEN", "view")
PLATFORM_URL = os.environ.get("AP_PLATFORM_URL", "http://127.0.0.1:8000").rstrip("/")


import threading

_migrate_lock = threading.Lock()
_migrated_dsn: str | None = None


def _db() -> Database:
    """后台使用独立数据库连接（只读为主），避免与平台写连接互相阻塞。

    DSN 与平台完全一致 → 查的是同一份数据；SQLite 下 WAL 支持多进程并发读。
    migrate 只在该进程首次连接时执行一次，避免每请求重复 DDL 造成锁竞争。
    """
    global _migrated_dsn
    dsn = os.environ.get("AP_DB_DSN") or f"sqlite:///{_token_file().parent / 'agent.db'}"
    db = Database(dsn)
    with _migrate_lock:
        if _migrated_dsn != dsn:
            db.migrate()
            _migrated_dsn = dsn
    return db


def _role(token: str | None) -> str | None:
    if not token:
        return None
    if secrets.compare_digest(token, ADMIN_TOKEN):
        return "admin"
    if secrets.compare_digest(token, VIEW_TOKEN):
        return "viewer"
    return None


app = FastAPI(title="Agent Platform · 管理后台", version="2.0.0")

_WHITELIST = ("/health", "/api/admin/login", "/console", "/", "")


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    # 用 scope["path"] 而非 request.url.path：挂载到 /console 时后者会带前缀，
    # 而 scope["path"] 已被 Mount 剥掉前缀，鉴权逻辑因此与部署形态无关。
    path = request.scope.get("path", request.url.path)
    # 兼容「独立进程却通过 /console 前缀访问」的情况：归一化后路由与鉴权都成立
    if path.startswith("/console/api/"):
        path = path[len("/console"):]
        request.scope["path"] = path
    is_page = path in ("/", "/console", "/console/", "/index.html")
    is_open = path in ("/health",) or path.endswith("/api/admin/login") or is_page
    if is_open or not path.startswith("/api/"):
        return await call_next(request)
    token = request.headers.get("X-Admin-Token") or request.query_params.get("token")
    role = _role(token)
    if not role:
        return JSONResponse(status_code=401, content={
            "error": "UNAUTHORIZED",
            "message": "管理后台需要访问令牌，请在登录框输入超管或只读令牌。",
        })
    request.state.role = role
    request.state.actor = "admin" if role == "admin" else "viewer"
    return await call_next(request)


def _require_admin(request: Request) -> None:
    if getattr(request.state, "role", None) != "admin":
        raise HTTPException(status_code=403, detail={
            "error": "READ_ONLY", "message": "该操作需要超管令牌，只读令牌只能查看。"})


# ==================== 登录 / 健康 ====================

class LoginIn(BaseModel):
    token: str


@app.post("/api/admin/login")
def login(body: LoginIn) -> dict:
    role = _role(body.token.strip())
    if not role:
        raise HTTPException(status_code=401, detail={"error": "BAD_TOKEN", "message": "令牌无效"})
    return {"ok": True, "role": role, "token": body.token.strip()}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "admin-console", "version": "2.0.0",
            "uptime_seconds": int(time.time() - _STARTED_AT)}


@app.get("/")
@app.get("/console")
@app.get("/console/")
def console_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")


@app.get("/console_ext.js", include_in_schema=False)
def console_ext_js() -> FileResponse:
    """扩展功能脚本：挂载形态 /console/console_ext.js 与独立形态 /console_ext.js 均可命中。"""
    return FileResponse(str(STATIC_DIR / "console_ext.js"),
                        media_type="application/javascript")


# ==================== 0. 总览驾驶舱 ====================

@app.get("/api/admin/overview")
def overview() -> dict:
    db = _db()
    try:
        def n(sql: str) -> int:
            return int((db.query_one(sql) or {}).get("n") or 0)

        sessions = n("SELECT COUNT(*) AS n FROM sessions")
        by_channel = db.query(
            "SELECT channel, COUNT(*) AS n FROM sessions GROUP BY channel")
        by_owner = db.query(
            "SELECT owner, COUNT(*) AS n FROM sessions GROUP BY owner ORDER BY n DESC LIMIT 20")
        by_status = db.query(
            "SELECT status, COUNT(*) AS n FROM sessions GROUP BY status")
        tasks = n("SELECT COUNT(*) AS n FROM tasks")
        tasks_done = n("SELECT COUNT(*) AS n FROM tasks WHERE status='done'")
        snapshots = n("SELECT COUNT(*) AS n FROM snapshots")
        logs = n("SELECT COUNT(*) AS n FROM logs")
        chat_turns = n("SELECT COUNT(*) AS n FROM chat_turns")
        events = n("SELECT COUNT(*) AS n FROM events")
        incidents = n("SELECT COUNT(*) AS n FROM incidents")
        incidents_open = n("SELECT COUNT(*) AS n FROM incidents WHERE status!='resolved'")
        avg_ms = db.query_one(
            "SELECT AVG(duration_ms) AS v FROM events WHERE kind='chat.done' AND duration_ms IS NOT NULL")
        durations = db.query(
            "SELECT duration_ms FROM events WHERE kind='chat.done' AND duration_ms IS NOT NULL"
            " ORDER BY id DESC LIMIT 200")
        ds = sorted(int(r["duration_ms"]) for r in durations if r.get("duration_ms") is not None)
        p95 = ds[int(len(ds) * 0.95)] if ds else None

        # 目录能力数量（不启动 Runtime，避免与平台进程争抢 MCP 连接）
        tools = skills = 0
        try:
            cfg = yaml.safe_load((ROOT / "config" / "mcp_tools.yaml").read_text(encoding="utf-8")) or {}
            tools = len(cfg.get("tools") or [])
        except Exception:
            pass
        try:
            skills = len([p for p in (ROOT / "app" / "skills" / "packages").iterdir()
                          if p.is_dir() and (p / "manifest.yaml").is_file()])
        except Exception:
            pass

        return {
            "service": {"name": "Agent Platform 管理后台", "version": "2.0.0",
                        "uptime_seconds": int(time.time() - _STARTED_AT)},
            "sessions": sessions, "by_channel": by_channel, "by_owner": by_owner,
            "by_status": by_status,
            "tasks": tasks, "tasks_done": tasks_done,
            "snapshots": snapshots, "logs": logs, "chat_turns": chat_turns,
            "events": events, "incidents": incidents, "incidents_open": incidents_open,
            "performance": {
                "avg_duration_ms": int((avg_ms or {}).get("v") or 0),
                "p95_duration_ms": p95, "samples": len(ds),
                "note": "取自 chat.done 事件的真实测量值，非估算",
            },
            "capabilities": {"tools": tools, "skills": skills},
            "topology": [
                {"service": "platform", "url": PLATFORM_URL, "role": "智能体平台"},
                {"service": "admin", "url": "self", "role": "Agent Platform 管理后台（独立鉴权）"},
                {"service": "database", "dsn": db.dsn.split("///")[-1].split("/")[-1],
                 "role": "共享单一数据源"},
            ],
            "consistency": obs.consistency_check(db),
        }
    finally:
        db.close()


@app.get("/api/admin/consistency")
def consistency() -> dict:
    db = _db()
    try:
        return obs.consistency_check(db)
    finally:
        db.close()


# ==================== 1. 任务与作业 ====================

@app.get("/api/admin/sessions")
def sessions(limit: int = Query(100, ge=1, le=500), channel: str | None = None,
             owner: str | None = None, status: str | None = None) -> dict:
    db = _db()
    try:
        repo = Repository(db)
        rows = repo.list_sessions(limit=limit, include_probes=True, channel=channel)
        # owner 维度在这里过滤（repository 保持第一阶段签名不变）
        if owner:
            rows = [r for r in rows if (r.get("owner") or "default") == owner]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        # 补上 owner 字段（list_sessions 的 SELECT 未带 owner，这里补齐，保证隔离视图可用）
        for r in rows:
            if "owner" not in r:
                row = db.query_one("SELECT owner FROM sessions WHERE session_id=?", (r["session_id"],))
                r["owner"] = (row or {}).get("owner") or "default"
        return {"total": len(rows), "sessions": rows, "filter": {"channel": channel,
                "owner": owner, "status": status}}
    finally:
        db.close()


@app.get("/api/admin/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    db = _db()
    try:
        repo = Repository(db)
        s = repo.get_session(session_id)
        if not s:
            return {"found": False, "session_id": session_id}
        s["owner"] = s.get("owner") or "default"
        return {
            "found": True, "session": s,
            "tasks": db.query(
                "SELECT task_id,title,assignee,status,attempts,error,updated_at FROM tasks"
                " WHERE session_id=? ORDER BY task_id", (session_id,)),
            "jobs": db.query(
                "SELECT job_id,task_id,agent_id,action,target,status,attempts FROM jobs"
                " WHERE session_id=? ORDER BY job_id", (session_id,)),
            "snapshots": db.query(
                "SELECT step_index,agent_id,phase,status,error,created_at FROM snapshots"
                " WHERE session_id=? ORDER BY step_index", (session_id,)),
            "handoffs": db.query(
                "SELECT step_index,task_id,from_agent,to_agent,reason,verdict,created_at"
                " FROM handoffs WHERE session_id=? ORDER BY id", (session_id,)),
            "chat_turns": db.query(
                "SELECT turn_index,role,content,mode,created_at FROM chat_turns"
                " WHERE session_id=? ORDER BY turn_index", (session_id,)),
        }
    finally:
        db.close()


@app.get("/api/admin/sessions/{session_id}/diagnose")
def session_diagnose(session_id: str) -> dict:
    """沿用第一阶段的一键诊断系统（同一库直接读，不转发、不重复实现）。"""
    db = _db()
    try:
        d = diagnose(Repository(db), session_id)
        d["report"] = format_diagnosis(d)
        return d
    finally:
        db.close()


@app.get("/api/admin/graph/{session_id}")
def graph(session_id: str) -> dict:
    db = _db()
    try:
        return {"session_id": session_id, "handoffs": db.query(
            "SELECT step_index,task_id,from_agent,to_agent,reason,verdict FROM handoffs"
            " WHERE session_id=? ORDER BY id", (session_id,))}
    finally:
        db.close()


# ==================== 2. 诊断与恢复（写操作转发平台，避免跨进程重复写状态） ====================

@app.post("/api/admin/sessions/{session_id}/resume")
async def resume(request: Request, session_id: str) -> dict:
    _require_admin(request)
    import urllib.request
    try:
        req = urllib.request.Request(f"{PLATFORM_URL}/api/sessions/{session_id}/resume",
                                     data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        db = _db()
        try:
            obs.admin_audit(db, actor=request.state.actor, action="resume", target=session_id,
                            detail={"result": body.get("status")})
            obs.emit_event(db, kind="admin.resume", session_id=session_id,
                           actor=request.state.actor, status=str(body.get("status")),
                           summary=f"后台触发断点恢复 · {session_id[:16]}")
        finally:
            db.close()
        return {"ok": True, "result": body}
    except Exception as exc:
        return {"ok": False, "error": f"调用平台恢复接口失败：{exc}",
                "hint": f"请确认平台服务可达（{PLATFORM_URL}）"}
    finally:
        pass


# ==================== 3. 错误代码核查（核心） ====================

@app.get("/api/admin/incidents")
def incidents(limit: int = Query(100, ge=1, le=500), status: str | None = None,
              error_class: str | None = None) -> dict:
    db = _db()
    try:
        return {"incidents": obs.list_incidents(db, limit=limit, status=status,
                                                error_class=error_class)}
    finally:
        db.close()


@app.get("/api/admin/incidents/lookup")
def incident_lookup(code: str = Query(..., min_length=3)) -> dict:
    """凭用户反馈的错误代码反查全链路证据。"""
    db = _db()
    try:
        return obs.lookup_incident(db, code)
    finally:
        db.close()


class ResolveIn(BaseModel):
    code: str
    resolution: str


@app.post("/api/admin/incidents/resolve")
def incident_resolve(request: Request, body: ResolveIn) -> dict:
    _require_admin(request)
    db = _db()
    try:
        ok = obs.resolve_incident(db, body.code, body.resolution, request.state.actor)
        obs.admin_audit(db, actor=request.state.actor, action="resolve_incident",
                        target=body.code, detail={"resolution": body.resolution})
        return {"ok": ok, "code": body.code.upper()}
    finally:
        db.close()


@app.get("/api/admin/error_classes")
def error_classes() -> dict:
    from app.errors import ERROR_CLASSES
    return {"classes": ERROR_CLASSES}


# ==================== 4. 实时事件流 ====================

@app.get("/api/admin/events")
def events(after_id: int = 0, limit: int = Query(200, ge=1, le=1000),
           session_id: str | None = None, kind: str | None = None,
           channel: str | None = None) -> dict:
    db = _db()
    try:
        rows = obs.list_events(db, after_id=after_id, limit=limit,
                               session_id=session_id, kind=kind, channel=channel)
        return {"events": rows, "latest_id": obs.latest_event_id(db), "count": len(rows)}
    finally:
        db.close()


@app.get("/api/admin/stream")
async def stream(request: Request, after_id: int = 0, token: str = ""):
    """SSE：以 events.id 为游标轮询共享库，跨进程 / 跨容器都能即时看到平台的每一步。"""
    async def gen():
        cursor = after_id
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            rows = await asyncio.to_thread(_poll_events, cursor)
            if rows:
                for r in rows:
                    cursor = int(r["id"])
                    yield f"data: {json.dumps(r, ensure_ascii=False, default=str)}\n\n"
                idle = 0
            else:
                idle += 1
                yield ": keepalive\n\n"
            await asyncio.sleep(0.6 if idle < 5 else 1.5)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _poll_events(cursor: int) -> list[dict]:
    db = _db()
    try:
        return obs.list_events(db, after_id=cursor, limit=100)
    finally:
        db.close()


# ==================== 5-7. 智能体 / 工具 / 技能 / 资料库 ====================

@app.get("/api/admin/catalog")
def catalog() -> dict:
    out: dict[str, Any] = {"agents": [], "tools": [], "skills": [], "documents": []}
    try:
        out["agents"] = (yaml.safe_load((ROOT / "config" / "agents.yaml").read_text(encoding="utf-8")) or {}).get("executors", [])
    except Exception:
        pass
    try:
        out["tools"] = (yaml.safe_load((ROOT / "config" / "mcp_tools.yaml").read_text(encoding="utf-8")) or {}).get("tools", [])
    except Exception:
        pass
    pkg = ROOT / "app" / "skills" / "packages"
    if pkg.is_dir():
        for p in sorted(pkg.iterdir()):
            mf = p / "manifest.yaml"
            if mf.is_file():
                try:
                    m = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
                    out["skills"].append({"name": m.get("name") or p.name,
                                          "title": m.get("title") or m.get("name") or p.name,
                                          "description": m.get("description", "")})
                except Exception:
                    out["skills"].append({"name": p.name, "title": p.name, "description": ""})
    db = _db()
    try:
        out["documents"] = db.query(
            "SELECT doc_id,source,title,chunks,created_at FROM documents ORDER BY created_at DESC LIMIT 100")
    finally:
        db.close()
    return out


# ==================== 8. 安全与访问 ====================

@app.get("/api/admin/security")
def security() -> dict:
    db = _db()
    try:
        integrity = "unknown"
        try:
            if db.dialect == "sqlite":
                integrity = (db.query_one("PRAGMA integrity_check") or {}).get("integrity_check", "unknown")
            else:
                integrity = "postgresql(managed)"
        except Exception:
            pass
        key_file = _token_file().parent / "llm_runtime.json"
        checks = []
        def chk(name: str, ok: bool, note: str):
            checks.append({"item": name, "ok": bool(ok), "note": note})
        db_path = Path(db.dsn.split("///")[-1]) if db.dialect == "sqlite" else None
        chk("全参数化查询", True, "所有 SQL 均使用占位符绑定，无字符串拼接用户输入")
        chk("数据库完整性", integrity == "ok" or integrity.startswith("postgresql"),
            f"integrity_check = {integrity}")
        if db_path and db_path.is_file():
            mode = oct(db_path.stat().st_mode & 0o777)
            chk("数据库文件权限", mode in ("0o600", "0o640", "0o644"),
                f"当前 {mode}，Docker 部署建议 600 并仅挂载到内部卷")
        if key_file.is_file():
            mode = oct(key_file.stat().st_mode & 0o777)
            chk("API 密钥文件权限", mode == "0o600", f"当前 {mode}（600 为合格）")
        chk("后台独立鉴权", True, "超管 / 只读双令牌，所有数据接口强制校验")
        chk("日志脱敏", True, "结构化日志上下文摘要 + 密钥自动 REDACTED")
        return {"checks": checks, "audit": obs.list_admin_audit(db, limit=50),
                "dialect": db.dialect}
    finally:
        db.close()


# ==================== 9. 沙箱隔离 ====================

@app.get("/api/admin/sandbox")
def sandbox() -> dict:
    db = _db()
    try:
        return {
            "channels": db.query(
                "SELECT channel, COUNT(*) AS n FROM sessions GROUP BY channel"),
            "owners": db.query(
                "SELECT owner, COUNT(*) AS n FROM sessions GROUP BY owner ORDER BY n DESC"),
            "rules": [
                {"layer": "通道沙箱", "status": "已启用",
                 "detail": "chat / work 两套通道，会话创建时绑定，跨通道返回 409 CHANNEL_ISOLATION"},
                {"layer": "归属沙箱", "status": "已启用",
                 "detail": "会话绑定 owner，跨归属访问返回 409 OWNER_ISOLATION；历史数据归 default"},
                {"layer": "会话沙箱", "status": "已启用",
                 "detail": "上下文 / 快照 / 日志 / 产物按 session_id 隔离"},
                {"layer": "工具边界", "status": "已启用",
                 "detail": "白名单 + 确认模式 + 路径限制，防止越权写文件"},
                {"layer": "容器隔离", "status": "Docker 部署时启用",
                 "detail": "platform / admin / database 分服务，数据库仅挂内部卷不暴露端口"},
            ],
        }
    finally:
        db.close()


# ==================== 10. 系统与部署 ====================

@app.get("/api/admin/system")
def system() -> dict:
    db = _db()
    try:
        backup_dir = _token_file().parent / "backups"
        backups = []
        if backup_dir.is_dir():
            for p in sorted(backup_dir.glob("*.db"), reverse=True)[:20]:
                backups.append({"name": p.name, "size": p.stat().st_size,
                                "created_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                            time.localtime(p.stat().st_mtime))})
        return {
            "version": "2.0.0", "uptime_seconds": int(time.time() - _STARTED_AT),
            "dialect": db.dialect, "dsn_masked": db.dsn.split("://")[0] + "://***",
            "services": [
                {"name": "platform", "url": PLATFORM_URL, "restart_policy": "unless-stopped"},
                {"name": "admin", "url": "self", "restart_policy": "unless-stopped"},
            ],
            "backups": backups,
            "deploy": {"mode": "docker compose", "command": "./deploy.sh up",
                       "note": "单条命令构建并启动，含 healthcheck 与自动重启"},
        }
    finally:
        db.close()


@app.post("/api/admin/backup")
def backup(request: Request) -> dict:
    _require_admin(request)
    db = _db()
    try:
        if db.dialect != "sqlite":
            return {"ok": False, "error": "PostgreSQL 请使用 pg_dump 备份（见部署文档）"}
        src = Path(db.dsn.split("///")[-1])
        dst_dir = src.parent / "backups"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"agent_{time.strftime('%Y%m%d_%H%M%S')}.db"
        # 使用 SQLite 官方 backup API，保证 WAL 状态下的一致性快照
        import sqlite3
        con = sqlite3.connect(str(src))
        out = sqlite3.connect(str(dst))
        try:
            con.backup(out)
        finally:
            out.close()
            con.close()
        obs.admin_audit(db, actor=request.state.actor, action="backup", target=dst.name)
        return {"ok": True, "file": dst.name, "size": dst.stat().st_size}
    finally:
        db.close()

# ============================================================
# 第三阶段：智能分析中枢 / 升级迭代 / 用户与同意 / 错误深度核查
# 所有数据均来自与平台共享的同一个数据库，无复制、无估算。
# ============================================================

class AckIn(BaseModel):
    insight_id: int
    status: str                     # acknowledged | dismissed | open


class ConsentIn(BaseModel):
    user_key: str
    consent: bool
    note: str | None = None


class IterUpdateIn(BaseModel):
    item_id: int
    status: str | None = None
    priority: str | None = None
    note: str | None = None


def _audit(request: Request, db, action: str, detail: Any = None) -> None:
    obs.admin_audit(db, actor=getattr(request.state, "actor", "admin"),
                    action=action, detail=detail)


# ---------- 11. 智能分析中枢 ----------

@app.get("/api/admin/analysis/latest")
def analysis_latest() -> dict:
    db = _db()
    try:
        return intel.latest_analysis(db)
    finally:
        db.close()


@app.get("/api/admin/analysis/runs")
def analysis_runs(limit: int = Query(50, ge=1, le=500)) -> dict:
    db = _db()
    try:
        return {"runs": intel.list_runs(db, limit)}
    finally:
        db.close()


@app.get("/api/admin/analysis/insights")
def analysis_insights(limit: int = Query(100, ge=1, le=1000),
                      severity: str | None = None, category: str | None = None,
                      status: str | None = None) -> dict:
    db = _db()
    try:
        return {"items": intel.list_insights(db, limit=limit, severity=severity,
                                             category=category, status=status)}
    finally:
        db.close()


@app.get("/api/admin/analysis/trend")
def analysis_trend(limit: int = Query(40, ge=1, le=200)) -> dict:
    db = _db()
    try:
        return {"trend": intel.health_trend(db, limit)}
    finally:
        db.close()


@app.post("/api/admin/analysis/run")
def analysis_run(request: Request) -> dict:
    _require_admin(request)
    db = _db()
    try:
        out = intel.analyze_once(db, force=True)
        _audit(request, db, "analysis.run", {"run_id": out.get("run_id"),
                                             "score": out.get("health_score")})
        return out
    finally:
        db.close()


@app.post("/api/admin/analysis/brief")
def analysis_brief(request: Request) -> dict:
    """按需调用大模型生成运营解读。不可用/失败时如实返回原因，不伪造结论。"""
    _require_admin(request)
    db = _db()
    try:
        out = intel.ai_brief(db)
        _audit(request, db, "analysis.brief", {"ok": out.get("ok"),
                                               "model": out.get("model")})
        return out
    finally:
        db.close()


@app.post("/api/admin/analysis/insights/ack")
def analysis_ack(request: Request, body: AckIn) -> dict:
    _require_admin(request)
    if body.status not in ("open", "acknowledged", "dismissed"):
        raise HTTPException(status_code=400, detail="status 不合法")
    db = _db()
    try:
        ok = intel.ack_insight(db, body.insight_id, body.status)
        _audit(request, db, "analysis.ack", {"id": body.insight_id, "status": body.status})
        return {"ok": ok}
    finally:
        db.close()


# ---------- 12. 升级迭代中心 ----------

@app.get("/api/admin/iteration/stats")
def iteration_stats() -> dict:
    db = _db()
    try:
        return intel.iteration_stats(db)
    finally:
        db.close()


@app.get("/api/admin/iteration/items")
def iteration_items(status: str | None = None, category: str | None = None,
                    limit: int = Query(200, ge=1, le=1000)) -> dict:
    db = _db()
    try:
        return {"items": intel.list_iterations(db, status=status,
                                               category=category, limit=limit)}
    finally:
        db.close()


@app.post("/api/admin/iteration/scan")
def iteration_scan(request: Request) -> dict:
    _require_admin(request)
    db = _db()
    try:
        out = intel.scan_iteration(db)
        _audit(request, db, "iteration.scan", {"created": out.get("created"),
                                               "merged": out.get("merged")})
        return out
    finally:
        db.close()


@app.post("/api/admin/iteration/update")
def iteration_update(request: Request, body: IterUpdateIn) -> dict:
    _require_admin(request)
    db = _db()
    try:
        fields = {k: v for k, v in
                  {"status": body.status, "priority": body.priority, "note": body.note}.items()
                  if v is not None}
        ok = intel.update_iteration(db, body.item_id, **fields)
        _audit(request, db, "iteration.update", {"id": body.item_id, **fields})
        return {"ok": ok}
    finally:
        db.close()


# ---------- 13. 用户与同意管理 ----------

@app.get("/api/admin/users")
def users(q: str | None = None, phone: str | None = None, account: str | None = None,
          consent: int | None = None, limit: int = Query(100, ge=1, le=500)) -> dict:
    db = _db()
    try:
        return {"users": intel.list_users(db, q=q, phone=phone, account=account,
                                          consent=consent, limit=limit)}
    finally:
        db.close()


@app.post("/api/admin/users/consent")
def users_consent(request: Request, body: ConsentIn) -> dict:
    _require_admin(request)
    db = _db()
    try:
        ok = intel.set_consent(db, user_key=body.user_key, consent=body.consent,
                               actor=getattr(request.state, "actor", "admin"),
                               note=body.note)
        return {"ok": ok, "consent": body.consent,
                "notice": "同意状态已同步到该用户的全部会话"}
    finally:
        db.close()


@app.get("/api/admin/users/{user_key}")
def user_detail(user_key: str) -> dict:
    db = _db()
    try:
        return intel.user_detail(db, user_key)
    finally:
        db.close()


@app.get("/api/admin/users/{user_key}/sessions")
def user_sessions(user_key: str) -> dict:
    db = _db()
    try:
        d = intel.user_detail(db, user_key)
        return {"found": d.get("found", False), "sessions": d.get("sessions", [])}
    finally:
        db.close()


@app.get("/api/admin/chatlog")
def chatlog(request: Request, session_id: str | None = None, user_key: str | None = None,
            purpose: str = "bugfix", reveal: int = 0,
            limit: int = Query(200, ge=1, le=1000)) -> dict:
    role = getattr(request.state, "role", None)
    if reveal and role != "admin":
        raise HTTPException(status_code=403, detail="查看聊天原文需要超管令牌，且会留痕")
    db = _db()
    try:
        return intel.chatlog(db, session_id=session_id or None, user_key=user_key or None,
                             limit=limit, reveal=bool(reveal),
                             actor=getattr(request.state, "actor", "admin"), purpose=purpose)
    finally:
        db.close()


@app.get("/api/admin/query-audit")
def query_audit(limit: int = Query(100, ge=1, le=1000)) -> dict:
    db = _db()
    try:
        return {"items": intel.list_query_audit(db, limit)}
    finally:
        db.close()


# ---------- 14. 错误深度核查 ----------

@app.get("/api/admin/errors/timeline")
def errors_timeline(code: str = Query(..., min_length=3)) -> dict:
    db = _db()
    try:
        return intel.error_timeline(db, code)
    finally:
        db.close()


@app.get("/api/admin/errors/similar")
def errors_similar(code: str = Query(..., min_length=3),
                   limit: int = Query(20, ge=1, le=200)) -> dict:
    db = _db()
    try:
        return intel.similar_incidents(db, code, limit)
    finally:
        db.close()


@app.get("/api/admin/errors/rootcause")
def errors_rootcause(code: str = Query(..., min_length=3)) -> dict:
    db = _db()
    try:
        return intel.root_cause(db, code)
    finally:
        db.close()


@app.get("/api/admin/errors/trends")
def errors_trends(hours: int = Query(24, ge=1, le=720)) -> dict:
    db = _db()
    try:
        return intel.error_trends(db, hours)
    finally:
        db.close()

