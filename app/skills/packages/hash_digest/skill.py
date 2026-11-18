"""技能包：计算文本摘要（sha256 / md5 等）。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["hash_digest"](params)
