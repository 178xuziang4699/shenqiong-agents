"""人工确认闸门：所有关键写操作（permission=write/dangerous）执行前必经此处。

默认 mode=prompt（真人在终端确认）。演示/自动化用 approve / reject / scripted。
EOF、空输入、异常一律视为"拒绝"——默认拒绝比默认放行安全。
"""

from __future__ import annotations

import json
import sys
import threading

from app.core.util import render  # noqa: F401  (保留给调用方做参数渲染扩展)


class Confirmer:
    def __init__(self, settings, mode: str | None = None, script: list[str] | None = None):
        cfg = settings.p("confirmation", default={}) or {}
        self.require_before_write = bool(cfg.get("require_before_write", True))
        self.write_permissions = set(cfg.get("write_permissions", ["write", "dangerous"]))
        self.mode = (mode or cfg.get("mode") or "prompt").strip().lower()
        raw_script = script
        if raw_script is None:
            raw = str(cfg.get("script") or "")
            raw_script = [s.strip() for s in raw.split(",") if s.strip()]
        self._script = list(raw_script)
        self._lock = threading.Lock()

    @property
    def is_interactive(self) -> bool:
        return self.mode == "prompt"

    def needs_confirmation(self, permission: str) -> bool:
        return self.require_before_write and permission in self.write_permissions

    def confirm(self, action: str, detail: dict | None = None) -> tuple[bool, str]:
        """返回 (是否放行, 结论说明)。"""
        payload = json.dumps(detail or {}, ensure_ascii=False, indent=2, default=str)
        print("\n" + "─" * 68)
        print("[人工确认] 即将执行关键写操作")
        print(f"  动作: {action}")
        if payload and payload != "{}":
            body = payload if len(payload) <= 1200 else payload[:1200] + "\n  …(已截断)"
            print("  参数: " + body.replace("\n", "\n        "))
        print("─" * 68)

        if self.mode == "approve":
            print("  结果: 已放行（confirmation.mode=approve，自动确认模式）")
            return True, "auto_approved"
        if self.mode == "reject":
            print("  结果: 已拒绝（confirmation.mode=reject）")
            return False, "auto_rejected"
        if self.mode == "scripted":
            with self._lock:
                decision = self._script.pop(0) if self._script else "reject"
            approved = decision.lower() in ("approve", "yes", "y", "ok")
            print(f"  结果: {'已放行' if approved else '已拒绝'}（scripted: {decision}）")
            return approved, f"scripted:{decision}"

        # prompt
        try:
            if not sys.stdin.isatty():
                print("  结果: 已拒绝（非交互环境无法确认，按默认拒绝处理）")
                return False, "denied_non_interactive"
            answer = input("  确认执行？输入 yes 放行，其他任意输入拒绝: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  结果: 已拒绝（输入流结束）")
            return False, "denied_eof"
        approved = answer in ("yes", "y")
        print(f"  结果: {'已放行' if approved else '已拒绝'}")
        return approved, "approved" if approved else "rejected"
