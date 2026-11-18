"""技能包：检索知识库并返回带来源引用的结果。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["kb_lookup"](params)
