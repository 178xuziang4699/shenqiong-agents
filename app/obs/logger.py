"""结构化日志：所有日志必须带 session_id / agent_id / step_index 三件套。

- 三件套缺失 → 抛 LogContractError（视为不合格日志，直接失败，不允许写半条）。
- 脱敏：环境变量里的密钥值、sk- 形式的字面量、key=value 形式的凭据都会被替换。
- 上下文摘要：截断 + 哈希，既可用于排查，又不落用户敏感原文。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import traceback
from pathlib import Path
from typing import Any

from app.db.repository import Repository
from app.models import now_iso

_SECRET_LITERAL = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_KV_SECRET = re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|authorization)\b\s*[:=]\s*\S+")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


class LogContractError(RuntimeError):
    """日志三件套缺失。"""


def _secret_values() -> list[str]:
    out = []
    for k, v in os.environ.items():
        if not v or len(v) < 8:
            continue
        if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            out.append(v)
    return out


def redact(text: str) -> str:
    if not text:
        return ""
    for secret in _secret_values():
        text = text.replace(secret, "***REDACTED***")
    text = _SECRET_LITERAL.sub("***REDACTED***", text)
    text = _KV_SECRET.sub(lambda m: f"{m.group(1)}=***REDACTED***", text)
    return text


def digest_context(payload: Any, max_chars: int = 240) -> str:
    """脱敏 + 截断 + 短哈希，兼顾可读与不可逆。"""
    try:
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        raw = str(payload)
    raw = redact(raw)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    clipped = raw[:max_chars].replace("\n", "\\n")
    if len(raw) > max_chars:
        clipped += f"…(len={len(raw)})"
    return f"{clipped} #sha256:{h}"


class StructuredLogger:
    def __init__(self, repo: Repository, settings, echo: bool = True):
        self.repo = repo
        self.settings = settings
        cfg = settings.p("logging", default={}) or {}
        self.min_level = _LEVELS.get(str(cfg.get("level", "INFO")).upper(), 20)
        self.max_context_chars = int(cfg.get("max_context_chars", 240))
        self.echo = echo
        log_file = settings.root / str(cfg.get("file", "logs/runtime.jsonl"))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self.path: Path = log_file
        self._lock = threading.RLock()

    # ---------- 核心写入 ----------
    def log(
        self,
        level: str,
        module: str,
        message: str,
        *,
        session_id: str | None,
        agent_id: str | None,
        step_index: int | None,
        error_code: str | None = None,
        stack: str | None = None,
        context: Any = None,
        exc: BaseException | None = None,
    ) -> None:
        level = level.upper()
        if not session_id or not agent_id or step_index is None:
            raise LogContractError(
                f"日志缺少三件套 session_id={session_id!r} agent_id={agent_id!r} step_index={step_index!r}"
                f" module={module!r}"
            )
        if _LEVELS.get(level, 20) < self.min_level:
            return
        if exc is not None and not stack:
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        digest = digest_context(context, self.max_context_chars) if context is not None else None
        record = {
            "ts": now_iso(),
            "level": level,
            "module": module,
            "session_id": session_id,
            "agent_id": agent_id,
            "step_index": int(step_index),
            "error_code": error_code,
            "message": redact(message),
            "context": digest,
        }
        with self._lock:
            self.repo.insert_log(
                session_id=session_id, agent_id=agent_id, step_index=int(step_index),
                level=level, module=module, message=redact(message),
                error_code=error_code, stack=stack, context_digest=digest,
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**record, "stack": stack}, ensure_ascii=False) + "\n")
            if self.echo:
                tail = f" | {digest}" if digest else ""
                code = f" [{error_code}]" if error_code else ""
                print(f"[{level:<7}] step={int(step_index):<4} {agent_id:<12} {module:<22} {record['message']}{code}{tail}")

    # ---------- 便捷方法 ----------
    def debug(self, module: str, message: str, **kw) -> None:
        self.log("DEBUG", module, message, **kw)

    def info(self, module: str, message: str, **kw) -> None:
        self.log("INFO", module, message, **kw)

    def warning(self, module: str, message: str, **kw) -> None:
        self.log("WARNING", module, message, **kw)

    def error(self, module: str, message: str, **kw) -> None:
        self.log("ERROR", module, message, **kw)
