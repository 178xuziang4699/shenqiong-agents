"""数据库连接层：SQLite 起步，PostgreSQL 通过同一接口迁移。

设计取舍：
- 只做一层薄封装（连接、占位符方言转换、迁移、事务），不引入 ORM。
  理由：单机场景 ORM 带来的抽象成本高于收益，SQL 直写更好排查。
- 所有访问串行化（RLock），因为并发上限 5 且写多读少，SQLite 下这样最稳。
  迁移 PG 后如需真并发，把 lock 改为连接池即可，不动上层代码。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

_MIGRATION_DIR = Path(__file__).resolve().parent


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.lock = threading.RLock()
        if dsn.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self._path = Path(dsn[len("sqlite:///"):])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        elif dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            self.dialect = "postgresql"
            try:
                import psycopg  # type: ignore
                from psycopg.rows import dict_row  # type: ignore
            except ImportError as exc:  # 明确报错，不留 TODO
                raise RuntimeError(
                    "连接 PostgreSQL 需要 psycopg：pip install 'psycopg[binary]'"
                ) from exc
            self._conn = psycopg.connect(dsn, row_factory=dict_row)
        else:
            raise ValueError(f"不支持的 DSN: {dsn}")

    # ---------- SQL 方言 ----------
    def q(self, sql: str) -> str:
        """占位符适配：内部统一用 ? 书写，PG 下转 %s。"""
        if self.dialect == "postgresql":
            return sql.replace("?", "%s")
        return sql

    # ---------- 执行 ----------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.lock:
            cur = self._conn.cursor()
            cur.execute(self.q(sql), tuple(params))
            self._conn.commit()
            return cur.rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self.lock:
            cur = self._conn.cursor()
            cur.execute(self.q(sql), tuple(params))
            rows = cur.fetchall()
            if not rows:
                return []
            if isinstance(rows[0], sqlite3.Row):
                return [dict(r) for r in rows]
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self.lock:
            cur = self._conn.cursor()
            cur.executemany(self.q(sql), [tuple(p) for p in seq])
            self._conn.commit()

    # ---------- 迁移 ----------
    def migrate(self) -> None:
        schema = "schema_postgres.sql" if self.dialect == "postgresql" else "schema_sqlite.sql"
        ddl = (_MIGRATION_DIR / schema).read_text(encoding="utf-8")
        with self.lock:
            cur = self._conn.cursor()
            if self.dialect == "sqlite":
                cur.executescript(ddl)
            else:
                # PG 不支持一次多语句，按分号切分逐条执行
                for stmt in _split_sql(ddl):
                    cur.execute(stmt)
            self._conn.commit()
        self._ensure_channel_column()

    def _ensure_channel_column(self) -> None:
        """幂等补列：老库建表时没有 channel，CREATE TABLE IF NOT EXISTS 不会补列。

        channel 是会话级沙箱标识，创建后永不改写；这里只做结构补齐，
        不触碰任何已有会话的业务数据。
        """
        with self.lock:
            cur = self._conn.cursor()
            if self.dialect == "sqlite":
                cols = {row[1] for row in cur.execute("PRAGMA table_info(sessions)").fetchall()}
                if "channel" not in cols:
                    cur.execute(
                        "ALTER TABLE sessions ADD COLUMN channel TEXT NOT NULL DEFAULT 'chat'")
            else:
                cur.execute(
                    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
                    "channel TEXT NOT NULL DEFAULT 'chat'")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_channel "
                "ON sessions(channel, updated_at DESC)")
            # 第二阶段：幂等补归属列与索引（老库 CREATE TABLE IF NOT EXISTS 不会补列）
            if self.dialect == "sqlite":
                cols = {row[1] for row in cur.execute("PRAGMA table_info(sessions)").fetchall()}
                if "owner" not in cols:
                    cur.execute(
                        "ALTER TABLE sessions ADD COLUMN owner TEXT NOT NULL DEFAULT 'default'")
            else:
                cur.execute(
                    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
                    "owner TEXT NOT NULL DEFAULT 'default'")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_owner "
                "ON sessions(owner, updated_at DESC)")
            # 幂等补 events.duration_ms（老库建表时无此列）
            if self.dialect == "sqlite":
                ecols = {row[1] for row in cur.execute("PRAGMA table_info(events)").fetchall()}
                if ecols and "duration_ms" not in ecols:
                    cur.execute("ALTER TABLE events ADD COLUMN duration_ms INTEGER")
            else:
                cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS duration_ms INTEGER")
            # 第三阶段：幂等补用户身份 / 同意列（老库 CREATE TABLE IF NOT EXISTS 不会补列）
            if self.dialect == "sqlite":
                scols = {row[1] for row in cur.execute("PRAGMA table_info(sessions)").fetchall()}
                for col, ddl in (("user_phone", "TEXT"), ("user_account", "TEXT"),
                                 ("consent", "INTEGER NOT NULL DEFAULT 0")):
                    if col not in scols:
                        cur.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}")
            else:
                cur.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_phone TEXT")
                cur.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_account TEXT")
                cur.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS consent INTEGER NOT NULL DEFAULT 0")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_phone ON sessions(user_phone)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(user_account)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_consent ON sessions(consent)")
            self._conn.commit()

    def close(self) -> None:
        with self.lock:
            self._conn.close()


def _split_sql(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt and stmt != ";":
                out.append(stmt.rstrip(";"))
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return [re.sub(r"\s+", " ", s).strip() for s in out if s.strip()]


_db: Database | None = None


def get_db(dsn: str | None = None) -> Database:
    global _db
    if _db is None:
        from app.config import get_settings
        _db = Database(dsn or get_settings().db_dsn)
        _db.migrate()
    return _db


def reset_db() -> None:
    """测试/演示用：释放单例，便于换库或删文件重建。"""
    global _db
    if _db is not None:
        _db.close()
    _db = None
