"""技能加载器：manifest 驱动、按触发条件匹配、运行时动态加载。

约束（对应需求第 12 条）：
- 技能包 = 目录 + manifest.yaml + 入口模块，可整体拷入/删除，不改主流程；
- 主流程只认 "skill 名字 + 入参"，不出现任何针对具体技能的 if/else；
- manifest 的 inputs 会作为契约，加载后先校验再执行。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from app.errors import SchemaValidationError, SkillNotFound
from app.tools.validate import validate

_SAFE_NAME = re.compile(r"^[a-z0-9_]+$")


@dataclass
class SkillPackage:
    name: str
    version: str
    description: str
    entry: str
    path: Path
    triggers: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    dependencies: dict = field(default_factory=dict)
    permissions: str = "read"
    timeout_seconds: float = 30.0
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version, "description": self.description,
            "entry": self.entry, "triggers": self.triggers, "inputs": self.inputs,
            "dependencies": self.dependencies, "permissions": self.permissions,
            "timeout_seconds": self.timeout_seconds,
        }


class SkillLoader:
    def __init__(self, settings, registry=None):
        self.settings = settings
        self.registry = registry
        self.packages_dir = settings.root / "app" / "skills" / "packages"
        self.packages: dict[str, SkillPackage] = {}
        self._modules: dict[str, Any] = {}
        self.warnings: list[str] = []

    # ---------- 发现 ----------
    def discover(self) -> dict[str, SkillPackage]:
        self.packages.clear()
        if not self.packages_dir.exists():
            return self.packages
        for manifest_path in sorted(self.packages_dir.glob("*/manifest.yaml")):
            try:
                data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                name = str(data.get("name") or manifest_path.parent.name)
                if not _SAFE_NAME.match(name):
                    self.warnings.append(f"技能名不合规已跳过: {name}")
                    continue
                entry = str(data.get("entry") or "skill.py:run")
                pkg = SkillPackage(
                    name=name,
                    version=str(data.get("version", "0.0.0")),
                    description=str(data.get("description", "")),
                    entry=entry,
                    path=manifest_path.parent,
                    triggers=data.get("triggers") or {},
                    inputs=data.get("inputs") or {"type": "object"},
                    dependencies=data.get("dependencies") or {},
                    permissions=str(data.get("permissions", "read")),
                    timeout_seconds=float(data.get("timeout_seconds") or 30),
                    manifest=data,
                )
                self.packages[name] = pkg
            except Exception as exc:  # noqa: BLE001 - 单个技能包坏掉不影响其他技能
                self.warnings.append(f"技能包解析失败 {manifest_path}: {exc}")
        return self.packages

    # ---------- 匹配 ----------
    def match(self, text: str, intent: str | None = None) -> list[str]:
        """按 manifest 的触发条件打分匹配，返回命中的技能名（高分在前）。"""
        if not self.packages:
            self.discover()
        text = text or ""
        scored: list[tuple[float, str]] = []
        for name, pkg in self.packages.items():
            triggers = pkg.triggers or {}
            score = 0.0
            intents = triggers.get("intents") or []
            if intent and intent in intents:
                score += 1.0
            hits = sum(1 for kw in (triggers.get("keywords") or []) if kw in text)
            score += 0.5 * hits
            for pattern in triggers.get("regex") or []:
                try:
                    if re.search(pattern, text):
                        score += 0.8
                except re.error:
                    self.warnings.append(f"技能 {name} 的触发正则非法: {pattern}")
            require_all = bool(triggers.get("require_all"))
            if score > 0 and (not require_all or hits >= len(triggers.get("keywords") or [1])):
                scored.append((score, name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [n for _, n in scored]

    # ---------- 加载与执行 ----------
    def load(self, name: str) -> Callable:
        pkg = self.packages.get(name)
        if pkg is None:
            self.discover()
            pkg = self.packages.get(name)
        if pkg is None:
            raise SkillNotFound(f"技能不存在: {name}")
        module = self._modules.get(name)
        if module is None:
            file_part, _, func_name = pkg.entry.partition(":")
            file_path = pkg.path / (file_part or "skill.py")
            if not file_path.exists():
                raise SkillNotFound(f"技能入口文件不存在: {file_path}")
            spec = importlib.util.spec_from_file_location(f"ap_skill_{name}", file_path)
            if spec is None or spec.loader is None:
                raise SkillNotFound(f"技能模块无法加载: {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"ap_skill_{name}"] = module
            spec.loader.exec_module(module)
            self._modules[name] = module
            func_name = func_name or "run"
            if not hasattr(module, func_name):
                raise SkillNotFound(f"技能 {name} 缺少入口函数 {func_name}")
        func_name = pkg.entry.partition(":")[2] or "run"
        return getattr(module, func_name)

    def run(self, name: str, params: dict, ctx: dict | None = None) -> dict:
        pkg = self.packages.get(name) or (self.discover() or {}).get(name)
        if pkg is None:
            raise SkillNotFound(f"技能不存在: {name}")
        errors = validate(params, pkg.inputs or {"type": "object"})
        if errors:
            raise SchemaValidationError(f"技能 {name} 入参不合规: {'; '.join(errors)}",
                                        detail={"errors": errors})
        # 依赖工具必须在注册表中存在（技能不自带工具直连能力）
        from app.core.faults import injector
        injector().check_skill(name)
        func = self.load(name)
        result = func(params, ctx or {})
        if not isinstance(result, dict):
            raise SchemaValidationError(f"技能 {name} 必须返回 dict，实际 {type(result).__name__}")
        result.setdefault("ok", True)
        result.setdefault("skill", name)
        return result

    def catalogue(self) -> list[dict]:
        if not self.packages:
            self.discover()
        return [pkg.to_dict() for pkg in sorted(self.packages.values(), key=lambda p: p.name)]
