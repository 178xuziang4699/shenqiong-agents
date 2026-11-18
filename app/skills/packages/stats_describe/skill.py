"""技能包：描述性统计：均值/中位数/标准差。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["stats_describe"](params)
