"""流转记录器：每次 Agent 之间的流转都必须落一条 (from, to, 原因)。

只做两件事：写 handoffs 表、把流转图渲染成文本。
不做"顺便改任务状态"这类隐式动作——状态变更由发起方显式执行，便于排查。
"""

from __future__ import annotations

from app.db.repository import Repository
from app.obs.logger import StructuredLogger


class HandoffRecorder:
    def __init__(self, repo: Repository, log: StructuredLogger):
        self.repo = repo
        self.log = log

    def record(
        self, session_id: str, from_agent: str, to_agent: str, reason: str,
        *, task_id: str | None = None, verdict: str | None = None,
        agent_id: str | None = None, step_index: int | None = None,
    ) -> None:
        step = step_index if step_index is not None else self.repo.current_step(session_id)
        self.repo.insert_handoff(session_id, step, task_id, from_agent, to_agent, reason, verdict)
        self.log.info(
            "handoff",
            f"{from_agent} → {to_agent}｜{reason}",
            session_id=session_id,
            agent_id=agent_id or from_agent,
            step_index=step,
            context={"task_id": task_id, "verdict": verdict},
        )

    # ---------------- 流转图 ----------------
    def graph_text(self, session_id: str) -> str:
        rows = self.repo.list_handoffs(session_id)
        if not rows:
            return "（无流转记录）"
        nodes: dict[str, int] = {}
        edges: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            nodes[r["from_agent"]] = nodes.get(r["from_agent"], 0) + 1
            nodes[r["to_agent"]] = nodes.get(r["to_agent"], 0) + 1
            key = (r["from_agent"], r["to_agent"])
            edges.setdefault(key, []).append(f"[step {r['step_index']}] {r['reason']}")

        lines = ["参与者（流转次数）："]
        for name, count in sorted(nodes.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"  - {name:<14} {count}")
        lines.append("")
        lines.append("流转边：")
        for (src, dst), reasons in sorted(edges.items()):
            lines.append(f"  {src} ──▶ {dst}  （{len(reasons)} 次）")
            for reason in reasons:
                lines.append(f"        · {reason}")
        return "\n".join(lines)
