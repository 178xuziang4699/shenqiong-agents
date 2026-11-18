"""文档入库：切分 → 向量化 → 落库。

切分策略：按段落聚合成 ~320 字窗口，窗口间重叠 60 字，保证跨段语义不被切断，
同时每个切块保留 source + chunk_index，供检索结果回引原文。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.knowledge.embedder import embed
from app.models import dumps, now_iso

CHUNK_SIZE = 320
CHUNK_OVERLAP = 60


def _safe_path(path: str, workspace: Path) -> Path:
    target = (workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if target != workspace and workspace not in target.parents:
        raise PermissionError(f"路径越界：{path} 不在工作区 {workspace} 内")
    return target


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if not buf:
            buf = para
        elif len(buf) + len(para) + 1 <= size:
            buf += "\n" + para
        else:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = (tail + "\n" + para).strip() if tail else para
    if buf.strip():
        chunks.append(buf.strip())
    # 单段超长时硬切
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= size * 2:
            final.append(chunk)
            continue
        for i in range(0, len(chunk), size):
            final.append(chunk[i:i + size + overlap])
    return [c for c in final if c.strip()]


def ingest_path(repo, path: str, workspace: Path, title: str | None = None) -> dict:
    target = _safe_path(path, workspace)
    if not target.is_file():
        raise FileNotFoundError(f"文档不存在: {path}")
    text = target.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError(f"文档 {path} 无可入库内容")

    source = str(target.relative_to(workspace)) if workspace in target.parents else str(target)
    doc_id = "doc_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    rows = [
        (idx, chunk, dumps(embed(chunk)), source)
        for idx, chunk in enumerate(chunks)
    ]
    repo.replace_chunks(doc_id, rows)
    repo.upsert_document(doc_id, source, title or target.stem, len(chunks))
    return {
        "ok": True,
        "doc_id": doc_id,
        "source": source,
        "title": title or target.stem,
        "chunks": len(chunks),
        "chars": len(text),
        "ingested_at": now_iso(),
    }


def ingest_tool(args: dict, ctx: dict | None = None) -> dict:
    """注册表本地工具入口：kb.ingest"""
    from app.config import get_settings
    from app.db.database import get_db
    from app.db.repository import Repository

    settings = get_settings()
    repo = Repository(get_db(settings.db_dsn))
    return ingest_path(repo, args["path"], settings.workspace, args.get("title"))
