"""技能包：去除常见 AI 套话，压缩冗余表述。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["text_humanize"](params)
