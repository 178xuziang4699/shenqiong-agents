"""技能包：语义化版本比较。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["semver_compare"](params)
