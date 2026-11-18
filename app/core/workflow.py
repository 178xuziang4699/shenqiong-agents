"""工作流引擎：串行 / 并行 / 条件分支 / 人工确认节点 / 超时边界。

调度模型（尽量少一层抽象）：
- 依赖满足即可执行（depends_on → 串行语义）；
- 同一 parallel_group 的就绪任务并行执行，并发上限来自 policy；
- condition 不成立 → 本任务 skipped，其下游一并 skipped（不假装成功）；
- 任一任务失败 → 标记失败、下游 blocked，其余无依赖任务继续跑。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.expr import eval_bool
from app.core.util import with_timeout
from app.errors import AgentInterrupt, AgentPlatformError, EscalationRequired, HumanRejected
from app.models import SubTask, TaskPlan, dumps, loads

SKIP_MARK = {"__skipped__": True}


class WorkflowEngine:
    def __init__(self, settings, repo, tracer, log, managers: dict, handoff, confirmer):
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.managers = managers
        self.handoff = handoff
        self.confirmer = confirmer

    # ==================== 对外入口 ====================
    def run_plan(
        self, session_id: str, plan: TaskPlan, *, pre_done: dict | None = None,
        resume: bool = False, allow_degrade: bool = False,
    ) -> dict:
        results: dict[str, dict] = dict(pre_done or {})
        remaining = [t for t in plan.subtasks if t.task_id not in results]
        failed: list[dict] = []
        skipped: list[dict] = []
        blocked: set[str] = set()

        if resume:
            self.log.info(
                "workflow", f"断点续跑：复用 {len(results)} 个已完成任务，待执行 {len(remaining)} 个",
                session_id=session_id, agent_id="workflow",
                step_index=self.repo.current_step(session_id),
                context={"done": sorted(results), "todo": [t.task_id for t in remaining]},
            )

        while remaining:
            for task in list(remaining):
                if any(dep in blocked for dep in task.depends_on):
                    blocked.add(task.task_id)
                    skipped.append({"task_id": task.task_id, "reason": "上游任务失败/被拒/跳过"})
                    remaining.remove(task)
                    self.repo.set_task_status(session_id, task.task_id, "skipped")
                    results[task.task_id] = SKIP_MARK

            batch = self._next_batch(remaining, results)
            if not batch:
                if remaining:
                    reason = f"依赖无法满足（可能存在环或上游被跳过）：{[t.task_id for t in remaining]}"
                    self.log.error("workflow", reason, session_id=session_id, agent_id="workflow",
                                   step_index=self.repo.current_step(session_id), error_code="CONFIG_INVALID")
                    for task in remaining:
                        self.repo.set_task_status(session_id, task.task_id, "blocked")
                        blocked.add(task.task_id)
                        skipped.append({"task_id": task.task_id, "reason": reason})
                    remaining.clear()
                break

            for task in batch:
                remaining.remove(task)

            batch_results = self._run_batch(session_id, batch, results, resume, allow_degrade)
            for task_id, payload in batch_results.items():
                if payload is None:
                    blocked.add(task_id)
                    skipped.append({"task_id": task_id, "reason": "任务未产出结果"})
                    results[task_id] = SKIP_MARK
                elif payload.get("__skipped__"):
                    skipped.append({"task_id": task_id, "reason": payload.get("reason", "条件不成立")})
                    results[task_id] = payload
                elif payload.get("__failed__"):
                    failed.append({"task_id": task_id, "error": payload.get("error")})
                    blocked.add(task_id)
                    results[task_id] = SKIP_MARK
                else:
                    results[task_id] = payload

        degraded = [
            tid for tid, payload in results.items()
            if isinstance(payload, dict) and payload.get("__degraded__")
        ]
        status = "completed" if not failed else ("partial" if results else "failed")
        if degraded and status == "completed":
            status = "partial"   # 降级跳过的环节不冒充「全部完成」
        return {
            "status": status,
            "results": {k: v for k, v in results.items() if not (isinstance(v, dict) and v.get("__skipped__"))},
            "skipped": skipped,
            "failed": failed,
            "degraded": degraded,
        }

    # ==================== 批次调度 ====================
    def _next_batch(self, remaining: list[SubTask], results: dict) -> list[SubTask]:
        ready = [t for t in remaining if all(dep in results for dep in t.depends_on)]
        if not ready:
            return []
        first = ready[0]
        if not first.parallel_group:
            return [first]
        batch = [first]
        for task in ready[1:]:
            if task.parallel_group == first.parallel_group:
                batch.append(task)
            else:
                break
        return batch

    def _run_batch(self, session_id: str, batch: list[SubTask], results: dict,
                   resume: bool, allow_degrade: bool) -> dict:
        snapshot = dict(results)
        if len(batch) == 1:
            return {batch[0].task_id: self._run_one(session_id, batch[0], snapshot, resume, allow_degrade)}

        max_workers = min(len(batch), self.settings.max_workers)
        self.log.info(
            "workflow", f"并行执行 {len(batch)} 个任务（并发上限 {max_workers}）：{[t.task_id for t in batch]}",
            session_id=session_id, agent_id="workflow",
            step_index=self.repo.current_step(session_id),
            context={"group": batch[0].parallel_group},
        )
        out: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wf") as pool:
            futures = {
                pool.submit(self._run_one, session_id, t, snapshot, resume, allow_degrade): t
                for t in batch
            }
            for future, task in futures.items():
                try:
                    out[task.task_id] = future.result()
                except AgentInterrupt:
                    raise
                except BaseException as exc:  # noqa: BLE001 - 并行分支失败必须收口
                    self.log.error(
                        "workflow", f"并行任务 {task.task_id} 异常：{exc}",
                        session_id=session_id, agent_id="workflow",
                        step_index=self.repo.current_step(session_id),
                        error_code=getattr(exc, "error_code", "UNKNOWN"), exc=exc,
                    )
                    out[task.task_id] = {"__failed__": True, "error": str(exc)[:300]}
        return out

    # ==================== 单任务执行 ====================
    def _run_one(self, session_id: str, task: SubTask, results: dict,
                 resume: bool, allow_degrade: bool) -> dict:
        # 1) 条件分支
        if task.condition:
            ok, err = eval_bool(task.condition, results, default=False)
            if err or not ok:
                reason = f"条件不成立：{task.condition}" + (f"（求值异常：{err}）" if err else "")
                self.log.warning("workflow", f"{task.task_id} {reason}，跳过",
                                 session_id=session_id, agent_id="workflow",
                                 step_index=self.repo.current_step(session_id), error_code="CONDITION_FALSE")
                self.repo.set_task_status(session_id, task.task_id, "skipped")
                return {"__skipped__": True, "reason": reason}
            self.log.info("workflow", f"{task.task_id} 条件成立：{task.condition}",
                          session_id=session_id, agent_id="workflow",
                          step_index=self.repo.current_step(session_id))

        # 2) 人工确认节点
        if task.requires_human:
            with self.tracer.step(
                session_id, "workflow", f"human_gate:{task.task_id}",
                {"task_id": task.task_id, "title": task.title, "write_op": task.write_op},
            ) as st:
                approved, note = self.confirmer.confirm(
                    f"进入子任务 {task.task_id}「{task.title}」（{'含关键写操作' if task.write_op else '流程节点'}）",
                    {"objective": task.objective, "acceptance": task.acceptance},
                )
                st.set_output({"approved": approved, "note": note})
            if not approved:
                self.handoff.record(
                    session_id, "workflow", "supervisor",
                    f"子任务 {task.task_id} 人工确认被拒（{note}）→ 上报总控",
                    task_id=task.task_id, verdict="reject",
                )
                self.repo.set_task_status(session_id, task.task_id, "rejected",
                                          error={"error_code": "HUMAN_REJECTED", "message": note})
                return {"__failed__": True, "error": f"HUMAN_REJECTED: {note}"}

        manager = self.managers.get(task.assignee)
        if manager is None:
            self.repo.set_task_status(session_id, task.task_id, "failed",
                                      error={"error_code": "CONFIG_INVALID", "message": "管理者不存在"})
            return {"__failed__": True, "error": f"管理者不存在: {task.assignee}"}

        # 3) 分派 + 执行（含子任务超时）
        self.handoff.record(
            session_id, "supervisor", manager.agent_id,
            f"按计划分派子任务 {task.task_id}「{task.title}」（依赖 {task.depends_on or '无'}）",
            task_id=task.task_id, verdict="dispatch",
        )
        self.repo.set_task_status(session_id, task.task_id, "running", bump_attempts=True)
        ctx = {"session_id": session_id, "results": results, "allow_degrade": allow_degrade}

        if resume:
            # 断点续跑：让管理者优先复用已落盘的 done 作业
            ctx["resume"] = True

        try:
            outcome = with_timeout(
                lambda: manager.handle_task(task, ctx, reuse_done_jobs=resume),
                self.settings.subtask_timeout, label=f"task:{task.task_id}",
            )
        except AgentInterrupt:
            # 中断不是失败：保留快照与作业状态，交给恢复流程从断点续跑
            raise
        except (EscalationRequired, HumanRejected) as exc:
            self.repo.set_task_status(session_id, task.task_id, "failed", error=exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)})
            return {"__failed__": True, "error": str(exc)[:300]}
        except TimeoutError:
            err = {"error_code": "TIMEOUT", "message": f"子任务超时 {self.settings.subtask_timeout}s"}
            self.repo.set_task_status(session_id, task.task_id, "failed", error=err)
            self.handoff.record(session_id, manager.agent_id, "supervisor",
                                f"子任务 {task.task_id} 超时 → 上报总控", task_id=task.task_id, verdict="escalate")
            return {"__failed__": True, "error": err["message"]}
        except AgentPlatformError as exc:
            self.repo.set_task_status(session_id, task.task_id, "failed", error=exc.to_dict())
            return {"__failed__": True, "error": str(exc)[:300]}

        output = outcome.get("output", {})
        if outcome.get("degraded"):
            output = dict(output) if isinstance(output, dict) else {"value": output}
            output["__degraded__"] = True
            output["degrade_reason"] = str(outcome.get("reason") or "")[:200]
            self.handoff.record(
                session_id, manager.agent_id, "supervisor",
                f"子任务 {task.task_id} 按恢复策略降级跳过，未产出有效结果",
                task_id=task.task_id, verdict="degrade",
            )
        self.repo.set_task_status(session_id, task.task_id, "done", result=output)
        return output
