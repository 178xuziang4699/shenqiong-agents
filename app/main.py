"""最小 Web 接入层（FastAPI）：命令行已能跑通，这里只是把同一套 runtime 暴露成 HTTP。

刻意保持极薄：路由函数只做参数转换与错误码映射，业务全在 runtime 里。
前端是 app/static 下的三个静态文件（index.html / app.css / app.js），不走模板引擎。
"""

from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.display import enrich
from app.errors import AgentInterrupt, AgentPlatformError, classify
from app.llm import optimize_prompt
from app.obs import console as obs
from app.obs import intelligence as intel
from app.runtime import Runtime
from app.tools.registry import ToolContext

STATIC_DIR = Path(__file__).parent / "static"

_rt: Runtime | None = None


def get_rt() -> Runtime:
    global _rt
    if _rt is None:
        _rt = Runtime(echo=False)
        _rt.startup()
    return _rt


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_rt()
    # 第三阶段：常驻智能分析引擎（规则驱动，周期扫描真实库表）
    intel.start_background_loop()
    yield
    if _rt is not None:
        _rt.shutdown()


app = FastAPI(title="Agent Platform", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """兜底：任何未归类异常都生成一个「可反馈错误代码」并落库。

    用户把 AP-YYYYMMDD-XXXXXX 报给管理员，管理后台凭码即可反查到
    会话 / 步骤 / 快照 / 日志 / 工具审计，实现需求里的「错误代码核查」。
    """
    db = None
    code = "AP-UNAVAILABLE"
    error_class = classify(exc)
    session_id = None
    try:
        # 尽力从请求体取 session_id（只读 body，不消费后续流），让事故能关联到证据链
        raw = getattr(request, "_body", None)
        if raw:
            try:
                session_id = (json.loads(raw) or {}).get("session_id")
            except Exception:
                pass
        rt = get_rt()
        db = rt.db
        code = obs.raise_incident(
            db, error_class=error_class, message=str(exc)[:2000],
            session_id=session_id, level="ERROR", source=f"http.{request.url.path}",
            stack="".join(__import__("traceback").format_exception(
                type(exc), exc, exc.__traceback__))[-4000:],
            context={"method": request.method, "path": request.url.path},
        )
    except Exception:  # 诊断设施本身失败，也不能把 500 变成第二次异常
        pass
    return JSONResponse(
        status_code=500,
        content={
            "error_code": code,           # 可反馈给管理员的错误代码
            "error_class": error_class,   # 内部归类，用于后台筛选
            "session_id": session_id,
            "message": str(exc)[:500],
            "hint": "请记录此错误代码并反馈给管理员，可在管理后台「错误代码核查」中凭码定位。",
        },
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """浏览器默认请求，返回空成功响应，避免控制台出现 404。"""
    return Response(status_code=204)


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    skill: str | None = None      # 点击技能卡片时自动带上，走「技能直达」分支
    channel: str | None = None    # 显式沙箱通道：chat | work，只能由用户选择，不能由文本推断
    owner: str | None = None      # 用户/租户归属；缺省 default，与历史数据一致
    user_account: str | None = None  # 用户账号（排查检索键，可空）
    user_phone: str | None = None    # 用户手机号（排查检索键，可空）
    consent: bool = False            # 是否同意将聊天内容用于升级迭代分析


class OptimizeIn(BaseModel):
    draft: str                    # 用户正在手写的草稿
    context: str = ""             # 可选上下文（如已选技能、已附资料）


@app.get("/health")
def health() -> dict:
    rt = get_rt()
    return {
        "status": "ok",
        "tools": len(rt.registry.tools),
        "skills": len(rt.skills.catalogue()),
        "agents": len(rt.executors) + len(rt.managers) + 1,
        "sessions": len(rt.repo.list_sessions(limit=500)),
        "db": rt.settings.db_dsn,
        "warnings": rt.registry.warnings,
        "confirm_mode": rt.confirmer.mode,
        "llm": rt.llm.describe(),
    }


def _record_incident(rt: Runtime, error_class: str, message: str,
                     source: str, session_id: str | None = None) -> str:
    """把已知异常也登记成可反馈错误代码。诊断设施失败时降级为占位码，不影响主流程。

    必须带上 session_id 与当前步号：用户凭码反馈时，后台才能反查到
    该会话的快照、日志、工具审计与事件流，形成完整证据链。
    """
    try:
        step_index = None
        if session_id:
            row = rt.repo.get_session(session_id)
            if row:
                step_index = row.get("step_index")
        return obs.raise_incident(rt.db, error_class=error_class, message=message,
                                  session_id=session_id, step_index=step_index,
                                  source=source)
    except Exception:
        return "AP-UNAVAILABLE"


def _emit_chat_event(rt: Runtime, out: dict, elapsed_ms: int) -> None:
    """每完成一轮前端任务就写一条事件；后台与前端共享同一库，写完即可查。

    这是「前端每执行一次任务，后台即时看到」的核心：不复制、不同步，
    后台读的就是前端刚写入的同一张表。事件流只是把「可查」升级为「推送」。
    """
    try:
        action = (out.get("decision") or {}).get("action") or out.get("mode") or "reply"
        obs.emit_event(
            rt.db, kind="chat.done", session_id=out.get("session_id"),
            channel=out.get("channel"), actor="supervisor",
            status=str(out.get("status") or "completed"),
            summary=f"动作 {action} · 耗时 {elapsed_ms}ms",
            payload={"action": action, "dispatched": out.get("dispatched")},
            duration_ms=elapsed_ms,
        )
    except Exception:
        pass  # 事件是观测增强，绝不能因为写入失败让应答失败


def _chat_artifacts(out: dict) -> list[dict]:
    """从办事结果里挑出代码 / 文档产物，供前端展示预览与下载。"""
    results = (out.get("outcome") or {}).get("results") or {}
    artifacts: list[dict] = []
    for tid, payload in results.items():
        if not isinstance(payload, dict):
            continue
        if "code" in payload and "filename" in payload:
            artifacts.append({
                "task_id": tid, "path": payload.get("filename"),
                "language": payload.get("language"), "lines": payload.get("lines"),
                "bytes": payload.get("bytes"), "source_zh": payload.get("source_zh"),
                "written": False,
            })
    written_paths: set[str] = set()
    for payload in results.values():
        if not isinstance(payload, dict) or "path" not in payload or "bytes" not in payload:
            continue
        path = payload.get("path")
        for art in artifacts:
            if art["path"] == path:
                art["written"] = True
                art["written_bytes"] = payload["bytes"]
                written_paths.add(path)
    # 报告 / 数据类产物（没有 code 字段）也给出可查看、可下载的文件行
    for tid, payload in results.items():
        if not isinstance(payload, dict):
            continue
        path = payload.get("path")
        if not path or "bytes" not in payload or path in written_paths:
            continue
        if any(a.get("path") == path for a in artifacts):
            continue
        is_doc = str(path).lower().endswith((".md", ".txt", ".csv", ".json", ".html"))
        artifacts.append({
            "task_id": tid, "path": path,
            "language": "文档" if is_doc else "文件",
            "lines": payload.get("lines"), "bytes": payload.get("bytes"),
            "written": True, "written_bytes": payload["bytes"],
            "source_zh": "工作流写入", "kind": "document" if is_doc else "file",
        })
    return artifacts


def _stamp_elapsed(rt: Runtime, session_id: str, elapsed_ms: int) -> None:
    """把本轮真实耗时补写进最后一条 chat.respond 快照，刷新回放也能显示「已思考 Xs」。

    只追加一个字段，不改原有快照内容；写失败不影响本轮应答。
    """
    try:
        row = rt.repo.db.query_one(
            "SELECT step_index, output FROM snapshots WHERE session_id = ?"
            " AND phase = 'chat.respond' ORDER BY step_index DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return
        payload = json.loads(row["output"]) if row["output"] else {}
        payload["elapsed_ms"] = elapsed_ms
        rt.repo.db.execute(
            "UPDATE snapshots SET output = ? WHERE session_id = ? AND step_index = ?",
            (json.dumps(payload, ensure_ascii=False), session_id, row["step_index"]),
        )
    except Exception:
        pass


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    rt = get_rt()
    started = time.perf_counter()
    # 先确定会话 ID 并贯穿全程：异常时也要用它登记事故，保证错误代码能反查到证据链
    sid = body.session_id or rt.new_session_id("web")
    try:
        out = rt.supervisor.handle(sid, body.message, skill=body.skill,
                                   channel=body.channel or "chat",
                                   owner=body.owner or "default")
    except AgentInterrupt as exc:
        code = _record_incident(rt, "INTERRUPTED", str(exc), "api.chat", session_id=sid)
        raise HTTPException(
            status_code=409,
            detail={"error_code": "INTERRUPTED", "incident_code": code,
                    "message": str(exc), "session_id": sid},
        )
    except AgentPlatformError as exc:
        code = _record_incident(rt, exc.error_code, exc.message, "api.chat", session_id=sid)
        # 沙箱隔离类错误统一 409 Conflict，与第一阶段跨通道语义保持一致
        status = 409 if exc.error_code in ("CHANNEL_ISOLATION", "OWNER_ISOLATION") else 400
        raise HTTPException(status_code=status, detail={
            **exc.to_dict(), "incident_code": code, "session_id": sid})

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _stamp_elapsed(rt, out["session_id"], elapsed_ms)
    # 第三阶段：绑定用户身份与同意状态；未传身份时不影响任何历史行为，
    # 绑定失败也绝不影响对话主链路（辅助能力降级，不冒充成功）。
    if body.user_account or body.user_phone:
        try:
            intel.bind_user(rt.db, session_id=out["session_id"],
                            account=body.user_account, phone=body.user_phone,
                            consent=bool(body.consent))
        except Exception:
            pass

    # 统一对话历史：reply / document / skill / task 四种动作都写同一张表，
    # 办事模式之后追问也能拿到上下文；本轮 user 写在应答之后，模型看到的
    # history 天然不含本轮，避免重复。mode 列记录「本轮真实动作」，便于回放核对。
    sid = out["session_id"]
    action_tag = (out.get("decision") or {}).get("action") or out["mode"]
    rt.repo.add_chat_turn(sid, "user", body.message, action_tag)
    rt.repo.add_chat_turn(sid, "assistant", out.get("answer") or "", action_tag)
    obs.emit_event(rt.db, kind="chat.turn", session_id=sid,
                   channel=out.get("channel"), actor="user",
                   status="recorded", summary="多轮上下文已写入统一历史表",
                   payload={"action": action_tag})

    result = {
        "session_id": out["session_id"],
        "mode": out["mode"],                     # 派发标记：chat(直答/文档/技能) | task(多Agent编排)，前端据此渲染
        "channel": out.get("channel", "chat"),  # 沙箱通道：chat | work（不可变）
        "action": (out.get("decision") or {}).get("action", "reply"),
        "dispatched": out["dispatched"],
        "elapsed_ms": elapsed_ms,
    }
    if out["mode"] == "chat":
        result["answer"] = out["answer"]
        result["citations"] = out.get("citations", [])
        result["route"] = (out.get("decision") or {}).get("reason")
        result["engine"] = out.get("engine")
        result["skill"] = out.get("skill")
        result["skill_ok"] = out.get("skill_ok")
        result["skill_raw"] = out.get("skill_raw")
        result["office"] = out.get("office")
        result["artifacts"] = out.get("artifacts", [])
    else:
        result["status"] = out["status"]
        result["answer"] = out.get("answer") or ""
        result["route"] = (out.get("decision") or {}).get("reason")
        result["engine"] = rt.llm.describe()
        outcome = out["outcome"]
        failed_ids = {f["task_id"] for f in outcome.get("failed", [])}
        skipped_ids = {s["task_id"] for s in outcome.get("skipped", [])}
        result["plan"] = [
            {"task_id": t["task_id"], "title": t["title"], "assignee": t["assignee"],
             "depends_on": t["depends_on"], "parallel_group": t.get("parallel_group"),
             "status": ("failed" if t["task_id"] in failed_ids else
                        "skipped" if t["task_id"] in skipped_ids else "done")}
            for t in out["plan"]["subtasks"]
        ]
        result["outcome"] = {
            "status": out["outcome"]["status"],
            "skipped": out["outcome"].get("skipped", []),
            "failed": out["outcome"].get("failed", []),
        }
        result["artifacts"] = _chat_artifacts(out)
    _emit_chat_event(rt, out, elapsed_ms)
    return result


@app.get("/api/sessions")
def sessions(limit: int = Query(30, ge=1, le=500),
             channel: str | None = Query(None)) -> list[dict]:
    """channel 传入时只返回该沙箱通道的会话，不传则返回全部（运维管理页用）。"""
    return get_rt().repo.list_sessions(limit=limit, channel=channel)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    """删除一个会话的全部数据（级联清 plans/tasks/jobs/handoffs/logs/snapshots）。"""
    rt = get_rt()
    if session_id.startswith(("capability_probe_", "web_probe_")):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "FORBIDDEN", "message": "内部探针会话不可删除"},
        )
    n = rt.repo.delete_session(session_id)
    return {"ok": True, "deleted": n}


@app.post("/api/optimize")
def optimize(body: OptimizeIn) -> dict:
    """实时提示词优化：把手写草稿改写成标准提示词。

    - 已接模型：由模型改写（更自然、贴合原意）。
    - 未接模型 / 模型不可用：降级到离线规则（清理口语词 + 按意图补成角色/任务/约束）。
    返回 (optimized, source)，source 标识来源，前端据此标注。
    """
    rt = get_rt()
    draft = (body.draft or "").strip()
    if not draft:
        return {"ok": True, "optimized": "", "source": "empty"}
    optimized, source = optimize_prompt(rt.llm, draft, body.context)
    return {"ok": True, "optimized": optimized, "source": source}


@app.get("/api/sessions/{session_id}/replay")
def replay(session_id: str) -> dict:
    rt = get_rt()
    return {"session_id": session_id, "chain": rt.replay(session_id)}


@app.get("/api/sessions/{session_id}/tasks")
def tasks(session_id: str) -> dict:
    """两级分派的结果：子任务 + 其下作业。"""
    rt = get_rt()
    rows = rt.repo.list_tasks(session_id)
    out = []
    for t in rows:
        jobs = rt.repo.list_jobs(session_id, t["task_id"])
        out.append({
            "task_id": t["task_id"], "title": t["title"], "assignee": t["assignee"],
            "status": t["status"], "depends_on": t["depends_on"], "attempts": t["attempts"],
            "jobs": [{
                "job_id": j["job_id"], "agent_id": j["agent_id"], "action": j["action"],
                "target": j["target"], "status": j["status"], "attempts": j["attempts"],
            } for j in jobs],
        })
    plan = rt.repo.get_plan(session_id)
    return {
        "session_id": session_id,
        "plan": None if not plan else {
            "intent": plan.intent, "subtasks": [s.to_dict() for s in plan.subtasks],
        },
        "tasks": out,
    }


@app.get("/api/sessions/{session_id}/handoffs")
def handoffs(session_id: str) -> dict:
    return {"session_id": session_id, "handoffs": get_rt().repo.list_handoffs(session_id)}


@app.get("/api/sessions/{session_id}/graph")
def graph(session_id: str) -> dict:
    return {"session_id": session_id, "graph": get_rt().graph(session_id)}


@app.get("/api/sessions/{session_id}/logs")
def logs(session_id: str, limit: int = Query(200, ge=1, le=2000)) -> dict:
    rows = get_rt().repo.list_logs(session_id)
    return {"session_id": session_id, "total": len(rows), "logs": rows[-limit:]}


@app.get("/api/sessions/{session_id}/audit")
def audit(session_id: str) -> dict:
    return {"session_id": session_id, "audit": get_rt().repo.list_audit(session_id)}


@app.get("/api/sessions/{session_id}/diagnose")
def diagnose(session_id: str) -> dict:
    rt = get_rt()
    diag = rt.diagnose(session_id)
    diag["report"] = rt.diagnose_text(session_id)
    return diag


@app.post("/api/sessions/{session_id}/resume")
def resume(session_id: str) -> dict:
    rt = get_rt()
    try:
        out = rt.resume(session_id)
    except AgentInterrupt as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_code": "INTERRUPTED", "message": str(exc)},
        )
    except AgentPlatformError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict())
    return {
        "ok": out.get("ok", False),
        "resumed": out.get("resumed", False),
        "action": out.get("action"),
        "reused_tasks": out.get("reused_tasks", []),
        "outcome_status": (out.get("outcome") or {}).get("status"),
        "report": out.get("report"),
        "reason": out.get("reason") or out.get("error"),
    }


