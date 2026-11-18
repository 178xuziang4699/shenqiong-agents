"""MCP stdio 服务骨架：newline 分帧的 JSON-RPC 2.0。

只实现最小必需方法：initialize / notifications/initialized / tools/list / tools/call / ping。
服务进程与主进程严格隔离（独立子进程），工具崩溃不会带走编排进程。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

Handler = Callable[[dict], dict]


def serve(server_name: str, version: str, tools: list[dict], handlers: dict[str, Handler]) -> None:
    protocol = "2024-11-05"

    def reply(msg_id: Any, result: Any) -> None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def reply_error(msg_id: Any, code: int, message: str) -> None:
        sys.stdout.write(
            json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}, ensure_ascii=False) + "\n"
        )
        sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            reply_error(None, -32700, f"parse error: {exc}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            reply(msg_id, {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": server_name, "version": version},
            })
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        elif method == "ping":
            reply(msg_id, {})
        elif method == "tools/list":
            reply(msg_id, {"tools": tools})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = handlers.get(name or "")
            if handler is None:
                reply(msg_id, {
                    "content": [{"type": "text", "text": f"未实现的工具: {name}"}],
                    "isError": True,
                })
                continue
            try:
                result = handler(args)
                reply(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
                    "isError": False,
                })
            except Exception as exc:  # noqa: BLE001 - 工具异常必须回传为 isError，不静默
                detail = f"{type(exc).__name__}: {exc}"
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()
                reply(msg_id, {"content": [{"type": "text", "text": detail}], "isError": True})
        else:
            reply_error(msg_id, -32601, f"method not found: {method}")
