"""技能包：提取 PPT 每页文本大纲。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["pptx_outline"](params)
