"""总控 Agent（Supervisor）：平台唯一的入口。

沙箱隔离：用户显式选择「对话 / 办事」两套通道，通道归属写死在会话上，
消息内容（包括任何关键词）都不能改变它。两个通道都开放全部能力，
既能正常对话，也能产出文档、跑多智能体编排。

它只做三件事：
1. 校验通道 —— 只认显式传入的 channel，绝不由文本推断；
2. 通道内动作判定 —— 语义决定本轮「直接回答 / 产出文档 / 编排办事」；
3. 派发与收口 —— 把计划交给工作流引擎，汇总成自然语言答复。

关键词路由（ModeRouter / routing.yaml）已下线，仅保留为可观测接口。
"""

from __future__ import annotations

import json
import re

from app.core.expr import resolve_path  # noqa: F401
from app.core.handoff import HandoffRecorder
from app.core.planner import Planner
from app.core.router import ModeRouter  # noqa: F401 - 仅兼容装配与可观测，不参与分流
from app.db.repository import Repository
from app.errors import AgentPlatformError
from app.llm import LLM, LLMError
from app.office import OfficeEngine
from app.models import TaskPlan, now_iso
from app.obs.tracer import Tracer
from app.tools.registry import ToolContext


class Supervisor:
    agent_id = "supervisor"

    def __init__(self, settings, repo: Repository, tracer: Tracer, log, router: ModeRouter,
                 planner: Planner, llm: LLM, registry, engine, handoff: HandoffRecorder,
                 skills=None):
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.router = router
        self.planner = planner
        self.llm = llm
        self.registry = registry
        self.engine = engine
        self.handoff = handoff
        self.skills = skills          # 技能直达：点击技能卡片后直接执行
        self.office = OfficeEngine(llm)  # 办公引擎：一句话 → 一份可读交付文档

    # ==================== 入口：沙箱通道 ====================
    CHANNELS = ("chat", "work")

    @staticmethod
    def normalize_channel(channel: str | None) -> str:
        return channel if channel in Supervisor.CHANNELS else "chat"

    def handle(self, session_id: str, text: str, skill: str | None = None,
               channel: str = "chat", force_action: str | None = None,
               owner: str = "default") -> dict:
        """平台唯一入口。沙箱原则（不可违反）：

        1. channel 只来自调用方显式传入（用户在界面上选的两套通道），
           **绝不由消息内容推断**；缺省按最保守的「对话」通道处理。
        2. 会话创建时绑定 channel，之后任何请求都不能改写；跨通道发送直接拒绝，
           两套通道的历史、快照、日志、产物互不共享。
        3. 消息内容只用于决定「本轮在通道内怎么答」（reply / document / task），
           两个通道都开放全部三种动作，因此都能对话、也都能办事。
        4. 关键词规则（ModeRouter / routing.yaml / office.is_office）已从分流链路
           彻底移除，仅保留为可观测接口，不参与任何模式判定。
        5. owner 是第二阶段的用户/租户级隔离：会话创建时绑定归属，跨归属访问拒绝。
           缺省 'default' 与第一阶段历史数据一致，不改变任何既有行为。
        """
        ch = self.normalize_channel(channel)
        owner = owner or "default"
        row = self.repo.get_session(session_id)
        if row is None:
            self.repo.create_session(session_id, mode=ch, channel=ch, owner=owner)
            bound = ch
        else:
            bound = row.get("channel") or "chat"
        if bound != ch:
            raise AgentPlatformError(
                f"沙箱隔离：会话 {session_id} 绑定在「{bound}」通道，"
                f"不能在「{ch}」通道发送。请切回对应通道，或新建一个 {ch} 通道会话。",
                error_code="CHANNEL_ISOLATION",
                detail={"session_id": session_id, "bound_channel": bound,
                        "requested_channel": ch},
            )
        bound_owner = (row or {}).get("owner") or "default"
        if row is not None and bound_owner != owner:
            raise AgentPlatformError(
                f"沙箱隔离：会话 {session_id} 属于用户「{bound_owner}」，"
                f"用户「{owner}」不能访问该会话的数据。",
                error_code="OWNER_ISOLATION",
                detail={"session_id": session_id, "bound_owner": bound_owner,
                        "requested_owner": owner},
            )

        if skill:
            return self._skill(session_id, text, skill, ch)

        with self.tracer.step(session_id, self.agent_id, "route",
                              {"text": text, "channel": ch}) as st:
            # force_action 只由调用方显式给定（如 CLI 的 run-task 子命令），
            # 绝不由消息内容推断，仍不改变通道归属。
            if force_action in ("reply", "document", "task"):
                decision = {
                    "channel": ch, "action": force_action, "source": "explicit",
                    "reason": "调用方显式指定动作",
                    "note": "通道由用户显式选择，关键词不参与模式判定",
                }
            else:
                decision = self._decide_action(session_id, text, ch)
            self.log.info(
                "supervisor",
                f"通道 {ch}（显式指定，不受消息内容影响）内动作 → {decision['action']}"
                f"（来源 {decision['source']}，{decision['reason']}）",
                session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                context=decision,
            )
            st.set_output(decision)

        if decision["action"] == "document":
            return self._office(session_id, text, decision, ch)
        if decision["action"] == "task":
            return self._task(session_id, text, decision, ch)
        return self._chat(session_id, text, decision, ch)

    # ==================== 通道内动作判定（只决定怎么答，不改通道）====================
    def _decide_action(self, session_id: str, text: str, channel: str) -> dict:
        """语义判定本轮动作。不调用 ModeRouter、不调用 is_office、不接收任何
        关键词 hint——关键词（请 / 帮我 / 分析一下 / 帮我办件事）不能影响判定。

        模型不可用时降级 reply：宁可只给一段对话回答，也绝不误触发办事。
        """
        history = self.repo.list_chat_turns(session_id)
        got = self.llm.understand_action(text, history)
        return {
            "channel": channel,
            "action": got.get("action", "reply"),
            "reason": got.get("reason", ""),
            "source": got.get("source", "fallback"),
            "note": "通道由用户显式选择，关键词不参与模式判定",
        }

    def _catalog(self) -> dict:
        """给动态规划器一份真实能力白名单（模型不得编造不存在的工具/技能）。"""
        try:
            tools = [t.get("name") for t in self.registry.catalogue()]
        except Exception:  # noqa: BLE001
            tools = []
        try:
            skills = list(self.skills.packages.keys())
        except Exception:  # noqa: BLE001
            skills = []
        try:
            managers = list(self.engine.managers.keys())
        except Exception:  # noqa: BLE001
            managers = []
        return {"managers": managers, "tools": [t for t in tools if t], "skills": skills}

    @staticmethod
    def _artifacts_from(outcome: dict) -> list[dict]:
        arts: list[dict] = []
        for payload in ((outcome or {}).get("results") or {}).values():
            if not isinstance(payload, dict):
                continue
            path = payload.get("filename") or payload.get("path")
            if path:
                arts.append({"path": path, "bytes": payload.get("bytes"),
                             "lines": payload.get("lines")})
        return arts[:5]

    # ==================== 办公模式：一句话 → 一份真实交付文档 ====================
    def _office(self, session_id: str, text: str, decision: dict, channel: str) -> dict:
        """产出可读、可下载、已落盘的文档；快照与日志契约照旧，原有能力不受影响。

        任何通道都可调用；这里不再改写会话模式（通道归属不可变）。
        """
        with self.tracer.step(session_id, self.agent_id, "chat.respond",
                              {"text": text, "intent": "office"}) as st:
            doc = self.office.produce(text)
            written, write_err = False, ""
            try:
                res = self.registry.call(
                    "fs.write_text",
                    {"path": doc["path"], "content": doc["content"], "overwrite": True},
                    ToolContext(session_id=session_id, agent_id=self.agent_id),
                )
                written = res.get("bytes") is not None
            except AgentPlatformError as exc:
                write_err = str(exc)
                self.log.warning(
                    "supervisor", f"办公文档落盘失败，正文仍回传：{exc}",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=st.step_index, error_code=exc.error_code,
                )
            except Exception as exc:  # noqa: BLE001 - 落盘异常也不能吞掉交付物
                write_err = f"{type(exc).__name__}: {exc}"
                self.log.warning(
                    "supervisor", f"办公文档落盘异常，正文仍回传：{exc}",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=st.step_index, error_code="WRITE_ERROR",
                )
            answer = doc["summary"]
            if not written:
                answer += (f"\n\n注：文件写入未成功（{write_err or '未知原因'}），"
                           "文档正文已在下方完整给出，可直接复制保存。")
            meta = {k: doc[k] for k in (
                "doc_type", "doc_type_zh", "topic", "title", "path",
                "chars", "lines", "source_zh")}
            meta["written"] = written
            st.set_output({"answer": answer, "citations": [], "office": meta,
                           "history_turns": len(self.repo.list_chat_turns(session_id)) // 2,
                           "degraded": False})
            self.log.info(
                "supervisor",
                f"办公交付完成：{doc['doc_type_zh']}《{doc['title']}》"
                f"（{doc['chars']} 字，来源 {doc['source_zh']}，落盘={'成功' if written else '失败'}）",
                session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                context={"doc_type": doc["doc_type"], "topic": doc["topic"],
                         "path": doc["path"], "written": written},
            )
        self.repo.update_session(session_id, status="completed")
        return {
            "session_id": session_id,
            "mode": "chat",
            "channel": channel,
            "dispatched": False,
            "decision": decision,
            "answer": answer,
            "citations": [],
            "office": {**meta, "content": doc["content"]},
            "artifacts": [{
                "kind": "document", "path": doc["path"], "title": doc["title"],
                "doc_type_zh": doc["doc_type_zh"], "chars": doc["chars"],
                "lines": doc["lines"], "source_zh": doc["source_zh"],
                "written": written,
            }],
            "engine": self.llm.describe(),
        }

    # ==================== 技能直达（点击技能卡片即自动使用）====================
    _FIELD_ZH = {
        "lines": "代码行数", "bytes": "字节数", "words": "字数", "points": "要点数",
        "count": "数量", "hits": "命中数", "citations": "来源引用", "errors": "错误项",
        "checked": "检查方式", "filename": "目标文件", "source_zh": "生成方式",
        "language": "语言", "spec": "需求", "summary_len": "摘要长度",
        "ms": "耗时(ms)", "ok": "成功", "exists": "存在", "size": "大小",
    }
    _TEXT_ARG_KEYS = ("text", "json", "data", "content", "query", "expression",
                      "pattern", "spec", "code", "left", "url", "topic", "title", "label")
    _PATH_ARG_KEYS = ("path", "right", "filename")

    def _skill_args(self, name: str, text: str) -> tuple[dict, str | None]:
        """把用户消息填进技能的主输入参数，其余参数沿用 manifest 的示例值。"""
        pkg = self.skills.packages.get(name)
        if pkg is None:
            self.skills.discover()
            pkg = self.skills.packages.get(name)
        if pkg is None:
            return {"text": text}, "text"
        props = ((pkg.manifest.get("inputs") or {}).get("properties") or {})
        sample = dict(pkg.manifest.get("sample_args") or {})
        primary = next(
            (k for k in self._TEXT_ARG_KEYS if (props.get(k) or {}).get("type") == "string"),
            None,
        )
        if primary is None:
            primary = next(
                (k for k in self._PATH_ARG_KEYS if (props.get(k) or {}).get("type") == "string"),
                None,
            )
        # 去掉前端预填的「用【技能名】处理以下内容：」前缀。
        # 前缀说明文字里不能再出现冒号：否则用户把 URL 贴在同一行时，
        # 贪婪匹配会一路吃到 https: 的冒号，把 https://example.com 截成 //example.com。
        clean = re.sub(r"^用【[^】]+】[^：:\n]*[：:]\s*", "", text or "", count=1).strip()
        clean = clean.lstrip("\n").strip()
        if primary and clean:
            if primary in self._PATH_ARG_KEYS and not re.search(r"\.\w{1,5}$", clean):
                pass  # 自然语言不像路径，保留示例默认值，避免把文本当文件路径
            else:
                sample[primary] = clean
        return sample, primary

    @staticmethod
    def _human_value(v) -> str:
        """把结构化值说成人话：列表取关键词，字典串成短语，不再甩 JSON。"""
        if isinstance(v, list):
            parts = []
            for item in v[:8]:
                if isinstance(item, dict):
                    word = next((item[k] for k in ("word", "name", "label", "key", "title")
                                 if k in item), None)
                    parts.append(str(word) if word is not None else str(item)[:40])
                else:
                    parts.append(str(item)[:40])
            return "、".join(parts) + (f" 等 {len(v)} 项" if len(v) > 8 else "")
        if isinstance(v, dict):
            return "、".join(f"{k}={str(x)[:30]}" for k, x in list(v.items())[:5])
        s = str(v)
        return s[:120] + ("…" if len(s) > 120 else "")

    @classmethod
    def _format_skill_output(cls, name: str, out: dict, ok: bool,
                             primary: str | None) -> str:
        """自然语言汇报：先说做了什么、结果是什么；原始结构化数据交给前端折叠展示。"""
        if not ok:
            return (f"我用【{name}】试了一下，这次没能执行成功。\n"
                    f"原因：{out.get('error') or '未知错误'}\n"
                    "换个输入再试一次，或者把需求说完整交给办事模式，我来重新安排。")
        big = ("code", "content", "text", "json", "data", "report", "html")
        lines = [f"已用【{name}】处理完成" +
                 (f"，你的输入已填入参数 {primary}。" if primary else "。"), ""]
        rows: list[str] = []
        for k, v in out.items():
            if k in ("ok", "skill"):
                continue
            label = cls._FIELD_ZH.get(k, k)
            if k in big:
                rows.append(f"· {label}：生成了 {len(str(v))} 字符的内容")
            else:
                rows.append(f"· {label}：{cls._human_value(v)}")
        if rows:
            lines += ["主要结果："] + rows[:7] + [""]
        lines.append("原始结构化数据可在下方展开查看；想把它整理成一份正式文档，直接说「写成报告」就行。")
        return "\n".join(lines)

    def _skill(self, session_id: str, text: str, skill_name: str,
               channel: str) -> dict:
        """点技能卡片 → 直接执行该技能 → 结果以对话气泡返回，并写入 chat.respond 快照。

        两个通道都能用技能；不改写会话模式（通道归属不可变）。
        仍然遵守日志契约（session_id/agent_id/step_index）与失败不静默；
        刷新页面后由 /replay 回放，技能结果不会丢。
        """
        args, primary = self._skill_args(skill_name, text)
        ok = True
        with self.tracer.step(session_id, self.agent_id, "chat.respond",
                              {"text": text, "skill": skill_name}) as st:
            try:
                out = self.skills.run(skill_name, args, {"source": "chat_skill"})
            except AgentPlatformError as exc:
                ok = False
                out = {"error": exc.message, "error_code": exc.error_code}
                self.log.warning(
                    "supervisor", f"技能直达失败 {skill_name}：{exc}",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=st.step_index, error_code=exc.error_code,
                )
            except Exception as exc:  # noqa: BLE001 - 技能包自身异常也必须回报
                ok = False
                out = {"error": f"{type(exc).__name__}: {exc}", "error_code": "SKILL_ERROR"}
                self.log.error(
                    "supervisor", f"技能直达异常 {skill_name}：{exc}",
                    session_id=session_id, agent_id=self.agent_id,
                    step_index=st.step_index, error_code="SKILL_ERROR",
                )
            # 技能若产出的是完整文档（不是零散字段），同样交付成可预览可下载的文件
            office_meta = None
            if ok and isinstance(out, dict):
                body = out.get("content")
                if isinstance(body, str) and len(body) > 300 and body.lstrip().startswith("#"):
                    from datetime import datetime
                    path = ("output/技能交付_" + skill_name + "_"
                            + datetime.now().strftime("%Y%m%d_%H%M%S") + ".md")
                    written = False
                    try:
                        res = self.registry.call(
                            "fs.write_text",
                            {"path": path, "content": body, "overwrite": True},
                            ToolContext(session_id=session_id, agent_id=self.agent_id),
                        )
                        written = res.get("bytes") is not None
                    except Exception as exc:  # noqa: BLE001 - 落盘失败不吞掉正文
                        self.log.warning(
                            "supervisor", f"技能文档落盘失败，正文仍回传：{exc}",
                            session_id=session_id, agent_id=self.agent_id,
                            step_index=st.step_index, error_code="WRITE_ERROR",
                        )
                    office_meta = {
                        "doc_type": out.get("doc_type") or "skill_doc",
                        "doc_type_zh": out.get("doc_type_zh") or "技能交付文档",
                        "topic": out.get("topic") or skill_name,
                        "title": out.get("title") or f"{skill_name} 交付文档",
                        "path": path, "chars": len(body),
                        "lines": len(body.splitlines()),
                        "source_zh": out.get("source_zh") or "技能包生成",
                        "written": written, "content": body,
                    }
            answer = self._format_skill_output(skill_name, out, ok, primary)
            snap_office = ({k: v for k, v in office_meta.items() if k != "content"}
                           if office_meta else None)
            st.set_output({"answer": answer, "citations": [], "skill": skill_name,
                           "ok": ok, "skill_raw": out, "office": snap_office})
            self.log.info(
                "supervisor",
                f"技能直达 {skill_name}（主参数 {primary or '默认'}，结果={'成功' if ok else '失败'}"
                f"{'，附带交付文档' if office_meta else ''}）",
                session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                context={"skill": skill_name, "ok": ok, "has_doc": bool(office_meta)},
            )
        self.repo.update_session(session_id, status="completed")
        return {
            "session_id": session_id,
            "mode": "chat",
            "channel": channel,
            "dispatched": False,
            "decision": {
                "channel": channel, "action": "skill", "source": "explicit",
                "reason": f"技能直达 {skill_name}",
                "note": "通道由用户显式选择，关键词不参与模式判定",
            },
            "answer": answer,
            "citations": [],
            "skill": skill_name,
            "skill_ok": ok,
            "skill_raw": out,
            "office": office_meta,
            "artifacts": ([{
                "kind": "document", "path": office_meta["path"],
                "title": office_meta["title"], "chars": office_meta["chars"],
                "lines": office_meta["lines"], "source_zh": office_meta["source_zh"],
                "written": office_meta["written"],
            }] if office_meta else []),
            "engine": self.llm.describe(),
        }

    # ==================== 对话模式 ====================
    def _chat(self, session_id: str, text: str, decision: dict,
              channel: str) -> dict:
        evidence: list[dict] = []
        history = self.repo.list_chat_turns(session_id)
        engine = self.llm.describe()
        degraded = False
        with self.tracer.step(session_id, self.agent_id, "chat.respond", {"text": text}) as st:
            # 只有明确的资料查询才检索；寒暄/能力询问不检索，避免把无关条文塞给模型
            if self.llm.needs_knowledge(text):
                try:
                    res = self.registry.call(
                        "kb.search", {"query": text, "top_k": 3},
                        ToolContext(session_id=session_id, agent_id=self.agent_id),
                    )
                    evidence = res.get("hits") or []
                except AgentPlatformError as exc:
                    self.log.warning(
                        "supervisor", f"对话模式检索失败，降级为纯对话应答：{exc}",
                        session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                        error_code=exc.error_code,
                    )
            try:
                answer = self.llm.answer(text, evidence, history)
            except LLMError as exc:
                degraded = True
                self.log.warning(
                    "supervisor", f"模型调用失败，降级离线应答：{exc}",
                    session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                    error_code=exc.error_code,
                )
                answer = self.llm.rule_answer(text, evidence, history)
            citations = [h["citation"] for h in evidence]
            st.set_output({
                "answer": answer, "citations": citations,
                "history_turns": len(history) // 2, "degraded": degraded,
            })
            self.log.info(
                "supervisor",
                f"对话模式直接应答（模型={'不可用' if degraded else engine['model']}，"
                f"历史 {len(history) // 2} 轮，"
                f"知识命中 {len(evidence)} 条，降级={degraded}，未触发派发）",
                session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                context={"provider": engine["provider"], "degraded": degraded},
            )
        self.repo.update_session(session_id, status="completed")
        return {
            "session_id": session_id,
            "mode": "chat",
            "channel": channel,
            "dispatched": False,
            "decision": decision,
            "answer": answer,
            "citations": citations,
            "engine": {**engine, "degraded": degraded, "history_turns": len(history) // 2},
        }

    @staticmethod
    def _looks_like_lookup(text: str) -> bool:
        """兼容旧调用点；判定逻辑统一收敛在 LLM.needs_knowledge。"""
        return LLM.needs_knowledge(text)

    # ==================== 办事模式 ====================
    def _task(self, session_id: str, text: str, decision: dict,
              channel: str) -> dict:
        with self.tracer.step(session_id, self.agent_id, "plan", {"text": text}) as st:
            plan: TaskPlan = self.planner.build_plan(
                session_id, text, llm=self.llm, catalog=self._catalog())
            self.repo.save_plan(plan)
            for task in plan.subtasks:
                self.repo.upsert_task(task, session_id, plan.plan_id)
            self.repo.update_session(session_id, intent=plan.intent)
            st.set_output(plan.to_dict())
            self.log.info(
                "supervisor",
                f"生成任务计划 {plan.plan_id}（intent={plan.intent}，{len(plan.subtasks)} 个子任务）",
                session_id=session_id, agent_id=self.agent_id, step_index=st.step_index,
                context={"subtasks": [
                    {"id": t.task_id, "title": t.title, "assignee": t.assignee,
                     "deps": t.depends_on, "write": t.write_op} for t in plan.subtasks
                ]},
            )
            print(self._format_plan(plan))

        outcome = self.engine.run_plan(session_id, plan)
        final = "completed" if outcome["status"] == "completed" else "failed"
        self.repo.update_session(session_id, status=final)

        # 收口：把结构化执行结果汇总成用户能直接读的自然语言结论，
        # 不再只回「已派发 N 个子任务，全部完成」。
        with self.tracer.step(session_id, self.agent_id, "task.summarize",
                              {"status": outcome["status"], "text": text}) as st:
            answer = self.llm.summarize_outcome(
                text, plan.to_dict(), outcome, self._artifacts_from(outcome))
            st.set_output({"answer": answer})
        self.log.info(
            "supervisor", f"计划 {plan.plan_id} 收口：{outcome['status']}",
            session_id=session_id, agent_id=self.agent_id,
            step_index=self.repo.current_step(session_id),
            context={"failed": outcome.get("failed"), "skipped": outcome.get("skipped"),
                     "answer_len": len(answer)},
        )
        return {
            "session_id": session_id,
            "mode": "task",
            "channel": channel,
            "dispatched": True,
            "decision": decision,
            "plan": plan.to_dict(),
            "outcome": outcome,
            "status": final,
            "answer": answer,
        }

    @staticmethod
    def _format_plan(plan: TaskPlan) -> str:
        lines = [f"\n[总控] 任务计划 {plan.plan_id}（intent={plan.intent}）"]
        for t in plan.subtasks:
            deps = ",".join(t.depends_on) or "-"
            flags = []
            if t.write_op:
                flags.append("写操作")
            if t.requires_human:
                flags.append("人工确认")
            if t.condition:
                flags.append(f"条件:{t.condition}")
            if t.parallel_group:
                flags.append(f"并行组:{t.parallel_group}")
            flag_text = f" [{'/'.join(flags)}]" if flags else ""
            lines.append(f"  {t.task_id} · {t.title} → {t.assignee} · 依赖 {deps}{flag_text}")
        return "\n".join(lines)