@app.get("/api/routing")
def routing(sample: str = "") -> dict:
    """暴露 config/routing.yaml 的判定规则，供界面上的「自动路由」下拉展示。

    带 sample 时顺带返回该文本的预判结果——只读，不改变任何会话状态。
    """
    rt = get_rt()
    cfg = rt.settings.routing or {}
    rules = []
    for r in cfg.get("rules", []) or []:
        m = r.get("match") or {}
        rules.append({
            "name": r.get("name"),
            "mode": r.get("mode"),
            "weight": r.get("weight"),
            "match_type": m.get("type"),
            "patterns": list(m.get("patterns") or []),
        })
    out = {
        "source": "config/routing.yaml",
        "active": False,
        "note": ("已下线：关键词规则不参与任何模式判定。通道（chat/work）由用户显式选择，"
                 "会话创建后不可变；消息内容只在通道内决定本轮动作。此处仅作可观测参考。"),
        "mechanism": "（历史机制）加权投票；现已不参与分流",
        "default_mode": cfg.get("default_mode"),
        "min_margin": cfg.get("min_margin"),
        "max_input_chars": cfg.get("max_input_chars"),
        "rules": rules,
    }
    if sample.strip():
        d = rt.router.decide(sample.strip())
        out["preview"] = {"sample": sample.strip(), **d.to_dict()}
    return out


