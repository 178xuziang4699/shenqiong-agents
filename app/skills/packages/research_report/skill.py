"""技能包：结构化调研报告。借鉴 Deep Research 的章节骨架，离线也产出完整定性报告。"""

from app.office import render, topic_of


def run(params: dict, ctx: dict | None = None) -> dict:
    spec = str(params.get("spec") or "").strip() or "调研一下目标市场"
    topic = topic_of(spec, "research")
    content = render("research", topic, "技能包 · 结构化调研模板")
    return {
        "ok": True, "doc_type": "research", "topic": topic,
        "title": f"{topic} · 调研报告", "chars": len(content),
        "lines": len(content.splitlines()), "source_zh": "技能包结构化调研",
        "content": content,
    }
