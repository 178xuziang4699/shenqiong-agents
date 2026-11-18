"""技能包：生成两段文本的 unified diff 与相似度。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["text_diff"](params)
