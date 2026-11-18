"""内置 MCP 服务：文件读写（受工作区沙箱限制，越界路径直接拒绝）。

属于"外部工具"边界，故走 MCP 子进程，而不是在业务代码里直接 open()。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.tools.servers.base import serve  # noqa: E402

WORKSPACE = Path(os.environ.get("AP_WORKSPACE") or os.getcwd()).resolve()

TOOLS = [
    {
        "name": "list_dir", "description": "列出目录下的文件",
        "inputSchema": {"type": "object", "required": ["path"],
                        "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}}},
    },
    {
        "name": "read_text", "description": "读取文本文件",
        "inputSchema": {"type": "object", "required": ["path"],
                        "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}},
    },
    {
        "name": "write_text", "description": "写入文本文件",
        "inputSchema": {"type": "object", "required": ["path", "content"],
                        "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                       "overwrite": {"type": "boolean"}}},
    },
    {
        "name": "stat", "description": "文件元信息",
        "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
    },
]


def _safe(path: str) -> Path:
    target = (WORKSPACE / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise PermissionError(f"路径越界：{path} 不在工作区 {WORKSPACE} 内")
    return target


def _list_dir(args: dict) -> dict:
    target = _safe(args["path"])
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {args['path']}")
    pattern = args.get("pattern") or "*"
    items = []
    for p in sorted(target.glob(pattern)):
        if p.is_file():
            items.append({"name": p.name, "size": p.stat().st_size, "path": str(p.relative_to(WORKSPACE))})
    return {"path": args["path"], "count": len(items), "items": items}


def _read_text(args: dict) -> dict:
    target = _safe(args["path"])
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {args['path']}")
    text = target.read_text(encoding="utf-8", errors="replace")
    limit = int(args.get("max_chars") or 200000)
    clipped = text[:limit]
    return {
        "path": str(target.relative_to(WORKSPACE)) if WORKSPACE in target.parents else str(target),
        "content": clipped,
        "chars": len(text),
        "truncated": len(text) > limit,
    }


def _write_text(args: dict) -> dict:
    target = _safe(args["path"])
    if target.exists() and args.get("overwrite") is False:
        raise FileExistsError(f"文件已存在且 overwrite=false: {args['path']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = str(args["content"])
    target.write_text(content, encoding="utf-8")
    return {
        "path": str(target.relative_to(WORKSPACE)) if WORKSPACE in target.parents else str(target),
        "bytes": len(content.encode("utf-8")),
        "chars": len(content),
        "created": True,
    }


def _stat(args: dict) -> dict:
    target = _safe(args["path"])
    if not target.exists():
        return {"path": args["path"], "exists": False}
    st = target.stat()
    return {"path": args["path"], "exists": True, "size": st.st_size, "is_file": target.is_file()}


HANDLERS = {"list_dir": _list_dir, "read_text": _read_text, "write_text": _write_text, "stat": _stat}


if __name__ == "__main__":
    serve("fs", "1.0.0", TOOLS, HANDLERS)
