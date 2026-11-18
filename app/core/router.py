"""双模式路由：对话模式（不派发） / 办事模式（触发编排）。

判定规则全部来自 config/routing.yaml，改配置即可改行为：
每条规则对某个模式加权，得分高者胜出；分差不足 min_margin 时落到 default_mode。
换句话说，模糊情况下宁可当闲聊，也不误触发会写数据的工作流。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RouteDecision:
    mode: str
    scores: dict = field(default_factory=dict)
    matched_rules: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "matched_rules": self.matched_rules,
            "reason": self.reason,
        }


class ModeRouter:
    def __init__(self, settings):
        self.cfg = settings.routing or {}

    def _rule_matches(self, rule: dict, text: str) -> bool:
        match = rule.get("match") or {}
        kind = match.get("type", "any_regex")
        patterns = match.get("patterns") or []
        if kind in ("any_regex", "regex"):
            return any(self._safe_search(p, text) for p in patterns)
        if kind == "all_regex":
            return bool(patterns) and all(self._safe_search(p, text) for p in patterns)
        if kind == "contains":
            return any(str(p) in text for p in patterns)
        if kind == "length_gt":
            return len(text) > int(match.get("value", 0))
        if kind == "length_lt":
            return len(text) < int(match.get("value", 0))
        return False

    @staticmethod
    def _safe_search(pattern: str, text: str) -> bool:
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False

    def decide(self, text: str) -> RouteDecision:
        text = (text or "").strip()
        scores: dict[str, float] = {}
        matched: list[dict] = []

        max_chars = int(self.cfg.get("max_input_chars", 4000) or 4000)
        for rule in self.cfg.get("rules", []) or []:
            if self._rule_matches(rule, text):
                mode = rule.get("mode", "chat")
                scores[mode] = scores.get(mode, 0.0) + float(rule.get("weight", 0.0))
                matched.append({"name": rule.get("name"), "mode": mode, "weight": rule.get("weight")})

        default_mode = self.cfg.get("default_mode", "chat")
        min_margin = float(self.cfg.get("min_margin", 0.0))

        if len(text) > max_chars:
            return RouteDecision(
                mode="task", scores={**scores, "task": scores.get("task", 0.0) + 1.0},
                matched_rules=matched,
                reason=f"输入长度 {len(text)} 超过阈值 {max_chars}，按办事模式处理",
            )

        if not scores:
            return RouteDecision(mode=default_mode, scores=scores, matched_rules=matched,
                                 reason="未命中任何规则，使用 default_mode")

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top_mode, top_score = ranked[0]
        if len(ranked) > 1:
            second_mode, second_score = ranked[1]
            if abs(top_score - second_score) < min_margin:
                return RouteDecision(
                    mode=default_mode, scores=scores, matched_rules=matched,
                    reason=f"分差 {abs(top_score - second_score):.2f} < min_margin {min_margin}，保守落到 {default_mode}",
                )
            return RouteDecision(
                mode=top_mode, scores=scores, matched_rules=matched,
                reason=f"{top_mode}({top_score:.2f}) > {second_mode}({second_score:.2f})",
            )
        return RouteDecision(mode=top_mode, scores=scores, matched_rules=matched,
                             reason=f"仅命中 {top_mode}({top_score:.2f})")
