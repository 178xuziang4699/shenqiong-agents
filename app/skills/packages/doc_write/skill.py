"""技能包：通用办公文档写作。产出完整 Markdown 正文，不是占位文本。

主对话里说一句需求会走办公引擎（接模型时由模型撰写）；
本技能包保证「离线也能出活」：结构化模板 + 主题填充，可直接预览下载。
"""

from app.office import DOC_SPECS, detect, render, topic_of


def run(params: dict, ctx: dict | None = None) -> dict:
    spec = str(params.get("spec") or "").strip() or "帮我写一份工作周报"
    key = str(params.get("doc_type") or "").strip()
    topic = ""
    if key not in DOC_SPECS:
        key, topic = detect(spec)
    if not topic:
        topic = topic_of(spec, key)
    content = render(key, topic, "技能包 · 本地结构化模板")
    return {
        "ok": True, "doc_type": key, "topic": topic,
        "title": f"{topic} · 文档", "chars": len(content),
        "lines": len(content.splitlines()), "source_zh": "技能包结构化生成",
        "content": content,
    }
