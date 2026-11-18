"""技能包：HEX / RGB / HSV / 亮度换算。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["color_convert"](params)
