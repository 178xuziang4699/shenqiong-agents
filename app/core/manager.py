"""管理者 Agent：承上（总控）启下（执行），负责拆解、二次派发、验收、打回、上报。

它掌握三件事，别的都不碰：
1. 把子任务拆成可执行的作业（jobs）；
2. 按能力挑执行 Agent 并二次派发，记录每一次流转；
3. 用机器可执行的 checks 验收，不通过就打回（限次），超限上报总控。
"""

from __future__ import annotations

from app.core.expr import eval_check
from app.core.faults import injector
from app.core.util import render
from app.errors import AgentInterrupt, AgentPlatformError, EscalationRequired
from app.models import Job, SubTask, dumps, loads
from app.errors import classify


class ManagerAgent:
    def __init__(self, agent_id: str, name: str, settings, repo, tracer, log, registry, skills,
                 handoff, executors: dict):
        self.agent_id = agent_id
        self.name = name
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.registry = registry
        self.skills = skills
        self.handoff = handoff
        self.executors = executors        # {agent_id: ExecutorAgent}
        self._override: dict[str, str] = {}

    # ==================== 拆解 ====================
    def decompose(self, task: SubTask) -> list[Job]:
        pipeline = task.params.get("pipeline")
        if not pipeline:
            pipeline = []
            if task.skill:
                pipeline.append({"action": "skill", "target": task.skill,
                                 "params": {k: v for k, v in task.params.items() if k != "pipeline"}})
            if task.tool:
                pipeline.append({"action": "tool", "target": task.tool,
                                 "params": {k: v for k, v in task.params.items() if k != "pipeline"}})
        if not pipeline:
            raise AgentPlatformError(
                f"子任务 {task.task_id} 没有可执行内容（既无 pipeline 也无 skill/tool）",
                error_code="SCHEMA_INVALID",
            )

        jobs: list[Job] = []
        for idx, step in enumerate(pipeline):
            action = step.get("action")
            target = step.get("target")
            if action not in ("tool", "skill") or not target:
                raise AgentPlatformError(f"子任务 {task.task_id} 的流水线第 {idx} 步不合法",
                                        error_code="SCHEMA_INVALID")
            executor = self._pick_executor(task.task_id, action, target)
            jobs.append(Job(
                job_id=f"{task.task_id}/j{idx}", task_id=task.task_id, session_id=task.params.get("_session_id", ""),
                agent_id=executor.agent_id, action=action, target=target,
                params=dict(step.get("params") or {}),
            ))
        return jobs

    def _pick_executor(self, task_id: str, action: str, target: str):
        override = self._override.get(task_id)
        if override and override in self.executors:
            cand = self.executors[override]
            if cand.can(action, target):
                return cand
        for executor in self.executors.values():
            if executor.can(action, target):
                return executor
        raise AgentPlatformError(
            f"无执行 Agent 具备能力 {action}:{target}（管理者 {self.agent_id} 的执行池："
            f"{[e.agent_id for e in self.executors.values()]}）",
            error_code="SCHEMA_INVALID",
        )

    def _swap_executor(self, task_id: str, action: str, target: str, exclude: str) -> str | None:
        for executor in self.executors.values():
            if executor.agent_id != exclude and executor.can(action, target):
                self._override[task_id] = executor.agent_id
                return executor.agent_id
        return None

    # ==================== 主流程 ====================
    def handle_task(self, task: SubTask, ctx: dict, reuse_done_jobs: bool = False) -> dict:
        session_id = ctx["session_id"]
        task.params.setdefault("_session_id", session_id)
        max_bounces = int(self.settings.p("recovery", "max_bounces", default=2))
        allow_degrade = bool(ctx.get("allow_degrade"))

        bounce = 0
        while True:
            try:
                jobs = self.decompose(task)
                outputs = self._run_jobs(task, jobs, ctx, reuse=reuse_done_jobs and bounce == 0)
            except AgentInterrupt:
                raise
            except EscalationRequired:
                raise
            except BaseException as exc:  # noqa: BLE001 - 作业失败必须归类处理，不静默
                code = classify(exc)
                self.log.error(
                    "manager", f"子任务 {task.task_id} 作业失败（{code}）：{exc}",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=self.repo.current_step(session_id), error_code=code, exc=exc,
                )
                if allow_degrade:
                    self.handoff.record(
                        session_id, self.agent_id, "supervisor",
                        f"作业失败（{code}）→ 按恢复策略降级处理，跳过失败环节（不伪报成功）",
                        task_id=task.task_id, verdict="degrade",
                    )
                    return {
                        "ok": True, "degraded": True, "reason": str(exc),
                        "output": {"ok": True, "degraded": True, "jobs": {},
                                   "note": "该环节因故障降级跳过，未产出有效结果"},
                    }
                bounce += 1
                if bounce > max_bounces:
                    self.handoff.record(
                        session_id, self.agent_id, "supervisor",
                        f"作业连续失败（{code}）且已打回 {max_bounces} 次 → 上报总控",
                        task_id=task.task_id, verdict="escalate",
                    )
                    raise EscalationRequired(f"子任务 {task.task_id} 失败：{exc}") from exc
                failed_job = self._first_unfinished_job(session_id, task.task_id)
                swapped = None
                if failed_job:
                    swapped = self._swap_executor(
                        task.task_id, failed_job["action"], failed_job["target"], failed_job["agent_id"]
                    )
                    self.repo.reset_job(session_id, failed_job["job_id"])
                if swapped:
                    self.handoff.record(
                        session_id, self.agent_id, swapped,
                        f"作业失败（{code}）→ 更换执行 Agent 重试第 {bounce} 次",
                        task_id=task.task_id, verdict="bounce",
                    )
                else:
                    self.repo.reset_jobs(session_id, task.task_id)
                    self.handoff.record(
                        session_id, self.agent_id, self.agent_id,
                        f"作业失败（{code}）→ 同池重试第 {bounce} 次",
                        task_id=task.task_id, verdict="bounce",
                    )
                self.repo.set_task_status(session_id, task.task_id, "running", bump_attempts=True)
                continue

            result = self._merge(task, jobs, outputs)
            ok, reasons = self.validate(task, result)
            if ok:
                self.handoff.record(
                    session_id, self.agent_id, "supervisor",
                    f"子任务 {task.task_id} 验收通过（{'；'.join(reasons)[:120]}），回传结果",
                    task_id=task.task_id, verdict="pass",
                )
                result["acceptance_reasons"] = reasons
                return {"ok": True, "output": result, "attempts": bounce + 1}

            bounce += 1
            self.log.warning(
                "manager", f"子任务 {task.task_id} 第 {bounce} 次验收不通过：{'；'.join(reasons)[:200]}",
                session_id=session_id, agent_id=self.agent_id,
                step_index=self.repo.current_step(session_id), error_code="VALIDATION_FAILED",
                context={"checks": task.checks},
            )
            if bounce > max_bounces:
                self.handoff.record(
                    session_id, self.agent_id, "supervisor",
                    f"打回 {max_bounces} 次仍不通过（{'；'.join(reasons)[:120]}）→ 上报总控",
                    task_id=task.task_id, verdict="escalate",
                )
                raise EscalationRequired(f"子任务 {task.task_id} 验收不通过：{'；'.join(reasons)}")
            target_agent = jobs[0].agent_id if jobs else self.agent_id
            self.handoff.record(
                session_id, self.agent_id, target_agent,
                f"验收不通过（{'；'.join(reasons)[:160]}）→ 打回重做第 {bounce} 次",
                task_id=task.task_id, verdict="bounce",
            )
            self.repo.reset_jobs(session_id, task.task_id)
            self.repo.set_task_status(session_id, task.task_id, "running", bump_attempts=True)

    # ==================== 作业执行 ====================
    def _run_jobs(self, task: SubTask, jobs: list[Job], ctx: dict, reuse: bool) -> dict:
        session_id = ctx["session_id"]
        outputs: dict[str, dict] = {}
        short: dict[str, dict] = {}   # j0/j1... → 输出，供流水线内 {{jobs.j0.content}} 引用
        # 降级路径下上游可能没有产出，模板变量缺失时填空串继续，不让下游二次崩溃
        strict = not bool(ctx.get("allow_degrade"))
        for job in jobs:
            existing = self.repo.get_job(session_id, job.job_id)
            if reuse and existing and existing["status"] == "done":
                outputs[job.job_id] = loads(existing["output"], {})
                short[job.job_id.rsplit("/", 1)[-1]] = outputs[job.job_id]
                self.log.info(
                    "manager",
                    f"断点续跑：复用已完成作业 {job.job_id}（不重复执行）",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=self.repo.current_step(session_id),
                    context={"job_id": job.job_id},
                )
                continue

            render_ctx = {
                **{k: v for k, v in ctx.get("results", {}).items()},
                "jobs": short,
                "input": ctx.get("input", {}),
                "task": {k: v for k, v in task.params.items() if not k.startswith("_")},
            }
            # 兼容模型规划时常用的 {{T1.result}} / {{T1.text}} 等别名：
            # 把上游任务的主要正文暴露成同义字段，避免字段名猜错导致整条链失败。
            for tid, res in list(render_ctx.items()):
                if tid in ("jobs", "input", "task") or not isinstance(res, dict):
                    continue
                main = next(
                    (res[k] for k in ("content", "report", "summary", "code",
                                      "text", "output_path", "path")
                     if k in res and isinstance(res[k], (str, int, float))),
                    None,
                )
                if main is not None:
                    aliased = dict(res)
                    for alias in ("result", "text", "data", "body", "output"):
                        aliased.setdefault(alias, main)
                    render_ctx[tid] = aliased
            job.params = render(job.params, render_ctx, strict)
            job.status = "running"
            self.repo.upsert_job(job)
            self.handoff.record(
                session_id, self.agent_id, job.agent_id,
                f"二次派发作业 {job.job_id}（{job.action}:{job.target}）",
                task_id=task.task_id, verdict="dispatch",
            )

            executor = self.executors[job.agent_id]
            try:
                result = executor.run(job, render_ctx)
            except BaseException as exc:  # noqa: BLE001
                job.status = "interrupted" if isinstance(exc, AgentInterrupt) else "failed"
                job.error = {"error_code": classify(exc), "message": str(exc)[:500]}
                self.repo.upsert_job(job)
                raise
            job.status = "done"
            job.output = result
            job.attempts = 1
            self.repo.upsert_job(job)
            outputs[job.job_id] = result
            short[job.job_id.rsplit("/", 1)[-1]] = result
            self.handoff.record(
                session_id, job.agent_id, self.agent_id,
                f"作业 {job.job_id} 执行完成，回传结果（{job.action}:{job.target}）",
                task_id=task.task_id, verdict="return",
            )
        return outputs

    def _first_unfinished_job(self, session_id: str, task_id: str) -> dict | None:
        for row in self.repo.list_jobs(session_id, task_id):
            if row["status"] != "done":
                return row
        return None

    # ==================== 验收 ====================
    def _merge(self, task: SubTask, jobs: list[Job], outputs: dict) -> dict:
        final = outputs.get(jobs[-1].job_id, {}) if jobs else {}
        merged = {**final, "jobs": outputs}
        merged.setdefault("ok", True)
        return merged

    def validate(self, task: SubTask, result: dict) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if injector().check_validation(task.task_id):
            return False, ["故障注入：本次验收强制不通过（用于演示打回重做）"]

        checks = task.checks or []
        if not checks:
            if result.get("ok") is False:
                return False, ["结果标记为 ok=false 且未定义验收项"]
            return True, ["未定义机器验收项，按 ok 标记通过"]

        ok_all = True
        for check in checks:
            ok, msg = eval_check({"output": result}, check)
            reasons.append(msg)
            ok_all = ok_all and ok
        return ok_all, reasons
