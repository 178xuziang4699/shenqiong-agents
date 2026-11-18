"""技能包：把 cron 表达式翻译成自然语言。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["cron_explain"](params)
