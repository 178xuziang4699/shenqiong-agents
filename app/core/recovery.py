"""中断恢复：先取证，再决定续跑方式，绝不无条件从头重跑。

固定动作序列（对应需求第 8 条）：
1. 读上下文快照定位停止位置（最后成功步 / 首个失败点）；
2. 检索该位置附近的错误日志，判断是否存在"未处理异常"；
3. 无异常 → 从断点继续；有异常 → 按错误归类执行修复动作后再续跑；
4. 续跑时已完成任务直接复用结果，已完成作业直接复用产出。
"""

from __future__ import annotations

from app.core.expr import resolve_path  # noqa: F401  (保留给扩展：按字段做断点校验)
from app.errors import AgentPlatformError
from app.models import loads
from app.obs.diagnose import diagnose, format_diagnosis

_NO_AUTO_RESUME = {"escalate"}


class RecoveryService:
    def __init__(self, settings, repo, tracer, log, engine, handoff, confirmer):
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.engine = engine
        self.handoff = handoff
        self.confirmer = confirmer

    def resume(self, session_id: str, force: bool = False) -> dict:
        session = self.repo.get_session(session_id)
        if not session:
            return {"ok": False, "error": "会话不存在", "session_id": session_id}

        diag = diagnose(self.repo, session_id, self.settings)

        with self.tracer.step(session_id, "supervisor", "recover.inspect",
                              {"diagnosis": diag}) as st:
            evidence = {
                "last_success_step": (diag.get("last_success") or {}).get("step_index"),
                "first_failure_step": (diag.get("first_failure") or {}).get("step_index"),
                "error_class": diag.get("error_class"),
                "unhandled_error": diag.get("unhandled_error"),
                "suggested_action": diag.get("suggested_action"),
            }
            self.log.info(
                "recovery", f"恢复前取证：{evidence}", session_id=session_id, agent_id="supervisor",
                step_index=st.step_index, context=evidence,
            )
            st.set_output(evidence)

        if session["status"] == "completed" and not force:
            return {"ok": True, "resumed": False, "reason": "会话已完成，无需恢复", "diagnosis": diag}

        plan = self.repo.get_plan(session_id)
        if plan is None:
            return {"ok": False, "error": "找不到该会话的任务计划，无法恢复（可能是对话模式会话）",
                    "diagnosis": diag}

        tasks = self.repo.list_tasks(session_id)
        pre_done: dict[str, dict] = {}
        reset: list[str] = []
        for task in tasks:
            if task["status"] == "done":
                pre_done[task["task_id"]] = loads(task["result"], {}) or {}
            elif task["status"] in ("running", "failed", "rejected", "blocked"):
                reset.append(task["task_id"])

        action = diag.get("suggested_action") or "resume_from_breakpoint"
        if action in _NO_AUTO_RESUME:
            self.log.warning(
                "recovery", f"错误归类 {diag.get('error_class')} 对应动作 {action}，需人工介入，不做自动续跑",
                session_id=session_id, agent_id="supervisor",
                step_index=self.repo.current_step(session_id), error_code=diag.get("error_class"),
            )
            return {"ok": False, "resumed": False, "need_human": True, "action": action,
                    "diagnosis": diag, "report": format_diagnosis(diag)}

        allow_degrade = action == "retry_then_degrade"
        with self.tracer.step(
            session_id, "supervisor", "recover.plan",
            {"action": action, "reset_tasks": reset, "reuse_tasks": sorted(pre_done)},
        ) as st:
            for task_id in reset:
                self.repo.set_task_status(session_id, task_id, "pending")
            self.repo.update_session(session_id, status="running")
            self.handoff.record(
                session_id, "supervisor", "supervisor",
                f"断点恢复：错误归类 {diag.get('error_class')} → 修复动作 {action}；"
                f"复用已完成任务 {len(pre_done)} 个，重置 {len(reset)} 个",
                verdict="resume",
            )
            st.set_output({"action": action, "allow_degrade": allow_degrade})

        outcome = self.engine.run_plan(
            session_id, plan, pre_done=pre_done, resume=True, allow_degrade=allow_degrade
        )
        final_status = "completed" if outcome["status"] == "completed" else "failed"
        self.repo.update_session(session_id, status=final_status)

        self.log.info(
            "recovery", f"恢复结束：{outcome['status']}（复用 {len(pre_done)} 个已完成任务）",
            session_id=session_id, agent_id="supervisor",
            step_index=self.repo.current_step(session_id),
            context={"failed": outcome.get("failed"), "skipped": outcome.get("skipped")},
        )
        return {
            "ok": outcome["status"] != "failed",
            "resumed": True,
            "action": action,
            "reused_tasks": sorted(pre_done),
            "reset_tasks": reset,
            "outcome": outcome,
            "diagnosis": diag,
            "report": format_diagnosis(diag),
        }
