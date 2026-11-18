"""技能包：UTC / 本地时间 / 时间戳 / ISO 周。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["datetime_tool"](params)
