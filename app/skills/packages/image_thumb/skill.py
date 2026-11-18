"""技能包：生成缩略图并落盘到 var/capability_probe。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["image_thumb"](params)