@app.get("/api/knowledge")
def knowledge(q: str = "", top_k: int = Query(5, ge=1, le=20)) -> dict:
    """资料库：已入库文档清单 + 可选的统一检索（结果带来源引用）。"""
    rt = get_rt()
    docs = rt.db.query(
        "SELECT doc_id, source, title, chunks, created_at FROM documents ORDER BY created_at DESC"
    )
    out = {
        "documents": docs,
        "chunk_count": rt.repo.count_chunks(),
        "source": "SQLite: documents / knowledge_chunks",
    }
    if q.strip():
        from app.knowledge.retriever import search
        out["search"] = search(rt.repo, q.strip(), top_k=top_k)
    return out


@app.get("/api/llm")
def llm_status() -> dict:
    """当前模型接入状态 + Ollama 可用模型清单，供界面显示与切换。"""
    return get_rt().llm.describe()


class ModelIn(BaseModel):
    model: str


@app.post("/api/llm/model")
def llm_set_model(body: ModelIn) -> dict:
    """运行时切换模型（不落盘；固化请写 AP_LLM_MODEL 环境变量）。"""
    rt = get_rt()
    name = body.model.strip()
    if not name:
        raise HTTPException(status_code=400, detail={"error_code": "SCHEMA_INVALID",
                                                    "message": "模型名不能为空"})
    available = [m["name"] for m in rt.llm.list_models()]
    if available and name not in available:
        raise HTTPException(status_code=400, detail={
            "error_code": "MODEL_NOT_FOUND", "message": f"模型 {name} 不在 Ollama 清单中",
            "available": available,
        })
    rt.llm.set_model(name)
    return {"ok": True, "llm": rt.llm.describe()}


