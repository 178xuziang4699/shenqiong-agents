"""工具注册表：所有外部工具调用的唯一入口。

职责（缺一不可）：
1. 注册：工具名 / 入参 schema / 超时 / 权限级别，启动时与 MCP 服务 tools/list 对齐；
2. 校验：调用前按 schema 校验参数，不合规直接拒绝（不把脏参数送到外部）；
3. 确认：权限为 write/dangerous 时输出动作并等待人工确认；
4. 审计：无论成功失败都写 tool_audit（入参只存脱敏摘要）。
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.db.repository import Repository
from app.errors import (
    SchemaValidationError,
    ToolCallError,
    ToolNotRegistered,
    ToolPermissionDenied,
    ToolTimeout,
)
from app.obs.logger import StructuredLogger, digest_context
from app.obs.tracer import Tracer
from app.tools.mcp_client import MCPClient
from app.tools.validate import validate


@dataclass
class ToolContext:
    session_id: str
    agent_id: str
    task_id: str | None = None
    job_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    server: str
    remote_name: str
    permission: str
    timeout_seconds: float
    description: str
    input_schema: dict
    transport: str = "stdio"
    local_handler: str | None = None
    schema_source: str = "yaml"


class ToolRegistry:
    def __init__(self, settings, repo: Repository, tracer: Tracer, log: StructuredLogger,
                 confirmer):
        self.settings = settings
        self.repo = repo
        self.tracer = tracer
        self.log = log
        self.confirmer = confirmer
        self.tools: dict[str, ToolSpec] = {}
        self.clients: dict[str, MCPClient] = {}
        self.server_tools: dict[str, list[dict]] = {}
        self._local_cache: dict[str, Callable] = {}
        self._started = False
        self.warnings: list[str] = []

    # ==================== 启动 ====================
    def start(self) -> None:
        if self._started:
            return
        cfg = self.settings.tools or {}
        startup_timeout = float(self.settings.p("timeouts", "mcp_startup_seconds", default=15))
        servers = {s["name"]: s for s in cfg.get("servers", []) or []}

        for tool in cfg.get("tools", []) or []:
            transport = tool.get("transport") or ("local" if tool.get("local_handler") else "stdio")
            spec = ToolSpec(
                name=tool["name"],
                server=tool.get("server", "local"),
                remote_name=tool.get("remote_name") or tool["name"].split(".")[-1],
                permission=tool.get("permission", "read"),
                timeout_seconds=float(tool.get("timeout_seconds") or self.settings.tool_timeout),
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema") or {"type": "object"},
                transport=transport,
                local_handler=tool.get("local_handler"),
            )
            if transport == "stdio":
                server_cfg = servers.get(spec.server)
                if not server_cfg:
                    raise ToolCallError(f"工具 {spec.name} 指向不存在的 MCP 服务 {spec.server}",
                                        error_code="CONFIG_INVALID")
                client = self._client_for(spec.server, server_cfg, startup_timeout)
                exposed = {t["name"] for t in self.server_tools.get(spec.server, [])}
                if exposed and spec.remote_name not in exposed:
                    msg = f"工具 {spec.name} 在服务 {spec.server} 的 tools/list 中不存在，已跳过注册"
                    self.warnings.append(msg)
                    continue
                # 以服务端 schema 为准（服务端是事实来源），注册表 schema 作备份
                for t in self.server_tools.get(spec.server, []):
                    if t["name"] == spec.remote_name and t.get("inputSchema"):
                        spec.input_schema = t["inputSchema"]
                        spec.schema_source = "server"
                        break
            else:
                if not spec.local_handler:
                    raise ToolCallError(f"本地工具 {spec.name} 缺少 local_handler",
                                        error_code="CONFIG_INVALID")
            self.tools[spec.name] = spec

        # 服务暴露但未注册的工具：只告警，不自动放行（注册表是白名单）
        for server_name, tools in self.server_tools.items():
            registered = {s.remote_name for s in self.tools.values() if s.server == server_name}
            for t in tools:
                if t["name"] not in registered:
                    self.warnings.append(f"MCP 服务 {server_name} 的工具 {t['name']} 未注册，已忽略")
        self._started = True

    def _client_for(self, name: str, cfg: dict, timeout: float) -> MCPClient:
        if name in self.clients:
            return self.clients[name]
        client = MCPClient(
            name=name,
            command=[str(c) for c in cfg.get("command", [])],
            cwd=self.settings.root / str(cfg.get("cwd", ".")),
            env=cfg.get("env") or {},
            startup_timeout=timeout,
        )
        info = client.start()
        self.clients[name] = client
        self.server_tools[name] = client.tools
        self.warnings.append(
            f"MCP 服务 {name} 已连接：{info.get('serverInfo', {}).get('name')}"
            f" v{info.get('serverInfo', {}).get('version')}，暴露 {len(client.tools)} 个工具"
        )
        return client

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()
        self._started = False

    # ==================== 查询 ====================
    def describe(self, name: str) -> dict:
        spec = self.tools.get(name)
        if not spec:
            return {}
        return {
            "name": spec.name, "server": spec.server, "transport": spec.transport,
            "permission": spec.permission, "timeout_seconds": spec.timeout_seconds,
            "description": spec.description, "input_schema": spec.input_schema,
            "schema_source": spec.schema_source,
        }

    def catalogue(self) -> list[dict]:
        return [self.describe(n) for n in sorted(self.tools)]

    # ==================== 调用 ====================
    def call(self, name: str, args: dict, ctx: ToolContext) -> dict:
        spec = self.tools.get(name)
        if spec is None:
            self.log.error(
                "tool.registry", f"调用未注册工具 {name}（拒绝）", session_id=ctx.session_id,
                agent_id=ctx.agent_id, step_index=self.repo.current_step(ctx.session_id),
                error_code="SCHEMA_INVALID", context={"tool": name},
            )
            raise ToolNotRegistered(f"工具未注册: {name}")

        # 1) 参数校验
        errors = validate(args, spec.input_schema)
        if errors:
            self.repo.insert_audit(
                ctx.session_id, ctx.agent_id, self.repo.current_step(ctx.session_id), name,
                spec.permission, digest_context(args), "denied", "SCHEMA_INVALID", 0,
            )
            raise SchemaValidationError(f"工具 {name} 入参不合规: {'; '.join(errors)}",
                                        detail={"errors": errors})

        # 2) 关键写操作人工确认
        if self.confirmer.needs_confirmation(spec.permission):
            with self.tracer.step(
                ctx.session_id, ctx.agent_id, "confirm",
                {"tool": name, "permission": spec.permission, "args": args},
            ) as cst:
                approved, note = self.confirmer.confirm(
                    f"调用工具 {name}（权限级别 {spec.permission}）", args
                )
                cst.set_output({"approved": approved, "note": note})
            if not approved:
                self.repo.insert_audit(
                    ctx.session_id, ctx.agent_id, cst.step_index, name, spec.permission,
                    digest_context(args), "rejected_by_human", "HUMAN_REJECTED", 0,
                )
                raise ToolPermissionDenied(f"人工拒绝执行工具 {name}（{note}）",
                                           detail={"note": note})

        # 3) 执行 + 审计（成功/失败都落）
        started = time.time()
        with self.tracer.step(
            ctx.session_id, ctx.agent_id, f"tool:{name}", {"args": args}
        ) as st:
            try:
                result = self._execute(spec, args, ctx)
            except Exception as exc:  # noqa: BLE001 - 必须审计后再抛
                code = getattr(exc, "error_code", "TOOL_ERROR")
                self.repo.insert_audit(
                    ctx.session_id, ctx.agent_id, st.step_index, name, spec.permission,
                    digest_context(args), "error", code, int((time.time() - started) * 1000),
                )
                raise
            self.repo.insert_audit(
                ctx.session_id, ctx.agent_id, st.step_index, name, spec.permission,
                digest_context(args), "ok", None, int((time.time() - started) * 1000),
            )
            st.set_output(result)
            st.meta = {"tool": name, "permission": spec.permission}
        return result

    def _execute(self, spec: ToolSpec, args: dict, ctx: ToolContext) -> dict:
        if spec.transport == "local":
            handler = self._local(spec)
            payload = dict(args)
            payload.setdefault("_ctx", {
                "session_id": ctx.session_id, "agent_id": ctx.agent_id,
                "task_id": ctx.task_id, "job_id": ctx.job_id,
            })
            try:
                from app.core.util import with_timeout
                return with_timeout(lambda: handler(payload), spec.timeout_seconds, label=spec.name)
            except TimeoutError as exc:
                raise ToolTimeout(f"本地工具 {spec.name} 超时 {spec.timeout_seconds}s") from exc
            except (ToolCallError, ToolTimeout):
                raise
            except Exception as exc:  # noqa: BLE001
                raise ToolCallError(f"本地工具 {spec.name} 执行失败: {exc}") from exc

        client = self.clients.get(spec.server)
        if client is None:
            raise ToolCallError(f"MCP 服务 {spec.server} 未连接", error_code="TOOL_ERROR")
        return client.call_tool(spec.remote_name, args, timeout=spec.timeout_seconds)

    def _local(self, spec: ToolSpec) -> Callable:
        handler = self._local_cache.get(spec.name)
        if handler is not None:
            return handler
        module_name, _, func_name = str(spec.local_handler).partition(":")
        if not module_name or not func_name:
            raise ToolCallError(f"local_handler 写法应为 module:function，实际 {spec.local_handler}",
                                error_code="CONFIG_INVALID")
        module = importlib.import_module(module_name)
        handler = getattr(module, func_name)
        self._local_cache[spec.name] = handler
        return handler
