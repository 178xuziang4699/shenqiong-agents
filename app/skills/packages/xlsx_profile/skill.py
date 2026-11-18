"""技能包：读取 Excel 工作表结构与预览。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["xlsx_profile"](params)
