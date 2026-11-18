"""上下文快照追踪器：把"每一步"落成可回放的一行。

一步 = 一个原子动作（路由判定、规划、派发、执行、校验、确认、检索…）。
每次进入 step() 先分配全局单调 step_index，退出时无论成功/失败/中断都写快照。
禁止"只在成功时写"——否则断点恢复没有证据可用。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.errors import AgentInterrupt, classify
from app.models import dumps
from app.obs.logger import StructuredLogger, digest_context
from app.db.repository import Repository


@dataclass
class StepHandle:
    session_id: str
    step_index: int
    agent_id: str
    phase: str
    input: Any = None
    output: Any = None
    meta: dict = field(default_factory=dict)

    def set_output(self, output: Any) -> None:
        self.output = output


class Tracer:
    def __init__(self, repo: Repository, log: StructuredLogger, settings):
        self.repo = repo
        self.log = log
        self.settings = settings
        self._lock = threading.RLock()

    @contextmanager
    def step(
        self, session_id: str, agent_id: str, phase: str, payload: Any = None,
        snapshot_input: bool = True,
    ) -> Iterator[StepHandle]:
        with self._lock:
            step_index = self.repo.bump_step(session_id)
        handle = StepHandle(
            session_id=session_id, step_index=step_index, agent_id=agent_id, phase=phase,
            input=payload if snapshot_input else None,
        )
        try:
            yield handle
        except AgentInterrupt as exc:
            self._write(handle, "interrupted", {"error_code": exc.error_code, "message": exc.message})
            self.log.error(
                "tracer", f"步骤中断：{phase}", session_id=session_id, agent_id=agent_id,
                step_index=step_index, error_code=exc.error_code, context=handle.input,
            )
            self.repo.update_session(session_id, status="interrupted")
            raise
        except BaseException as exc:  # noqa: BLE001 - 必须记录所有异常，禁止静默
            code = classify(exc)
            self._write(handle, "failed", {"error_code": code, "message": str(exc)[:500]})
            self.log.error(
                "tracer", f"步骤失败：{phase}", session_id=session_id, agent_id=agent_id,
                step_index=step_index, error_code=code, context=handle.input, exc=exc,
            )
            raise
        else:
            self._write(handle, "ok", None)
        finally:
            self.repo.prune_snapshots(session_id, self.settings.snapshot_keep)

    def _write(self, handle: StepHandle, status: str, error: dict | None) -> None:
        output = handle.output
        if output is not None and handle.meta:
            output = {"result": output, "meta": handle.meta}
        self.repo.insert_snapshot(
            session_id=handle.session_id,
            step_index=handle.step_index,
            agent_id=handle.agent_id,
            phase=handle.phase,
            status=status,
            input_=handle.input,
            output=output,
            error=error,
        )
        self.log.info(
            "tracer",
            f"步骤记录 {handle.phase} → {status}",
            session_id=handle.session_id,
            agent_id=handle.agent_id,
            step_index=handle.step_index,
            error_code=(error or {}).get("error_code"),
            context=output if output is not None else handle.input,
        )
        # 每执行一步就向控制台事件流写一条（与快照同一事务链、共享同一库）。
        # 后台 SSE 以自增 id 轮询即可「即时」看到每一步，不依赖进程内广播。
        try:
            from app.obs import console as obs
            obs.emit_event(
                self.repo.db, kind="task.step", session_id=handle.session_id,
                actor=handle.agent_id, status=status,
                summary=f"step {handle.step_index} · {handle.phase} → {status}",
                payload={"step_index": handle.step_index, "phase": handle.phase,
                         "error": error},
            )
        except Exception:
            pass  # 观测增强失败绝不影响编排主链路