class LLMConfigIn(BaseModel):
    base_url: str
    provider: str = "auto"
    model: str = ""
    api_key: str = ""
    clear_key: bool = False


# 常见服务商预设：只提供地址与默认模型名，绝不内置任何凭据
LLM_PRESETS: list[dict] = [
    {"id": "deepseek", "name": "DeepSeek 官方 API",
     "base_url": "https://api.deepseek.com/v1", "provider": "openai",
     "model": "deepseek-chat", "need_key": True,
     "hint": "在 platform.deepseek.com 创建 API Key，填进下方 API Key 输入框即可；国内网络可直连。"},
    {"id": "openai", "name": "OpenAI",
     "base_url": "https://api.openai.com/v1", "provider": "openai",
     "model": "gpt-4o-mini", "need_key": True,
     "hint": "填 sk- 开头的 Key；需要服务所在机器能访问 api.openai.com。"},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "base_url": "https://api.moonshot.cn/v1", "provider": "openai",
     "model": "moonshot-v1-8k", "need_key": True,
     "hint": "在 platform.moonshot.cn 创建 API Key，地址以 /v1 结尾。"},
    {"id": "custom", "name": "自定义 OpenAI 兼容服务",
     "base_url": "", "provider": "openai", "model": "",
     "need_key": True,
     "hint": "填完整的 .../v1 地址与模型名，适用于自建 vLLM、一体机、企业网关等。"},
    {"id": "ollama", "name": "Ollama 本机（私有模型）",
     "base_url": "http://127.0.0.1:11434", "provider": "ollama",
     "model": "", "need_key": False,
     "hint": "本机运行 Ollama 时填这个地址，模型名留空会自动挑参数规模最大的 DeepSeek。"},
]


