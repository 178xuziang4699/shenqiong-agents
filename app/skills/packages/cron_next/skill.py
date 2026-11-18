"""技能包：推算 */N 分钟cron的下几次触发时刻。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["cron_next"](params)
