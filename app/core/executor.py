"""执行 Agent：只负责"把单个作业干完"，不做规划、不做验收、不做流转决策。

三条纪律：
1. 任何失败都抛出去（由管理者决定重试/换人/降级），这里绝不吞异常；
2. 重试只针对瞬时故障（TIMEOUT / TOOL_ERROR / LLM_ERROR / UNKNOWN），指数退避；
3. 中断信号（AgentInterrupt）立即向上抛，保证断点位置准确。
"""

from __future__ import annotations

from app.core.faults import injector
from app.core.util import retry_call, with_timeout
from app.errors import AgentPlatformError, ToolNotRegistered
from app.models import Job
from app.tools.registry import ToolContext


class ExecutorAgent:
    def __init__(self, agent_id: str, name: str, settings, repo, tracer, log, registry, skills):
        self.agent_id = agent_id
        self.name = name
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.registry = registry
        self.skills = skills
        self.tools: list[str] = []
        self.skill_names: list[str] = []

    def can(self, action: str, target: str) -> bool:
        return target in (self.tools if action == "tool" else self.skill_names)

    def run(self, job: Job, ctx: dict) -> dict:
        attempts_seen = {"n": 0}
        session_id = job.session_id

        with self.tracer.step(
            session_id, self.agent_id, f"execute:{job.action}:{job.target}",
            {"job_id": job.job_id, "action": job.action, "target": job.target, "params": job.params},
        ) as st:
            # 故障注入点：模拟进程在执行该作业前被中断（"人为中断"演示靠它）
            injector().check_job(job.job_id)

            retry_cfg = self.settings.p("retry", default={}) or {}
            retry_on = tuple(retry_cfg.get("retry_on") or ("TIMEOUT", "TOOL_ERROR", "LLM_ERROR", "UNKNOWN"))

            def on_retry(exc: BaseException, attempt: int, delay: float) -> None:
                self.log.warning(
                    "executor",
                    f"作业 {job.job_id} 第 {attempt} 次执行失败，{delay:.2f}s 后重试：{exc}",
                    session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                    error_code=getattr(exc, "error_code", "UNKNOWN"),
                    context={"job_id": job.job_id, "target": job.target},
                )

            def attempt(n: int) -> dict:
                attempts_seen["n"] = n
                self.log.info(
                    "executor",
                    f"执行作业 {job.job_id}（{job.action}:{job.target}）第 {n} 次",
                    session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                    context={"params_digest": list(job.params.keys())},
                )
                return self._dispatch(job, ctx)

            result = retry_call(
                attempt,
                attempts=self.settings.retry_max_attempts,
                base_delay=float(retry_cfg.get("base_delay_seconds", 0.3)),
                max_delay=float(retry_cfg.get("max_delay_seconds", 8.0)),
                jitter=float(retry_cfg.get("jitter", 0.15)),
                retry_on=retry_on,
                on_retry=on_retry,
            )
            st.set_output(result)
            st.meta = {"attempts": attempts_seen["n"], "job_id": job.job_id}
            return result

    def _dispatch(self, job: Job, ctx: dict) -> dict:
        if job.action == "tool":
            injector().check_tool(job.target)
            if self.tools and job.target not in self.tools:
                raise ToolNotRegistered(
                    f"执行 Agent {self.agent_id} 未被授予工具 {job.target} 的调用权"
                )
            tool_ctx = ToolContext(
                session_id=job.session_id, agent_id=self.agent_id,
                task_id=job.task_id, job_id=job.job_id,
            )
            return self.registry.call(job.target, job.params, tool_ctx)

        if job.action == "skill":
            if self.skill_names and job.target not in self.skill_names:
                raise AgentPlatformError(
                    f"执行 Agent {self.agent_id} 未被授予技能 {job.target}", error_code="SCHEMA_INVALID"
                )
            name = job.target
            params = dict(job.params)
            timeout = 60.0
            try:
                pkg = self.skills.packages.get(name)
                if pkg is not None:
                    timeout = pkg.timeout_seconds
                result = with_timeout(lambda: self.skills.run(name, params, ctx), timeout, label=f"skill:{name}")
            except TimeoutError as exc:
                from app.errors import ToolTimeout
                raise ToolTimeout(f"技能 {name} 执行超时 {timeout}s") from exc
            return result

        raise AgentPlatformError(f"未知作业动作: {job.action}", error_code="SCHEMA_INVALID")
