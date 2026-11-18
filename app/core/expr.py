"""安全表达式求值：用于条件分支（condition）与验收项（checks）。

不引入 eval 的自由度：AST 白名单 + 空 builtins + 点号取值包装。
"""

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
    ast.Compare, ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.In, ast.NotIn,
    ast.Name, ast.Load, ast.Constant, ast.Attribute, ast.Subscript, ast.List, ast.Tuple,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.IfExp, ast.Call, ast.keyword,
)
_ALLOWED_CALLS = {"len", "abs", "int", "str", "float", "bool", "min", "max"}


class Dot(dict):
    """允许 `T1.summary_len` 这种点号访问；缺失键返回 None 而不是 KeyError。"""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return wrap(self.get(name))

    def __getitem__(self, key: Any) -> Any:
        return wrap(super().__getitem__(key))


def wrap(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, Dot):
        return Dot(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value


class _Guard(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"表达式含不允许的语法: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
            raise ValueError("表达式只允许使用白名单函数")
        super().generic_visit(node)


def eval_expr(expr: str, namespace: dict) -> Any:
    tree = ast.parse(expr, mode="eval")
    _Guard().visit(tree)
    env = {"__builtins__": {}, **{k: wrap(v) for k, v in namespace.items()}}
    return eval(compile(tree, "<expr>", "eval"), env)  # noqa: S307 - 已白名单校验


def eval_bool(expr: str, namespace: dict, *, default: bool = False) -> tuple[bool, str | None]:
    """返回 (结果, 错误说明)。任何异常都降级为 default，不抛给上层。"""
    try:
        return bool(eval_expr(expr, namespace)), None
    except Exception as exc:  # noqa: BLE001 - 条件表达式失败必须降级而非中断
        return default, f"{type(exc).__name__}: {exc}"


_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def resolve_path(obj: Any, path: str) -> Any:
    cur = obj
    if not path:
        return cur
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            return None
    return cur


def eval_check(result: dict, check: dict) -> tuple[bool, str]:
    """执行一条机器验收项：{"path": "output.words", "op": ">=", "value": 50}"""
    path = check.get("path", "")
    op = check.get("op", ">=")
    expected = check.get("value")
    actual = resolve_path(result, path)
    if op == "exists":
        ok = actual is not None
        return ok, f"{path} 存在={ok}"
    if op == "not_empty":
        if actual is None:
            return False, f"{path} 缺失"
        ok = len(actual) > 0 if hasattr(actual, "__len__") else bool(actual)
        return ok, f"{path} 非空={ok}（实际 {str(actual)[:60]}）"
    fn = _OPS.get(op)
    if fn is None:
        return False, f"未知比较符 {op}"
    if actual is None:
        return False, f"验收项 {path} 缺失（实际值 None）"
    try:
        ok = fn(actual, expected)
    except TypeError as exc:
        return False, f"验收项 {path} 类型不可比较: {exc}"
    return bool(ok), f"{path}={actual!r} {op} {expected!r} → {'通过' if ok else '不通过'}"
