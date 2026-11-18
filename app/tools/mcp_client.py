"""MCP 客户端：stdio 传输，JSON-RPC 2.0（newline 分帧）。

为什么自己实现而不用官方 SDK：
- 需求范围只需要 initialize / tools/list / tools/call 三个方法；
- 单机部署希望零额外依赖、零版本耦合；
- 200 行可控代码 < 一层看不见的传输封装（可维护性优先）。
若后续需要 resources/prompts/采样等能力，把 MCPClient 换成官方 SDK 的实现即可，
上层只依赖 request()/list_tools()/call_tool() 三个方法。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from app.errors import ToolCallError, ToolTimeout

PROTOCOL_VERSION = "2024-11-05"


class MCPClient:
    def __init__(
        self, name: str, command: list[str], cwd: str | Path = ".", env: dict | None = None,
        startup_timeout: float = 15.0,
    ):
        self.name = name
        self.command = command
        self.cwd = str(cwd)
        self.env = env or {}
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self._inbox: "queue.Queue[dict | None]" = queue.Queue()
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._stderr_tail: list[str] = []
        self._stderr_lock = threading.Lock()
        self.tools: list[dict] = []
        self.server_info: dict = {}

    # ---------------- 生命周期 ----------------
    def start(self) -> dict:
        import os
        env = dict(os.environ)
        env.update({k: str(v) for k, v in self.env.items()})
        self.proc = subprocess.Popen(
            self.command, cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_stdout, name=f"mcp-{self.name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"mcp-{self.name}-err", daemon=True).start()

        result = self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "agent-platform", "version": "1.0.0"}},
            timeout=self.startup_timeout,
        )
        self.server_info = result or {}
        self.notify("notifications/initialized", {})
        listed = self.request("tools/list", {}, timeout=self.startup_timeout) or {}
        self.tools = listed.get("tools", []) or []
        return self.server_info

    def close(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - 关闭失败不影响主流程，但要确保进程不残留
            self.proc.kill()

    # ---------------- 读线程 ----------------
    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._inbox.put(json.loads(line))
            except json.JSONDecodeError:
                self._inbox.put({"jsonrpc": "2.0", "error": {"code": -32700, "message": f"非法JSON: {line[:200]}"}})
        self._inbox.put(None)  # EOF

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            with self._stderr_lock:
                self._stderr_tail.append(line.rstrip())
                if len(self._stderr_tail) > 50:
                    self._stderr_tail.pop(0)

    @property
    def stderr_tail(self) -> str:
        with self._stderr_lock:
            return "\n".join(self._stderr_tail[-10:])

    # ---------------- 协议 ----------------
    def _write(self, payload: dict) -> None:
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            raise ToolCallError(f"MCP 服务 {self.name} 未运行", error_code="TOOL_ERROR")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = 30.0) -> Any:
        with self._id_lock:
            self._next_id += 1
            req_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            try:
                msg = self._inbox.get(timeout=timeout)
            except queue.Empty:
                raise ToolTimeout(
                    f"MCP 服务 {self.name} 方法 {method} 超时 {timeout}s（stderr: {self.stderr_tail[-300:]}）"
                ) from None
            if msg is None:
                raise ToolCallError(
                    f"MCP 服务 {self.name} 已退出（stderr: {self.stderr_tail[-500:] or '无输出'}）",
                    error_code="TOOL_ERROR",
                )
            if msg.get("id") != req_id:
                continue  # 通知或过期响应，丢弃
            if "error" in msg:
                err = msg["error"] or {}
                raise ToolCallError(
                    f"{self.name}.{method} 返回错误 [{err.get('code')}] {err.get('message')}",
                    error_code="TOOL_ERROR", detail={"mcp_error": err},
                )
            return msg.get("result")

    def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout) or {}
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
            raise ToolCallError(
                f"工具 {self.name}.{name} 执行失败: {' '.join(texts)[:400]}",
                error_code="TOOL_ERROR", detail={"content": texts},
            )
        # 约定：服务端把结构化结果 JSON 放进第一个 text block
        payload: dict = {"content": result.get("content", [])}
        for block in result.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    parsed = json.loads(block.get("text", ""))
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    payload.update(parsed)
                    break
        return payload
