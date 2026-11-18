"""技能包：按纬度与日期估算昼长（纯天文公式）。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["geo_solar"](params)