@app.get("/api/llm/presets")
def llm_presets() -> list[dict]:
    """界面「添加我的 API」下拉用的服务商预设，不含任何密钥。"""
    return LLM_PRESETS


@app.post("/api/llm/config")
def llm_config(body: LLMConfigIn) -> dict:
    """运行时接入自己的模型 API（只存进程内存，不落盘）。

    密钥只走两处：环境变量 AP_LLM_API_KEY（.env），或本接口的内存赋值；
    不写文件、不写数据库、不进结构化日志。api_key 留空表示「保留已填的密钥」，
    要清除请显式传 clear_key=true，或调用 /api/llm/clear。
    """
    import re as _re
    rt = get_rt()
    url = body.base_url.strip().rstrip("/")
    if not _re.match(r"^https?://[^\s]+$", url):
        raise HTTPException(status_code=400, detail={
            "error_code": "SCHEMA_INVALID", "message": "地址必须形如 https://主机/v1",
        })
    llm = rt.llm
    llm.cfg["base_url"] = url
    llm.cfg["provider"] = body.provider.strip() or "auto"
    llm.cfg["model"] = body.model.strip()
    if body.clear_key:
        llm.cfg["api_key"] = ""
    elif body.api_key.strip():
        llm.cfg["api_key"] = body.api_key.strip()
    llm.invalidate()
    return {"ok": True, "llm": llm.describe()}


@app.post("/api/llm/test")
def llm_test() -> dict:
    """真实调用一次模型，验证地址 / 密钥 / 模型名三件事是否都正确。"""
    return get_rt().llm.probe()


