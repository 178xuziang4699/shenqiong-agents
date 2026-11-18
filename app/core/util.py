"""通用工具：模板渲染、指数退避重试、超时执行、并行批处理。"""

from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable, Sequence

from app.errors import AgentInterrupt, classify

_TPL = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


# ==================== 模板渲染 ====================
def resolve_ref(path: str, ctx: dict, strict: bool = True) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                if not strict:
                    return ""   # 降级模式：上游结果缺失时不让下游整个崩掉
                raise KeyError(f"模板变量不存在: {path}")
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit():
            cur = cur[int(part)]
        else:
            if not strict:
                return ""
            raise KeyError(f"模板变量不可索引: {path}")
    return cur


def render(value: Any, ctx: dict, strict: bool = True) -> Any:
    """把 {"content": "{{T3.report}}"} 渲染成实际值。

    整体就是一个模板时保留原始类型（dict/list/数值），便于工具拿到结构化入参。
    strict=False 用于断点恢复的降级路径：模板变量缺失时填空串并继续，
    避免「上游被跳过 → 下游因引用不到结果而二次失败」。
    """
    if isinstance(value, str):
        whole = _TPL.fullmatch(value.strip())
        if whole:
            return resolve_ref(whole.group(1), ctx, strict)
        return _TPL.sub(lambda m: str(resolve_ref(m.group(1), ctx, strict)), value)
    if isinstance(value, list):
        return [render(v, ctx, strict) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx, strict) for k, v in value.items()}
    return value


# ==================== 重试 ====================
def retry_call(
    fn: Callable[[int], Any],
    *,
    attempts: int = 3,
    base_delay: float = 0.3,
    max_delay: float = 8.0,
    jitter: float = 0.15,
    retry_on: Sequence[str] = ("TIMEOUT", "TOOL_ERROR", "LLM_ERROR", "UNKNOWN"),
    on_retry: Callable[[BaseException, int, float], None] | None = None,
) -> Any:
    """指数退避重试。中断信号永不重试（否则断点语义就废了）。"""
    last_exc: BaseException | None = None
    total = max(1, attempts)
    for attempt in range(1, total + 1):
        try:
            return fn(attempt)
        except AgentInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            code = classify(exc)
            if code not in retry_on or attempt >= total:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 1 + random.uniform(-jitter, jitter)
            delay = max(0.0, delay)
            if on_retry:
                on_retry(exc, attempt, delay)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ==================== 超时 ====================
def with_timeout(fn: Callable[[], Any], seconds: float, label: str = "operation") -> Any:
    """单机场景下的软超时：到点向调用方抛 TimeoutError。

    注意：线程无法被强杀，超时后后台线程会自然结束，但其结果被丢弃。
    这是单机实现的已知取舍，已在 README 的"已知边界"中说明。
    """
    if seconds <= 0:
        return fn()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"timeout-{label}")
    future = pool.submit(fn)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError(f"{label} 超过 {seconds}s 未返回") from exc
    finally:
        pool.shutdown(wait=False)
