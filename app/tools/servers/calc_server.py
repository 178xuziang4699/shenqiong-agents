"""内置 MCP 服务：文本统计与安全算术。

独立进程存在，用来演示"工具是外部依赖"这一真实边界：
工具超时/崩溃只会让本次调用失败，不会让编排进程挂掉。
"""

from __future__ import annotations

import ast
import operator
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.tools.servers.base import serve  # noqa: E402

TOOLS = [
    {
        "name": "word_count", "description": "统计字数（CJK 按字计，英文按词计）",
        "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
    },
    {
        "name": "eval", "description": "四则运算求值",
        "inputSchema": {"type": "object", "required": ["expression"],
                        "properties": {"expression": {"type": "string"}}},
    },
    {
        "name": "sleep", "description": "睡眠指定秒数（用于超时/中断演示）",
        "inputSchema": {"type": "object", "required": ["seconds"],
                        "properties": {"seconds": {"type": "number"}}},
    },
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"


def _word_count(args: dict) -> dict:
    text = str(args.get("text") or "")
    cjk = sum(1 for ch in text if _is_cjk(ch))
    ascii_tokens = 0
    buf = ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "'-_"):
            buf += ch
        else:
            if buf:
                ascii_tokens += 1
                buf = ""
    if buf:
        ascii_tokens += 1
    return {
        "words": cjk + ascii_tokens,
        "cjk_chars": cjk,
        "ascii_words": ascii_tokens,
        "chars": len(text),
        "lines": text.count("\n") + 1 if text else 0,
    }


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"表达式不支持的语法: {type(node).__name__}")


def _eval(args: dict) -> dict:
    expr = str(args.get("expression") or "")
    value = _eval_node(ast.parse(expr, mode="eval"))
    return {"expression": expr, "value": value}


def _sleep(args: dict) -> dict:
    import time
    seconds = float(args.get("seconds") or 0)
    time.sleep(seconds)
    return {"slept": seconds}


HANDLERS = {"word_count": _word_count, "eval": _eval, "sleep": _sleep}


if __name__ == "__main__":
    serve("calc", "1.0.0", TOOLS, HANDLERS)