@app.post("/api/llm/clear")
def llm_clear() -> dict:
    """断开模型 API，恢复离线规则应答，并清掉内存里的地址与密钥。"""
    llm = get_rt().llm
    llm.cfg["base_url"] = ""
    llm.cfg["api_key"] = ""
    llm.cfg["model"] = ""
    llm.invalidate()
    return {"ok": True, "llm": llm.describe()}


@app.get("/api/tools")
def tools() -> list[dict]:
    return [enrich(r, "tool") for r in get_rt().registry.catalogue()]


@app.get("/api/skills")
def skills() -> list[dict]:
    return [enrich(r, "skill") for r in get_rt().skills.catalogue()]


# ==================== 能力目录：技能 / MCP 工具 / 专家 / 宿主对照 ====================
_ROOT = Path(__file__).resolve().parents[1]


@app.get("/api/office/types")
def office_types() -> dict:
    """办公引擎支持的文档类型，供首页能力展示与技能卡片使用。"""
    from app.office import OfficeEngine
    return {"total": len(OfficeEngine.catalogue()), "types": OfficeEngine.catalogue()}


@app.get("/api/artifact")
def artifact(path: str = Query(..., min_length=1)) -> dict:
    """只读查看办事产物（代码 / 报告），路径限制在产物目录内，防止越界读取。"""
    root = _ROOT.resolve()
    target = (root / path).resolve()
    allowed = [root / "output", root / "var" / "capability_probe", root / "samples"]
    if not any(target == a or a in target.parents for a in allowed):
        raise HTTPException(status_code=400, detail={
            "error_code": "PATH_FORBIDDEN",
            "message": "只允许查看 output / samples / var/capability_probe 下的文件",
        })
    if not target.is_file():
        raise HTTPException(status_code=404, detail={
            "error_code": "NOT_FOUND", "message": f"文件不存在：{path}",
        })
    size = target.stat().st_size
    if size > 200000:
        raise HTTPException(status_code=413, detail={
            "error_code": "TOO_LARGE", "message": "文件超过 200KB，请在服务器上查看",
        })
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "content": text, "bytes": size,
            "lines": len(text.splitlines())}


def _report() -> dict:
    p = _ROOT / "var" / "capability_report.json"
    if not p.exists():
        return {"summary": {}, "skills": [], "tools": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"summary": {}, "skills": [], "tools": []}


def _schema_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "string"


def _host_catalog() -> dict:
    p = _ROOT / "config" / "workbuddy_catalog.yaml"
    if not p.exists():
        return {"host_skills": [], "connectors": []}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


@app.get("/api/capabilities")
def capabilities() -> dict:
    from app.skills._ops import OPS
    rt = get_rt()
    if not rt.skills.packages:
        rt.skills.discover()
    rep = _report()
    verified_skills = {x["name"]: x for x in rep.get("skills", [])}
    verified_tools = {x["name"]: x for x in rep.get("tools", [])}

    skills = []
    for name, pkg in sorted(rt.skills.packages.items()):
        m = pkg.manifest or {}
        v = verified_skills.get(name, {})
        skills.append({
            "name": name,
            "description": pkg.description,
            "category": m.get("category") or "未分类",
            "color": m.get("color") or "#475569",
            "origin": m.get("origin") or "local",
            "permission": pkg.permissions,
            "timeout_seconds": pkg.timeout_seconds,
            "triggers": pkg.triggers,
            "sample_args": m.get("sample_args") or {},
            "verified": bool(v.get("ok")),
            "last_output": v.get("output", ""),
            "last_ms": v.get("ms"),
            "last_error": v.get("error"),
        })

    tools = []
    for spec in rt.registry.catalogue():
        v = verified_tools.get(spec["name"], {})
        tools.append({
            "name": spec["name"],
            "description": spec.get("description", ""),
            "permission": spec.get("permission"),
            "timeout_seconds": spec.get("timeout_seconds"),
            "server": spec.get("server"),
            "transport": spec.get("transport"),
            "input_schema": spec.get("input_schema"),
            "sample_args": v.get("sample_args") or {},
            "verified": bool(v.get("ok")),
            "last_output": v.get("output", ""),
            "last_ms": v.get("ms"),
            "last_error": v.get("error"),
        })

    experts = [{"id": rt.supervisor.agent_id, "role": "总控",
                "description": "意图解析、任务拆解、按计划分派与收口"}]
    for aid, mgr in rt.managers.items():
        experts.append({"id": aid, "role": "管理者",
                        "description": f"接收总控指派，二次派发作业并校验产出（{mgr.name}）"})
    for aid, exe in rt.executors.items():
        experts.append({"id": aid, "role": "执行者",
                        "description": f"执行具体作业，工具调用统一经 MCP 注册表（{exe.name}）"})

    host = _host_catalog()
    host_skills = host.get("host_skills") or []
    host_connectors = host.get("connectors") or []
    return {
        "summary": {
            "skills": len(skills), "skills_verified": sum(1 for x in skills if x["verified"]),
            "tools": len(tools), "tools_verified": sum(1 for x in tools if x["verified"]),
            "experts": len(experts),
            "host_skills": len(host_skills),
            "host_connectors": len(host_connectors),
            "report": rep.get("summary", {}),
            "generated_at": rep.get("generated_at"),
        },
        "skills": [enrich(x, "skill") for x in skills],
        "tools": [enrich(x, "tool") for x in tools],
        "experts": [enrich(x, "expert") for x in experts],
        "impl_options": sorted(OPS.keys()),
        "host_skills": [enrich(x, "host") for x in host_skills],
        "host_connectors": [enrich(x, "connector") for x in host_connectors],
    }


