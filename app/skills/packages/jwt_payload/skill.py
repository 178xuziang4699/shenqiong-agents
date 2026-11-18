"""技能包：解码 JWT payload（不验签，仅用于排查）。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["jwt_payload"](params)
