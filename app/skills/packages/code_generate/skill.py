"""技能包：生成完整可运行代码。

模型可用时由 LLM.generate_code 生成；不可用时降级为确定性模板，
并在返回值里如实标注来源，绝不把模板冒充成模型生成。
技能在主进程执行，因此可通过 runtime 单例拿到同一个 LLM 实例。
"""
from app.runtime import get_runtime


def run(params: dict, ctx: dict | None = None) -> dict:
    spec = str(params.get("spec") or "写一个 Python 工具函数并附单元测试")
    language = str(params.get("language") or "python")
    filename = str(params.get("filename") or "output/main.py")
    llm = get_runtime().llm
    code, source = llm.generate_code(spec, language, filename)
    lines = len(code.splitlines())
    return {
        "ok": True,
        "code": code,
        "language": language,
        "filename": filename,
        "lines": lines,
        "bytes": len(code.encode("utf-8")),
        "source": source,
        "source_zh": "模型生成" if source == "model" else "离线模板生成",
        "spec": spec[:200],
    }
