"""向量化：本地哈希向量（hashing trick），零依赖、确定性、可离线复现。

为什么不用第三方 embedding 模型：
- 单机离线场景不应强依赖模型下载或外部 API；
- 检索质量对"文档内定位原文"这个用途足够（配合关键词加权）；
- 接口保持 embed(text) -> list[float]，换真模型只需替换本文件实现，
  向量维度写入 knowledge_chunks 前不需要迁移（维度变化时要重建索引，README 已说明）。
"""

from __future__ import annotations

import hashlib
import math
import re

DIM = 256

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    # 英文/数字：整词 + 词干（前 4 字符）
    for word in _ASCII_WORD.findall(text):
        tokens.append(word)
        if len(word) > 4:
            tokens.append(word[:4])
    # 中文：单字 + 相邻双字（近似 bigram，兼顾召回与噪声）
    chars = _CJK.findall(text)
    tokens.extend(chars)
    tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return tokens


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % DIM
    sign = 1.0 if (value >> 63) & 1 == 0 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    tokens = _tokens(text or "")
    if not tokens:
        return vec
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    for token, count in counts.items():
        index, sign = _bucket(token)
        weight = 1.0 + math.log(count)
        vec[index] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))
