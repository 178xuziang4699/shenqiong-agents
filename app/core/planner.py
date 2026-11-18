"""规划器：把自然语言指令变成结构化任务计划（子任务 / 依赖 / 指派 / 验收标准）。

默认规则化规划（确定性、离线可跑）。要接大模型做规划，只需替换 build_plan 内部
生成 subtasks 的那一段，Plan/TaskPlan 结构与管理者的派发逻辑都不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import SubTask, TaskPlan, new_id

_PATH_RE = re.compile(r"([\w\u4e00-\u9fff./-]+\.(?:md|txt|json|csv|log))")


@dataclass
class PlannerContext:
    intent: str
    source_path: str
    output_path: str
    title: str


class Planner:
    def __init__(self, settings, log=None):
        self.settings = settings
        self.log = log

    # ---------- 意图 ----------
    @staticmethod
    def intent_of(text: str) -> str:
        if any(k in text for k in ("写代码", "写个代码", "生成代码", "代码", "函数", "脚本",
                                   "写程序", "开发", "实现", "单元测试", ".py", ".js", ".html")):
            return "code_gen"
        if any(k in text for k in ("报告", "笔记", "整理", "摘要", "汇总", "总结")):
            return "content_report"
        if any(k in text for k in ("统计", "计算", "数据", "字数", "指标")):
            return "data_stat"
        if any(k in text for k in ("检索", "查一下", "搜一下", "知识库")):
            return "kb_lookup"
        return "generic"

    def _ctx(self, text: str, intent: str) -> PlannerContext:
        paths = _PATH_RE.findall(text or "")
        source = "samples/notes.md"
        output = "output/report.md"
        for p in paths:
            if any(p.startswith(prefix) for prefix in ("output/", "out/")) or "报告" in text[:text.find(p) + len(p)]:
                output = p
            else:
                source = p
        if intent == "content_report" and len(paths) >= 2:
            source, output = paths[0], paths[-1]
        title = "素材整理报告"
        m = re.search(r"生成(?:一份|一个)?(.{2,20}?)报告", text or "")
        if m:
            title = f"{m.group(1).strip()}报告"
        return PlannerContext(intent=intent, source_path=source, output_path=output, title=title)

    # ---------- 计划 ----------
    def build_plan(self, session_id: str, text: str, llm=None, catalog: dict | None = None) -> TaskPlan:
        """规则模板打底；意图为 generic 时改由模型动态生成子任务（白名单校验 + 模板兜底）。

        成熟链路（code_gen / content_report / data_stat / kb_lookup）保留确定性模板，
        避免模型波动破坏演示与回归；只对原本最弱的「通用任务」做动态升级。
        """
        intent = self.intent_of(text)
        ctx = self._ctx(text, intent)
        dynamic = None
        if llm is not None and intent == "generic" and catalog:
            dynamic = llm.plan_subtasks(text, catalog)
        if dynamic:
            subtasks = self._from_dynamic(dynamic)
            intent = "dynamic"
        elif intent == "code_gen":
            subtasks = self._code_gen_plan(text)
        elif intent == "content_report":
            subtasks = self._content_report_plan(ctx)
        elif intent == "data_stat":
            subtasks = self._data_stat_plan(ctx)
        elif intent == "kb_lookup":
            subtasks = self._kb_lookup_plan(ctx, text)
        else:
            subtasks = self._generic_plan(ctx, text)
        return TaskPlan(plan_id=new_id("plan"), session_id=session_id, intent=intent, subtasks=subtasks)

    @staticmethod
    def _from_dynamic(items: list[dict]) -> list[SubTask]:
        """把模型产出的 JSON 子任务转成 SubTask；pipeline 原样交给管理者执行。"""
        out: list[SubTask] = []
        for t in items:
            pipe = t["pipeline"]
            first = pipe[0]
            out.append(SubTask(
                task_id=t["task_id"],
                title=t["title"],
                objective=t["title"],
                assignee=t["assignee"],
                depends_on=list(t.get("depends_on") or []),
                skill=first.get("target") if first.get("action") == "skill" else None,
                tool=first.get("target") if first.get("action") == "tool" else None,
                params={"pipeline": pipe},
                acceptance=["作业链执行完成且无异常"],
            ))
        return out

    # ---- 模板：素材 → 摘要 → 报告 → 落盘 → 入库（串行+并行+条件+人工+写确认）----
    def _content_report_plan(self, c: PlannerContext) -> list[SubTask]:
        common_stats = {"素材字数": "{{T2.words}}", "要点数": "{{T1.points}}"}
        return [
            SubTask(
                task_id="T1", title="抽取笔记要点", assignee="mgr_content",
                objective=f"读取 {c.source_path}，抽取不超过 5 条核心要点",
                parallel_group="analyze",
                skill="summarize_notes",
                params={"pipeline": [
                    {"action": "tool", "target": "fs.read_text", "params": {"path": c.source_path}},
                    {"action": "skill", "target": "summarize_notes",
                     "params": {"text": "{{jobs.j0.content}}", "max_points": 5}},
                ]},
                acceptance=["要点数量 1~5 条", "摘要总长度不少于 30 字"],
                checks=[{"path": "output.summary_len", "op": ">=", "value": 30},
                        {"path": "output.points", "op": ">=", "value": 1}],
            ),
            SubTask(
                task_id="T2", title="统计素材规模", assignee="mgr_data",
                objective=f"统计 {c.source_path} 的字数，作为报告的数据支撑",
                parallel_group="analyze",
                tool="calc.word_count",
                params={"pipeline": [
                    {"action": "tool", "target": "fs.read_text", "params": {"path": c.source_path}},
                    {"action": "tool", "target": "calc.word_count", "params": {"text": "{{jobs.j0.content}}"}},
                ]},
                acceptance=["返回字数大于 0"],
                checks=[{"path": "output.words", "op": ">=", "value": 50}],
            ),
            SubTask(
                task_id="T3", title="生成报告草稿", assignee="mgr_content",
                objective="把要点与统计组装成 Markdown 报告草稿",
                depends_on=["T1", "T2"],
                condition="T1.summary_len > 0",
                skill="compose_report",
                params={"pipeline": [
                    {"action": "skill", "target": "compose_report",
                     "params": {"title": c.title, "summary": "{{T1.summary}}", "stats": common_stats,
                                "output_path": c.output_path}},
                ]},
                acceptance=["报告长度不少于 200 字", "包含三个一级章节"],
                checks=[{"path": "output.report_len", "op": ">=", "value": 200}],
            ),
            SubTask(
                task_id="T4", title="写入报告文件", assignee="mgr_content",
                objective=f"把草稿写入 {c.output_path}（关键写操作，需人工确认）",
                depends_on=["T3"], requires_human=True, write_op=True,
                params={"pipeline": [
                    {"action": "tool", "target": "fs.write_text",
                     "params": {"path": "{{T3.output_path}}", "content": "{{T3.report}}"}},
                ]},
                acceptance=["文件写入成功且字节数大于 200"],
                checks=[{"path": "output.bytes", "op": ">=", "value": 200}],
            ),
            SubTask(
                task_id="T5", title="归档到知识库", assignee="mgr_data",
                objective=f"把 {c.output_path} 切分向量化入库，供后续检索",
                depends_on=["T4"], write_op=True,
                params={"pipeline": [
                    {"action": "tool", "target": "kb.ingest",
                     "params": {"path": "{{T3.output_path}}", "title": c.title}},
                ]},
                acceptance=["至少生成 1 个切块"],
                checks=[{"path": "output.chunks", "op": ">=", "value": 1}],
            ),
        ]

    def _data_stat_plan(self, c: PlannerContext) -> list[SubTask]:
        return [
            SubTask(
                task_id="T1", title="读取并统计素材", assignee="mgr_data",
                objective=f"读取 {c.source_path} 并统计字数与行数",
                params={"pipeline": [
                    {"action": "tool", "target": "fs.read_text", "params": {"path": c.source_path}},
                    {"action": "tool", "target": "calc.word_count", "params": {"text": "{{jobs.j0.content}}"}},
                ]},
                acceptance=["字数大于 0"], checks=[{"path": "output.words", "op": ">=", "value": 1}],
            ),
            SubTask(
                task_id="T2", title="生成统计摘要", assignee="mgr_content",
                objective="把统计结果写成一段摘要",
                depends_on=["T1"],
                skill="summarize_notes",
                params={"pipeline": [
                    {"action": "skill", "target": "summarize_notes",
                     "params": {"text": "素材共 {{T1.words}} 字、{{T1.lines}} 行。请据此给出阅读建议。",
                                "max_points": 3}},
                ]},
                acceptance=["摘要非空"], checks=[{"path": "output.summary_len", "op": ">=", "value": 5}],
            ),
        ]

    def _kb_lookup_plan(self, c: PlannerContext, text: str) -> list[SubTask]:
        return [
            SubTask(
                task_id="T1", title="知识库检索", assignee="mgr_data",
                objective="在知识库中检索与指令相关的原文片段",
                tool="kb.search",
                params={"pipeline": [
                    {"action": "tool", "target": "kb.search", "params": {"query": text, "top_k": 3}},
                ]},
                acceptance=["返回结果必须带来源引用"],
                checks=[{"path": "output.citations", "op": "not_empty"}],
            ),
        ]

    # ---- 模板：写代码 → 语法自检 → 落盘（关键写操作需人工确认）----
    def _code_gen_plan(self, text: str) -> list[SubTask]:
        m = re.search(r"([\w\u4e00-\u9fff./-]+\.(?:py|js|ts|html|sql|sh))", text or "")
        raw = m.group(1) if m else ""
        name = raw.rsplit("/", 1)[-1] if raw else "main.py"
        output = raw if raw.startswith(("output/", "out/")) else f"output/{name}"
        low = (text or "").lower()
        language = "python"
        if any(k in low for k in ("javascript", "node.js", "用 js", "写 js", ".js")):
            language = "javascript"
        elif ".html" in low or "网页" in low or "页面" in low:
            language = "html"
        # 去掉落盘路径描述，剩余部分作为代码需求交给生成技能
        spec = re.sub(
            r"(?:写入|保存到|存到|生成到)\s*[\w\u4e00-\u9fff./-]+\.\w+", "", text or ""
        ).strip()
        spec = spec or "写一个 Python 工具函数，带可运行示例与单元测试"
        return [
            SubTask(
                task_id="T1", title="按需求生成代码", assignee="mgr_content",
                objective="解析需求，生成完整可运行代码（模型可用则由模型生成，否则用离线模板）",
                skill="code_generate",
                params={"pipeline": [
                    {"action": "skill", "target": "code_generate",
                     "params": {"spec": spec, "language": language, "filename": output}},
                ]},
                acceptance=["代码行数不少于 5 行"],
                checks=[{"path": "output.lines", "op": ">=", "value": 5}],
            ),
            SubTask(
                task_id="T2", title="代码语法自检", assignee="mgr_data",
                objective="对生成代码做语法检查，不通过则打回重试",
                depends_on=["T1"], condition="T1.lines > 0",
                skill="code_check",
                params={"pipeline": [
                    {"action": "skill", "target": "code_check",
                     "params": {"code": "{{T1.code}}", "language": "{{T1.language}}"}},
                ]},
                acceptance=["语法检查通过，无错误项"],
                checks=[{"path": "output.lines", "op": ">=", "value": 1}],
            ),
            SubTask(
                task_id="T3", title="写入代码文件", assignee="mgr_content",
                objective=f"把代码写入 {output}（关键写操作，需人工确认）",
                depends_on=["T2"], requires_human=True, write_op=True,
                params={"pipeline": [
                    {"action": "tool", "target": "fs.write_text",
                     "params": {"path": output, "content": "{{T1.code}}", "overwrite": True}},
                ]},
                acceptance=["文件写入成功且字节数大于 20"],
                checks=[{"path": "output.bytes", "op": ">=", "value": 20}],
            ),
        ]

    def _generic_plan(self, c: PlannerContext, text: str) -> list[SubTask]:
        return [
            SubTask(
                task_id="T1", title="环境盘点", assignee="mgr_data",
                objective="列出工作区文件，作为通用任务的起点",
                params={"pipeline": [
                    {"action": "tool", "target": "fs.list_dir", "params": {"path": ".", "pattern": "*.md"}},
                ]},
                acceptance=["返回文件数量"], checks=[{"path": "output.count", "op": ">=", "value": 0}],
            ),
            SubTask(
                task_id="T2", title="检索相关信息", assignee="mgr_data",
                objective="用原始指令在知识库中检索，形成结论",
                depends_on=["T1"], tool="kb.search",
                params={"pipeline": [
                    {"action": "tool", "target": "kb.search", "params": {"query": text, "top_k": 3}},
                ]},
                acceptance=["返回结果带引用字段"],
                checks=[{"path": "output.hits", "op": "exists"}],
            ),
        ]
