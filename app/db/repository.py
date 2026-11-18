"""数据访问层：所有 SQL 只在这里出现，上层不写 SQL。

约定：
- 快照 / 日志 / 流转 / 审计 都是 append-only，便于事后复盘。
- tasks / jobs 是可更新的状态机，断点恢复依赖它们的确定性主键。
"""

from __future__ import annotations

import json
from typing import Any

from app.db.database import Database
from app.models import Job, SubTask, TaskPlan, dumps, loads, now_iso


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ==================== 会话 ====================
    def create_session(self, session_id: str, mode: str, intent: str | None = None,
                       channel: str = "chat", owner: str = "default") -> None:
        """创建会话并绑定沙箱通道与归属。channel / owner 一旦写入即不可变。

        owner 是第二阶段新增的用户/租户级隔离维度；缺省 'default' 保证所有
        第一阶段的调用（未传 owner）行为完全不变。
        """
        ts = now_iso()
        self.db.execute(
            "INSERT INTO sessions"
            " (session_id, mode, channel, owner, status, step_index, intent, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?) ON CONFLICT(session_id) DO NOTHING",
            (session_id, mode, channel, owner or "default", "running", intent, ts, ts),
        )
        try:
            from app.obs import console as obs
            obs.emit_event(self.db, kind="session.created", session_id=session_id,
                           channel=channel, actor="system", status="running",
                           summary=f"新建会话 · 通道 {channel} · 归属 {owner or 'default'}",
                           payload={"channel": channel, "owner": owner or "default"})
        except Exception:
            pass

    def session_owner(self, session_id: str) -> str | None:
        row = self.db.query_one(
            "SELECT owner FROM sessions WHERE session_id = ?", (session_id,))
        return (row or {}).get("owner")

    def get_session(self, session_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))

    def session_channel(self, session_id: str) -> str | None:
        row = self.db.query_one(
            "SELECT channel FROM sessions WHERE session_id = ?", (session_id,))
        return (row or {}).get("channel")

    def list_sessions(self, limit: int = 30, include_probes: bool = False,
                      channel: str | None = None) -> list[dict]:
        """最近活跃的会话，附带步数与摘要。channel 传入时只返回该沙箱的会话，
        实现「两套通道互不串历史」；不传则返回全部（运维管理页用）。
        """
        clauses: list[str] = []
        params: list[object] = []
        if not include_probes:
            clauses.append("s.session_id NOT LIKE 'capability_probe_%'")
            clauses.append("s.session_id NOT LIKE 'web_probe_%'")
        if channel in ("chat", "work"):
            clauses.append("s.channel = ?")
            params.append(channel)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        return self.db.query(
            "SELECT s.session_id, s.mode, s.channel, s.status, s.step_index, s.intent,"
            " s.created_at, s.updated_at,"
            " (SELECT COUNT(*) FROM snapshots p WHERE p.session_id = s.session_id) AS snapshot_count"
            f" FROM sessions s{where} ORDER BY s.updated_at DESC LIMIT ?",
            tuple(params),
        )

    def add_chat_turn(self, session_id: str, role: str, content: str,
                      mode: str | None = None) -> None:
        """统一写入一轮对话。

        chat / task / office / skill 四种模式都调这里，保证「办事轮次之后追问」
        也能拿到上下文；当前轮在模型应答前不写，避免重复。
        """
        content = str(content or "").strip()
        if not content:
            return
        row = self.db.query_one(
            "SELECT COALESCE(MAX(turn_index), 0) AS n FROM chat_turns WHERE session_id = ?",
            (session_id,),
        )
        idx = int((row or {}).get("n") or 0) + 1
        self.db.execute(
            "INSERT INTO chat_turns(session_id, turn_index, role, content, mode, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, idx, role, content[:8000], mode, now_iso()),
        )

    def list_chat_turns(self, session_id: str, limit: int = 24) -> list[dict]:
        """统一多轮上下文：优先 chat_turns 表，空则回退旧版快照（兼容历史会话）。

        输出标准 [{"role":"user"...},{"role":"assistant"...}]，供模型上下文、
        意图理解与页面回放共用。窗口放宽到 24 条（约 12 轮）。
        """
        rows = self.db.query(
            "SELECT role, content FROM chat_turns WHERE session_id = ?"
            " ORDER BY turn_index DESC LIMIT ?",
            (session_id, int(limit)),
        )
        turns = [
            {"role": r["role"], "content": str(r["content"])}
            for r in reversed(rows)
            if r.get("role") in ("user", "assistant") and r.get("content")
        ]
        if turns:
            return turns

        # 兼容旧数据：从落盘快照还原（老版本只有 chat.respond 步骤带对话）
        snaps = self.db.query(
            "SELECT input, output FROM snapshots WHERE session_id = ?"
            " AND phase = 'chat.respond' ORDER BY step_index DESC LIMIT ?",
            (session_id, int(limit)),
        )
        for row in reversed(snaps):
            inp = loads(row.get("input"), {}) or {}
            out = loads(row.get("output"), {}) or {}
            question = inp.get("text") if isinstance(inp, dict) else None
            answer = out.get("answer") if isinstance(out, dict) else None
            if question:
                turns.append({"role": "user", "content": str(question)})
            if answer:
                turns.append({"role": "assistant", "content": str(answer)})
        return turns

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE sessions SET {cols} WHERE session_id = ?",
            (*fields.values(), session_id),
        )

    def delete_session(self, session_id: str) -> int:
        """级联删除一个会话的全部数据：先子表后父表（避免外键/快照冲突）。

        返回被删除的行数，便于接口如实汇报。
        """
        tables = ["snapshots_archive", "snapshots", "logs", "handoffs",
                  "chat_turns", "tool_audit", "jobs", "tasks", "plans", "sessions"]
        total = 0
        for t in tables:
            try:
                total += self.db.execute(
                    f"DELETE FROM {t} WHERE session_id = ?", (session_id,))
            except Exception:
                # 表不存在（如未来迁移）时跳过，不影响主流程
                continue
        return total

    def bump_step(self, session_id: str) -> int:
        """分配该 session 的下一个全局步号（原子，断点定位的唯一依据）。

        会话不存在时先补建一行：否则 UPDATE 影响 0 行、步号永远停在 0，
        同一 session 的第二次快照会撞 (session_id, step_index) 复合主键。
        """
        self.create_session(session_id, "pending")
        ts = now_iso()
        self.db.execute(
            "UPDATE sessions SET step_index = step_index + 1, updated_at = ? WHERE session_id = ?",
            (ts, session_id),
        )
        row = self.db.query_one("SELECT step_index FROM sessions WHERE session_id = ?", (session_id,))
        return int(row["step_index"]) if row else 0

    def current_step(self, session_id: str) -> int:
        row = self.db.query_one("SELECT step_index FROM sessions WHERE session_id = ?", (session_id,))
        return int(row["step_index"]) if row else 0

    # ==================== 计划 / 任务 ====================
    def save_plan(self, plan: TaskPlan) -> None:
        self.db.execute(
            "INSERT INTO plans (plan_id, session_id, intent, payload, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(plan_id) DO UPDATE SET payload = excluded.payload",
            (plan.plan_id, plan.session_id, plan.intent, dumps(plan.to_dict()), plan.created_at),
        )

    def get_plan(self, session_id: str) -> TaskPlan | None:
        row = self.db.query_one(
            "SELECT payload FROM plans WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        return TaskPlan.from_dict(loads(row["payload"])) if row else None

    def upsert_task(self, subtask: SubTask, session_id: str, plan_id: str, status: str = "pending") -> None:
        self.db.execute(
            "INSERT INTO tasks (session_id, task_id, plan_id, title, assignee, status, depends_on,"
            " acceptance, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(session_id, task_id) DO UPDATE SET status = excluded.status,"
            " payload = excluded.payload, updated_at = excluded.updated_at",
            (
                session_id, subtask.task_id, plan_id, subtask.title, subtask.assignee, status,
                dumps(subtask.depends_on), dumps(subtask.acceptance), dumps(subtask.to_dict()), now_iso(),
            ),
        )

    def set_task_status(
        self, session_id: str, task_id: str, status: str, result: dict | None = None,
        error: dict | None = None, bump_attempts: bool = False,
    ) -> None:
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now_iso()]
        if result is not None:
            sets.append("result = ?")
            params.append(dumps(result))
        if error is not None:
            sets.append("error = ?")
            params.append(dumps(error))
        if bump_attempts:
            sets.append("attempts = attempts + 1")
        params.extend([session_id, task_id])
        self.db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE session_id = ? AND task_id = ?", tuple(params)
        )

    def get_task(self, session_id: str, task_id: str) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM tasks WHERE session_id = ? AND task_id = ?", (session_id, task_id)
        )

    def list_tasks(self, session_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM tasks WHERE session_id = ? ORDER BY task_id", (session_id,)
        )

    # ==================== 作业 ====================
    def upsert_job(self, job: Job) -> None:
        self.db.execute(
            "INSERT INTO jobs (session_id, job_id, task_id, agent_id, action, target, status,"
            " attempts, params, output, error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(session_id, job_id) DO UPDATE SET status = excluded.status,"
            " attempts = excluded.attempts, output = excluded.output, error = excluded.error,"
            " updated_at = excluded.updated_at",
            (
                job.session_id, job.job_id, job.task_id, job.agent_id, job.action, job.target,
                job.status, job.attempts, dumps(job.params),
                dumps(job.output) if job.output is not None else None,
                dumps(job.error) if job.error is not None else None,
                now_iso(),
            ),
        )

    def get_job(self, session_id: str, job_id: str) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM jobs WHERE session_id = ? AND job_id = ?", (session_id, job_id)
        )

    def list_jobs(self, session_id: str, task_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM jobs WHERE session_id = ? AND task_id = ? ORDER BY job_id", (session_id, task_id)
        )

    def reset_job(self, session_id: str, job_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET status = 'pending', output = NULL, error = NULL, updated_at = ?"
            " WHERE session_id = ? AND job_id = ?",
            (now_iso(), session_id, job_id),
        )

    def reset_jobs(self, session_id: str, task_id: str) -> None:
        """打回重做时把本任务的作业置回 pending，强制重跑。"""
        self.db.execute(
            "UPDATE jobs SET status = 'pending', output = NULL, error = NULL, updated_at = ?"
            " WHERE session_id = ? AND task_id = ?",
            (now_iso(), session_id, task_id),
        )

    # ==================== 快照 ====================
    def insert_snapshot(
        self, session_id: str, step_index: int, agent_id: str, phase: str, status: str,
        input_: Any = None, output: Any = None, error: Any = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO snapshots (session_id, step_index, agent_id, phase, status, input, output, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, step_index, agent_id, phase, status,
                dumps(input_) if input_ is not None else None,
                dumps(output) if output is not None else None,
                dumps(error) if error is not None else None,
                now_iso(),
            ),
        )

    def list_snapshots(self, session_id: str, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM snapshots WHERE session_id = ? ORDER BY step_index"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.db.query(sql, (session_id,))

    def count_snapshots(self, session_id: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM snapshots WHERE session_id = ?", (session_id,))
        return int(row["c"]) if row else 0

    def prune_snapshots(self, session_id: str, keep_last: int) -> int:
        """滚动归档：超出 keep_last 的最老快照搬到 archive 表。"""
        total = self.count_snapshots(session_id)
        overflow = total - keep_last
        if overflow <= 0:
            return 0
        victims = self.db.query(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY step_index LIMIT ?",
            (session_id, overflow),
        )
        ts = now_iso()
        for v in victims:
            self.db.execute(
                "INSERT INTO snapshots_archive (session_id, step_index, agent_id, phase, status, input,"
                " output, error, created_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (v["session_id"], v["step_index"], v["agent_id"], v["phase"], v["status"],
                 v["input"], v["output"], v["error"], v["created_at"], ts),
            )
            self.db.execute("DELETE FROM snapshots WHERE id = ?", (v["id"],))
        return len(victims)

    def list_archive(self, session_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM snapshots_archive WHERE session_id = ? ORDER BY step_index", (session_id,)
        )

    # ==================== 日志 ====================
    def insert_log(
        self, session_id: str, agent_id: str, step_index: int, level: str, module: str,
        message: str, error_code: str | None = None, stack: str | None = None,
        context_digest: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO logs (session_id, agent_id, step_index, level, module, message, error_code,"
            " stack, context_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, agent_id, step_index, level, module, message, error_code, stack,
             context_digest, now_iso()),
        )

    def list_logs(self, session_id: str, since_step: int | None = None, level: str | None = None) -> list[dict]:
        sql = "SELECT * FROM logs WHERE session_id = ?"
        params: list[Any] = [session_id]
        if since_step is not None:
            sql += " AND step_index >= ?"
            params.append(since_step)
        if level:
            sql += " AND level = ?"
            params.append(level)
        sql += " ORDER BY id"
        return self.db.query(sql, tuple(params))

    # ==================== 流转 ====================
    def insert_handoff(
        self, session_id: str, step_index: int, task_id: str | None, from_agent: str,
        to_agent: str, reason: str, verdict: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO handoffs (session_id, step_index, task_id, from_agent, to_agent, reason,"
            " verdict, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, step_index, task_id, from_agent, to_agent, reason, verdict, now_iso()),
        )

    def list_handoffs(self, session_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM handoffs WHERE session_id = ? ORDER BY id", (session_id,))

    # ==================== 工具审计 ====================
    def insert_audit(
        self, session_id: str, agent_id: str, step_index: int, tool: str, permission: str,
        args_digest: str, status: str, error_code: str | None, duration_ms: int,
    ) -> None:
        self.db.execute(
            "INSERT INTO tool_audit (session_id, agent_id, step_index, tool, permission, args_digest,"
            " status, error_code, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, agent_id, step_index, tool, permission, args_digest, status, error_code,
             duration_ms, now_iso()),
        )

    def list_audit(self, session_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM tool_audit WHERE session_id = ? ORDER BY id", (session_id,))

    # ==================== 知识库 ====================
    def upsert_document(self, doc_id: str, source: str, title: str, chunks: int) -> None:
        self.db.execute(
            "INSERT INTO documents (doc_id, source, title, chunks, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(doc_id) DO UPDATE SET title = excluded.title, chunks = excluded.chunks",
            (doc_id, source, title, chunks, now_iso()),
        )

    def replace_chunks(self, doc_id: str, rows: list[tuple[int, str, str, str]]) -> None:
        """rows: (chunk_index, content, embedding_json, source)"""
        self.db.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (doc_id,))
        ts = now_iso()
        self.db.executemany(
            "INSERT INTO knowledge_chunks (doc_id, chunk_index, content, embedding, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(doc_id, idx, content, emb, source, ts) for idx, content, emb, source in rows],
        )

    def list_chunks(self) -> list[dict]:
        return self.db.query("SELECT * FROM knowledge_chunks ORDER BY id")

    def count_chunks(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM knowledge_chunks")
        return int(row["c"]) if row else 0

    # ==================== 回放 ====================
    def replay_chain(self, session_id: str) -> list[dict]:
        """按 step_index 回放完整执行链（含输入/输出摘要与流转）。"""
        snaps = self.list_snapshots(session_id)
        handoffs = {h["step_index"]: h for h in self.list_handoffs(session_id)}
        chain = []
        for s in snaps:
            chain.append({
                "step_index": s["step_index"],
                "agent_id": s["agent_id"],
                "phase": s["phase"],
                "status": s["status"],
                "created_at": s["created_at"],
                "input": json.loads(s["input"]) if s["input"] else None,
                "output": json.loads(s["output"]) if s["output"] else None,
                "error": json.loads(s["error"]) if s["error"] else None,
                "handoff": handoffs.get(s["step_index"]),
            })
        return chain
