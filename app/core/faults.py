"""故障注入：让"中断恢复 / 重试退避 / 打回重做"可被真实演示，而不是靠嘴说。

全部通过环境变量控制，生产默认全关（不影响正常行为）：
  AP_INTERRUPT_ON_JOB=T4/j0     执行到该作业前抛出 AgentInterrupt（模拟断电/杀进程）
  AP_FAULT_TOOL="calc.word_count:1"   指定工具前 N 次调用抛 InjectedFault
  AP_FAULT_SKILL="compose_report:1"   指定技能前 N 次执行抛 InjectedFault
  AP_FAULT_VALIDATION="T3:1"          指定任务前 N 次验收强制不通过（演示打回重做）
"""

from __future__ import annotations

import os
import threading

from app.errors import AgentInterrupt, InjectedFault


def _parse_spec(spec: str) -> tuple[str, int]:
    if not spec:
        return "", 0
    name, _, times = spec.partition(":")
    try:
        n = int(times) if times else 1
    except ValueError:
        n = 1
    return name.strip(), n


class FaultInjector:
    def __init__(self) -> None:
        self.interrupt_job = os.environ.get("AP_INTERRUPT_ON_JOB", "").strip()
        self.fault_tool, self.fault_tool_times = _parse_spec(os.environ.get("AP_FAULT_TOOL", ""))
        self.fault_skill, self.fault_skill_times = _parse_spec(os.environ.get("AP_FAULT_SKILL", ""))
        self.fault_validation, self.fault_validation_times = _parse_spec(
            os.environ.get("AP_FAULT_VALIDATION", "")
        )
        self._counter: dict[str, int] = {}
        self._lock = threading.Lock()

    def _hit(self, key: str, limit: int) -> bool:
        if limit <= 0:
            return False
        with self._lock:
            used = self._counter.get(key, 0)
            if used >= limit:
                return False
            self._counter[key] = used + 1
            return True

    @property
    def enabled(self) -> bool:
        return bool(
            self.interrupt_job or self.fault_tool_times or self.fault_skill_times
            or self.fault_validation_times
        )

    def check_job(self, job_id: str) -> None:
        if self.interrupt_job and job_id == self.interrupt_job:
            raise AgentInterrupt(f"故障注入：执行 {job_id} 前模拟进程中断")

    def check_tool(self, tool: str) -> None:
        if self.fault_tool and tool == self.fault_tool and self._hit(f"tool:{tool}", self.fault_tool_times):
            raise InjectedFault(f"故障注入：工具 {tool} 首次调用失败（模拟瞬时故障）")

    def check_skill(self, skill: str) -> None:
        if self.fault_skill and skill == self.fault_skill and self._hit(
            f"skill:{skill}", self.fault_skill_times
        ):
            raise InjectedFault(f"故障注入：技能 {skill} 首次执行失败（模拟瞬时故障）")

    def check_validation(self, task_id: str) -> bool:
        return bool(
            self.fault_validation and task_id == self.fault_validation
            and self._hit(f"val:{task_id}", self.fault_validation_times)
        )


_injector: FaultInjector | None = None


def injector() -> FaultInjector:
    global _injector
    if _injector is None:
        _injector = FaultInjector()
    return _injector
