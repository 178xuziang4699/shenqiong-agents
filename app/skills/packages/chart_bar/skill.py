"""技能包：用 matplotlib 生成柱状图 PNG。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["chart_bar"](params)
