"""技能包：计算目录下文件的聚合校验和。实现体集中在 app/skills/_ops.py，便于统一验证。"""

from app.skills._ops import OPS


def run(params: dict, ctx: dict | None = None) -> dict:
    return OPS["dir_checksum"](params)
