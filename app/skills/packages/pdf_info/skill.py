"""技能包：读取 PDF 页数、尺寸等元信息。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["pdf_info"](params)
