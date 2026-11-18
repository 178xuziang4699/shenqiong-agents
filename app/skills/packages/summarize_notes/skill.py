"""技能实现：笔记摘要抽取。

纯规则实现（无需外部模型、可离线复现）：
1. 按中英文句末标点切句；
2. 过滤噪声（空行、纯符号、过短片段）；
3. 按"信息密度"打分：长度适中 + 含关键实体词 + 含数字/结论词加分；
4. 取前 N 句，按原文顺序输出，保证摘要可读性。
"""

from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])|(?<=[.!?;])\s+")
_NOISE = re.compile(r"^[\s\-=_*#·•\d\.、，,]+$")
_ENTITY_HINTS = ("要求", "必须", "需要", "结论", "建议", "原因", "流程", "规则", "指标", "预算", "风险")
_NUM = re.compile(r"\d")


def _clean(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip(" \t-*#•·")


def _score(sentence: str) -> float:
    score = 0.0
    length = len(sentence)
    if 12 <= length <= 80:
        score += 1.0
    elif length < 12:
        score -= 0.5
    else:
        score += 0.4
    score += 0.35 * sum(1 for h in _ENTITY_HINTS if h in sentence)
    if _NUM.search(sentence):
        score += 0.3
    if "：" in sentence or ":" in sentence:
        score += 0.15
    return score


def run(params: dict, ctx: dict | None = None) -> dict:
    text = str(params.get("text") or "")
    max_points = int(params.get("max_points") or 5)
    if not text.strip():
        raise ValueError("输入文本为空，无法抽取摘要")

    candidates: list[tuple[int, float, str]] = []
    for idx, raw in enumerate(_SENT_SPLIT.split(text)):
        sentence = _clean(raw or "")
        if len(sentence) < 6 or _NOISE.match(sentence):
            continue
        candidates.append((idx, _score(sentence), sentence))

    if not candidates:
        raise ValueError("未从输入中抽取出有效句子（可能全是噪声）")

    top = sorted(candidates, key=lambda x: (-x[1], x[0]))[:max_points]
    picked = [s for _, _, s in sorted(top, key=lambda x: x[0])]
    summary_text = "；".join(picked)
    return {
        "ok": True,
        "summary": picked,
        "summary_len": len(summary_text),
        "summary_text": summary_text,
        "points": len(picked),
        "source_chars": len(text),
    }
