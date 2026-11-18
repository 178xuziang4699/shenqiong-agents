"""技能包：读取图片格式、尺寸与色彩模式。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["image_info"](params)
