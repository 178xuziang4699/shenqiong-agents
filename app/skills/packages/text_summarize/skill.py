"""技能包：按句子信息密度抽取要点摘要。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["text_summarize"](params)
