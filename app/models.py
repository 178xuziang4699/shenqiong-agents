"""领域模型：只描述数据结构与序列化，不含业务逻辑。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, default=None):
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default if default is not None else {}


@dataclass
class SubTask:
    """结构化子任务：总控拆解产物，管理者据此二次派发。"""

    task_id: str
    title: str
    objective: str                 # 要做什么（给管理者看）
    assignee: str                  # 指派给哪个管理者
    depends_on: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)   # 人类可读验收标准
    checks: list[dict] = field(default_factory=list)      # 机器可执行验收项
    parallel_group: str | None = None                     # 同组并行执行
    condition: str | None = None                          # 条件分支表达式
    requires_human: bool = False                          # 人工确认节点
    write_op: bool = False                                # 是否含关键写操作
    skill: str | None = None
    tool: str | None = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "objective": self.objective,
            "assignee": self.assignee,
            "depends_on": self.depends_on,
            "acceptance": self.acceptance,
            "checks": self.checks,
            "parallel_group": self.parallel_group,
            "condition": self.condition,
            "requires_human": self.requires_human,
            "write_op": self.write_op,
            "skill": self.skill,
            "tool": self.tool,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubTask":
        return cls(
            task_id=data["task_id"],
            title=data["title"],
            objective=data.get("objective", ""),
            assignee=data["assignee"],
            depends_on=list(data.get("depends_on") or []),
            acceptance=list(data.get("acceptance") or []),
            checks=list(data.get("checks") or []),
            parallel_group=data.get("parallel_group"),
            condition=data.get("condition"),
            requires_human=bool(data.get("requires_human")),
            write_op=bool(data.get("write_op")),
            skill=data.get("skill"),
            tool=data.get("tool"),
            params=dict(data.get("params") or {}),
        )


@dataclass
class TaskPlan:
    """总控产出的结构化任务计划。"""

    plan_id: str
    session_id: str
    intent: str
    subtasks: list[SubTask]
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "intent": self.intent,
            "subtasks": [t.to_dict() for t in self.subtasks],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskPlan":
        return cls(
            plan_id=data["plan_id"],
            session_id=data["session_id"],
            intent=data.get("intent", "generic"),
            subtasks=[SubTask.from_dict(s) for s in data.get("subtasks", [])],
            created_at=data.get("created_at", now_iso()),
        )


@dataclass
class Job:
    """作业：管理者把子任务拆成的原子执行单元，由执行 Agent 承担。"""

    job_id: str
    task_id: str
    session_id: str
    agent_id: str
    action: str            # tool | skill
    target: str            # 工具名或技能名
    params: dict = field(default_factory=dict)
    status: str = "pending"    # pending | done | failed | interrupted
    attempts: int = 0
    output: dict | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "status": self.status,
            "attempts": self.attempts,
            "output": self.output,
            "error": self.error,
        }
