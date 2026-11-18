"""组装根（composition root）：所有对象只在这里实例化一次，依赖显式注入。

好处很直接：想知道"谁依赖谁"看这一个文件就够，不用追构造链；
本地工具处理器（kb.search / kb.ingest）也能通过 get_runtime() 拿到同一个 repo。
"""

from __future__ import annotations

import threading

from app.config import Settings, get_settings
from app.core.confirm import Confirmer
from app.core.executor import ExecutorAgent
from app.core.handoff import HandoffRecorder
from app.core.manager import ManagerAgent
from app.core.planner import Planner
from app.core.recovery import RecoveryService
from app.core.router import ModeRouter
from app.core.supervisor import Supervisor
from app.core.workflow import WorkflowEngine
from app.db.database import get_db
from app.db.repository import Repository
from app.llm import LLM
from app.models import new_id
from app.obs.diagnose import diagnose, format_diagnosis
from app.obs.logger import StructuredLogger
from app.obs.tracer import Tracer
from app.skills.loader import SkillLoader
from app.tools.registry import ToolRegistry


class Runtime:
    def __init__(self, settings: Settings | None = None, echo: bool = True,
                 confirm_mode: str | None = None, confirm_script: list[str] | None = None):
        self.settings = settings or get_settings()
        self.echo = echo
        self.db = get_db(self.settings.db_dsn)
        self.repo = Repository(self.db)
        self.log = StructuredLogger(self.repo, self.settings, echo=echo)
        self.tracer = Tracer(self.repo, self.log, self.settings)
        self.confirmer = Confirmer(self.settings, mode=confirm_mode, script=confirm_script)
        self.handoff = HandoffRecorder(self.repo, self.log)
        self.registry = ToolRegistry(self.settings, self.repo, self.tracer, self.log, self.confirmer)
        self.skills = SkillLoader(self.settings, self.registry)

        # ---- 执行层 ----
        agent_cfg = self.settings.agents or {}
        self.executors: dict[str, ExecutorAgent] = {}
        for item in agent_cfg.get("executors", []) or []:
            ex = ExecutorAgent(
                agent_id=item["id"], name=item.get("name", item["id"]), settings=self.settings,
                repo=self.repo, tracer=self.tracer, log=self.log, registry=self.registry,
                skills=self.skills,
            )
            ex.tools = list(item.get("tools") or [])
            ex.skill_names = list(item.get("skills") or [])
            self.executors[ex.agent_id] = ex

        # ---- 管理层 ----
        self.managers: dict[str, ManagerAgent] = {}
        for item in agent_cfg.get("managers", []) or []:
            pool = {eid: self.executors[eid] for eid in item.get("executors", []) if eid in self.executors}
            mgr = ManagerAgent(
                agent_id=item["id"], name=item.get("name", item["id"]), settings=self.settings,
                repo=self.repo, tracer=self.tracer, log=self.log, registry=self.registry,
                skills=self.skills, handoff=self.handoff, executors=pool,
            )
            self.managers[mgr.agent_id] = mgr

        # ---- 编排层 ----
        self.engine = WorkflowEngine(
            self.settings, self.repo, self.tracer, self.log, self.managers, self.handoff, self.confirmer
        )
        self.router = ModeRouter(self.settings)
        self.planner = Planner(self.settings, self.log)
        self.llm = LLM(self.settings, self.log)
        self.supervisor = Supervisor(
            self.settings, self.repo, self.tracer, self.log, self.router, self.planner,
            self.llm, self.registry, self.engine, self.handoff,
            self.skills,
        )
        self.recovery = RecoveryService(
            self.settings, self.repo, self.tracer, self.log, self.engine, self.handoff, self.confirmer
        )
        self._started = False
        self._lock = threading.Lock()
        set_runtime(self)

    # ---------- 生命周期 ----------
    def startup(self) -> "Runtime":
        with self._lock:
            if self._started:
                return self
            self.registry.start()
            self.skills.discover()
            self._started = True
        for msg in list(self.registry.warnings) + list(self.skills.warnings):
            print(f"[启动] {msg}")
        return self

    def shutdown(self) -> None:
        self.registry.close()

    def __enter__(self) -> "Runtime":
        return self.startup()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ---------- 便捷入口 ----------
    @staticmethod
    def new_session_id(prefix: str = "sess") -> str:
        return new_id(prefix)

    def chat(self, text: str, session_id: str | None = None) -> dict:
        return self.supervisor.handle(session_id or self.new_session_id("chat"), text)

    def run_task(self, text: str, session_id: str | None = None) -> dict:
        """CLI 的 run-task 是用户显式下达的办事指令，走 work 通道并强制编排动作。"""
        return self.supervisor.handle(
            session_id or self.new_session_id("task"), text,
            channel="work", force_action="task")

    def resume(self, session_id: str) -> dict:
        return self.recovery.resume(session_id)

    def diagnose(self, session_id: str) -> dict:
        return diagnose(self.repo, session_id, self.settings)

    def diagnose_text(self, session_id: str) -> str:
        return format_diagnosis(self.diagnose(session_id))

    def replay(self, session_id: str) -> list[dict]:
        return self.repo.replay_chain(session_id)

    def graph(self, session_id: str) -> str:
        return self.handoff.graph_text(session_id)


_RUNTIME: Runtime | None = None


def get_runtime() -> Runtime:
    """给本地工具处理器（kb.*）用的进程内单例。"""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime()
        _RUNTIME.startup()
    return _RUNTIME


def set_runtime(runtime: Runtime | None) -> None:
    global _RUNTIME
    _RUNTIME = runtime
