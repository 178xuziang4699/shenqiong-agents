"""技能包：仅列出允许的环境变量名（不暴露取值）。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["env_whitelist"](params)