class ProbeIn(BaseModel):
    kind: str                       # skill | tool
    name: str
    args: dict | None = None


@app.post("/api/capabilities/probe")
def capability_probe(body: ProbeIn) -> dict:
    """真实试运行：技能经 SkillLoader.run，工具经 ToolRegistry.call（含校验/确认/审计）。"""
    rt = get_rt()
    if body.kind not in ("skill", "tool"):
        raise HTTPException(status_code=400, detail={"error_code": "SCHEMA_INVALID",
                                                    "message": "kind 必须是 skill 或 tool"})
    args: dict[str, Any] = body.args if body.args is not None else {}
    if body.args is None:
        rep = _report()
        bucket = rep.get("skills" if body.kind == "skill" else "tools", [])
        args = dict(next((x.get("sample_args") or {} for x in bucket if x["name"] == body.name), {}))

    sid = f"web_probe_{int(time.time() * 1000)}"
    rt.repo.create_session(sid, "probe", f"web probe {body.kind}:{body.name}")
    step = rt.repo.current_step(sid)
    rt.log.info(
        "capabilities", f"试运行 {body.kind}:{body.name}",
        session_id=sid, agent_id="catalog", step_index=step,
        context={"args": args},
    )
    t0 = time.time()
    try:
        if body.kind == "skill":
            out = rt.skills.run(body.name, args, {"source": "web_probe"})
        else:
            out = rt.registry.call(
                body.name, args,
                ToolContext(session_id=sid, agent_id="catalog", job_id="probe"),
            )
        return {"ok": True, "kind": body.kind, "name": body.name,
                "ms": round((time.time() - t0) * 1000), "args": args, "output": out}
    except AgentPlatformError as exc:
        rt.log.error("capabilities", f"试运行失败 {body.name}: {exc}", session_id=sid,
                     agent_id="catalog", step_index=step, error_code=exc.error_code)
        return {"ok": False, "kind": body.kind, "name": body.name,
                "ms": round((time.time() - t0) * 1000), "args": args,
                "error_code": exc.error_code, "error": exc.message}
    except Exception as exc:  # noqa: BLE001 - 试运行异常必须回报，禁止静默
        rt.log.error("capabilities", f"试运行异常 {body.name}: {exc}", session_id=sid,
                     agent_id="catalog", step_index=step, error_code="PROBE_ERROR", exc=exc)
        return {"ok": False, "kind": body.kind, "name": body.name,
                "ms": round((time.time() - t0) * 1000), "args": args,
                "error_code": "PROBE_ERROR", "error": f"{type(exc).__name__}: {exc}"}


class CreateSkillIn(BaseModel):
    name: str
    description: str
    category: str = "自定义"
    impl: str
    sample_args: dict = {}


