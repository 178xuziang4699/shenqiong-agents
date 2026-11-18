"""检索召回：对 Agent 暴露统一接口，返回结果必须带来源引用。

接口契约（写进 docstring 就是契约，改签名要同步改调用方）：
  search(query, top_k) -> {
      "query", "hits": [{rank, score, source, chunk_index, citation, content}],
      "citations": ["samples/notes.md#chunk=2", ...]
  }
citation 格式：<source>#chunk=<index>，可直接定位回原始文档的切块。
"""

from __future__ import annotations

import json

from app.knowledge.embedder import cosine, embed


def _row_vector(raw: str) -> list[float]:
    try:
        data = json.loads(raw)
        return [float(x) for x in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def search(repo, query: str, top_k: int = 5, min_score: float = 0.02) -> dict:
    query = (query or "").strip()
    if not query:
        return {"query": query, "hits": [], "citations": [], "note": "空查询"}
    rows = repo.list_chunks()
    if not rows:
        return {"query": query, "hits": [], "citations": [], "note": "知识库为空，请先入库"}

    qvec = embed(query)
    scored = []
    for row in rows:
        score = cosine(qvec, _row_vector(row["embedding"]))
        # 关键词直击加权：短查询下向量容易稀释，字面命中必须给分
        if query in (row["content"] or ""):
            score += 0.25
        if score >= min_score:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])

    hits = []
    for rank, (score, row) in enumerate(scored[: max(1, top_k)], start=1):
        citation = f"{row['source']}#chunk={row['chunk_index']}"
        hits.append({
            "rank": rank,
            "score": round(score, 4),
            "source": row["source"],
            "chunk_index": row["chunk_index"],
            "citation": citation,
            "content": row["content"],
        })
    return {
        "query": query,
        "hits": hits,
        "citations": [h["citation"] for h in hits],
        "note": "" if hits else "未召回相关内容",
    }


def search_tool(args: dict, ctx: dict | None = None) -> dict:
    """注册表本地工具入口：kb.search"""
    from app.config import get_settings
    from app.db.database import get_db
    from app.db.repository import Repository

    settings = get_settings()
    repo = Repository(get_db(settings.db_dsn))
    return search(repo, args["query"], int(args.get("top_k") or 5))