@app.post("/api/capabilities/create")
def create_skill(body: CreateSkillIn) -> dict:
    """可插拔实证：运行时生成一个新的技能包目录并重新扫描加载。写操作，需人工确认。"""
    from app.skills._ops import OPS
    rt = get_rt()
    name = body.name.strip()
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise HTTPException(status_code=400, detail={"error_code": "SCHEMA_INVALID",
                                                    "message": "技能名只允许小写字母、数字、下划线"})
    if body.impl not in OPS:
        raise HTTPException(status_code=400, detail={"error_code": "SCHEMA_INVALID",
                                                    "message": f"实现体 {body.impl} 不在可用能力表中"})
    pkg_dir = _ROOT / "app" / "skills" / "packages" / name
    if pkg_dir.exists():
        raise HTTPException(status_code=409, detail={"error_code": "ALREADY_EXISTS",
                                                    "message": f"技能包 {name} 已存在"})

    approved, note = rt.confirmer.confirm(
        f"新建技能包 {name}", {"impl": body.impl, "category": body.category,
                              "sample_args": body.sample_args},
    )
    if not approved:
        raise HTTPException(status_code=403, detail={"error_code": "HUMAN_REJECTED",
                                                    "message": f"人工确认未放行（{note}）"})
    pkg_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "name": name, "version": "1.0.0", "description": body.description,
        "entry": "skill.py:run",
        "triggers": {"intents": [], "keywords": [], "regex": [], "require_all": False},
        "inputs": {"type": "object", "required": [],
                   "properties": {k: {"type": _schema_type(v)}
                                  for k, v in body.sample_args.items()}},
        "dependencies": {"tools": []}, "permissions": "read", "timeout_seconds": 30,
        "category": body.category, "color": "#0d9488", "origin": "runtime_created",
        "sample_args": body.sample_args,
    }
    (pkg_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (pkg_dir / "skill.py").write_text(
        f'"""运行时创建的技能包：{body.description}"""\n\n'
        'from app.skills._ops import OPS\n\n\n'
        'def run(params: dict, ctx: dict | None = None) -> dict:\n'
        f'    return OPS["{body.impl}"](params)\n',
        encoding="utf-8")
    rt.skills.discover()
    return {"ok": True, "name": name, "path": str(pkg_dir.relative_to(_ROOT)),
            "impl": body.impl, "loaded": name in rt.skills.packages,
            "confirm_note": note}


@app.post("/api/capabilities/refresh")
def refresh_skills() -> dict:
    rt = get_rt()
    pkgs = rt.skills.discover()
    return {"ok": True, "skills": len(pkgs), "warnings": rt.skills.warnings}


# ==================== 手机 App 下载 ====================
@app.get("/app.apk", include_in_schema=False)
def download_apk() -> FileResponse:
    """浏览器地址栏输入即下载的 APK 直链。

    带 Content-Disposition: attachment，任何浏览器打开都会触发下载而不是内联展示；
    MIME 为 application/vnd.android.package-archive，安卓系统能识别为安装包。
    """
    apk = STATIC_DIR / "download" / "AgentPlatform.apk"
    if not apk.exists():
        raise HTTPException(status_code=404, detail={
            "error_code": "APK_NOT_FOUND",
            "message": "安装包尚未构建，请在项目根目录运行 bash mobile/build_apk.sh",
        })
    return FileResponse(
        str(apk),
        media_type="application/vnd.android.package-archive",
        filename="智能体Agent.apk",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/download/admin-guide.pdf", include_in_schema=False)
@app.get("/admin-guide.pdf", include_in_schema=False)
def download_admin_guide() -> FileResponse:
    """管理后台《制作逻辑与作用说明书》PDF 下载直链。

    带 Content-Disposition: attachment，浏览器打开即触发下载；
    同时注册短路径 /admin-guide.pdf 作为备用入口，避免发布通道对子路径的差异处理。
    """
    pdf = STATIC_DIR / "download" / "agent-platform-admin-guide.pdf"
    if not pdf.exists():
        raise HTTPException(status_code=404, detail={
            "error_code": "GUIDE_NOT_FOUND",
            "message": "说明书尚未生成，请联系管理员重新生成 PDF。",
        })
    return FileResponse(
        str(pdf),
        media_type="application/pdf",
        filename="安全运维管理后台_制作逻辑与作用说明书.pdf",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/download", include_in_schema=False)
def download_page() -> FileResponse:
    """手机 App 下载引导页（含二维码、安装步骤）。"""
    page = STATIC_DIR / "download" / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="下载页不存在")
    return FileResponse(str(page), media_type="text/html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    """第一阶段运维管理页（保留兼容，不删除已有能力）。"""
    return FileResponse(str(STATIC_DIR / "admin.html"), media_type="text/html")


# 第二阶段：Agent Platform 管理后台。独立 FastAPI 应用，独立鉴权、独立前端页面。
# 在线发布通道通常只暴露一个端口，因此同时提供挂载形态（/console）；
# Docker / 本机部署则跑独立进程（uvicorn admin.server:app --port 8001）。
# 用 try 包裹：控制台模块异常绝不能让智能体平台起不来。
try:
    from admin.server import app as _admin_app
    app.mount("/console", _admin_app, name="console")
    _CONSOLE_MOUNTED = True
except Exception as _console_exc:  # pragma: no cover
    _CONSOLE_MOUNTED = False
    print(f"[console] 挂载失败（平台不受影响）：{_console_exc}")


@app.get("/console-status", include_in_schema=False)
def console_status() -> dict:
    return {"mounted": _CONSOLE_MOUNTED, "entry": "/console/",
            "standalone_port": 8001, "version": "2.0.0"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")
