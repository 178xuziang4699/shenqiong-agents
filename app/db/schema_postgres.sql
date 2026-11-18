-- ============================================================
-- PostgreSQL 迁移版建表语句（SQLite 版见 schema_sqlite.sql）
-- 差异点：AUTOINCREMENT → SERIAL / BIGSERIAL；TEXT 不变；
--        如需向量检索，把 embedding 改为 vector(256) 并安装 pgvector 扩展：
--        CREATE EXTENSION IF NOT EXISTS vector;
--        并建立 HNSW 索引：CREATE INDEX ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
-- ============================================================

CREATE TABLE IF NOT EXISTS sessions (
  session_id   TEXT PRIMARY KEY,
  mode         TEXT    NOT NULL,
  channel      TEXT    NOT NULL DEFAULT 'chat',  -- 沙箱通道：chat | work（创建后不可变）
  owner        TEXT    NOT NULL DEFAULT 'default',  -- 归属/租户：会话级隔离，创建后不可变
  user_phone   TEXT,                               -- 用户手机号（排查检索键，可空）
  user_account TEXT,                               -- 用户账号（排查检索键，可空）
  consent      INTEGER NOT NULL DEFAULT 0,         -- 是否同意聊天内容用于升级迭代分析
  status       TEXT    NOT NULL,
  step_index   INTEGER NOT NULL DEFAULT 0,
  intent       TEXT,
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  plan_id      TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  intent       TEXT NOT NULL,
  payload      TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id);

CREATE TABLE IF NOT EXISTS tasks (
  session_id   TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  plan_id      TEXT NOT NULL,
  title        TEXT NOT NULL,
  assignee     TEXT NOT NULL,
  status       TEXT NOT NULL,
  depends_on   TEXT NOT NULL DEFAULT '[]',
  acceptance   TEXT NOT NULL DEFAULT '[]',
  payload      TEXT NOT NULL DEFAULT '{}',
  result       TEXT,
  error        TEXT,
  attempts     INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (session_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, status);

CREATE TABLE IF NOT EXISTS jobs (
  session_id   TEXT NOT NULL,
  job_id       TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  agent_id     TEXT NOT NULL,
  action       TEXT NOT NULL,
  target       TEXT NOT NULL,
  status       TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  params       TEXT NOT NULL DEFAULT '{}',
  output       TEXT,
  error        TEXT,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (session_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id);
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id, status);

CREATE TABLE IF NOT EXISTS snapshots (
  id           SERIAL PRIMARY KEY,
  session_id   TEXT    NOT NULL,
  step_index   INTEGER NOT NULL,
  agent_id     TEXT    NOT NULL,
  phase        TEXT    NOT NULL,
  status       TEXT    NOT NULL,
  input        TEXT,
  output       TEXT,
  error        TEXT,
  created_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshots_step ON snapshots(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id, id);

CREATE TABLE IF NOT EXISTS snapshots_archive (
  id           SERIAL PRIMARY KEY,
  session_id   TEXT    NOT NULL,
  step_index   INTEGER NOT NULL,
  agent_id     TEXT    NOT NULL,
  phase        TEXT    NOT NULL,
  status       TEXT    NOT NULL,
  input        TEXT,
  output       TEXT,
  error        TEXT,
  created_at   TEXT    NOT NULL,
  archived_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archive_session ON snapshots_archive(session_id, step_index);

CREATE TABLE IF NOT EXISTS logs (
  id             SERIAL PRIMARY KEY,
  session_id     TEXT    NOT NULL,
  agent_id       TEXT    NOT NULL,
  step_index     INTEGER NOT NULL,
  level          TEXT    NOT NULL,
  module         TEXT    NOT NULL,
  message        TEXT    NOT NULL,
  error_code     TEXT,
  stack          TEXT,
  context_digest TEXT,
  created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_session_step ON logs(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(session_id, level, id);

CREATE TABLE IF NOT EXISTS handoffs (
  id          SERIAL PRIMARY KEY,
  session_id  TEXT    NOT NULL,
  step_index  INTEGER NOT NULL,
  task_id     TEXT,
  from_agent  TEXT    NOT NULL,
  to_agent    TEXT    NOT NULL,
  reason      TEXT    NOT NULL,
  verdict     TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_session ON handoffs(session_id, id);

CREATE TABLE IF NOT EXISTS tool_audit (
  id           SERIAL PRIMARY KEY,
  session_id   TEXT    NOT NULL,
  agent_id     TEXT    NOT NULL,
  step_index   INTEGER NOT NULL,
  tool         TEXT    NOT NULL,
  permission   TEXT    NOT NULL,
  args_digest  TEXT,
  status       TEXT    NOT NULL,
  error_code   TEXT,
  duration_ms  INTEGER,
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON tool_audit(session_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_audit(tool, status);

CREATE TABLE IF NOT EXISTS documents (
  doc_id     TEXT PRIMARY KEY,
  source     TEXT NOT NULL,
  title      TEXT,
  chunks     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_source ON documents(source);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id          SERIAL PRIMARY KEY,
  doc_id      TEXT    NOT NULL,
  chunk_index INTEGER NOT NULL,
  content     TEXT    NOT NULL,
  embedding   TEXT    NOT NULL,
  source      TEXT    NOT NULL,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON knowledge_chunks(doc_id, chunk_index);

CREATE TABLE IF NOT EXISTS chat_turns (
  id          SERIAL PRIMARY KEY,
  session_id  TEXT    NOT NULL,
  turn_index  INTEGER NOT NULL,
  role        TEXT    NOT NULL,
  content     TEXT    NOT NULL,
  mode        TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON chat_turns(session_id, turn_index);

-- 第二阶段：后端管理系统专用表（与 SQLite 版结构一致）
CREATE TABLE IF NOT EXISTS events (
  id          BIGSERIAL PRIMARY KEY,
  session_id  TEXT,
  channel     TEXT,
  kind        TEXT    NOT NULL,
  actor       TEXT,
  status      TEXT,
  summary     TEXT,
  payload     TEXT,
  duration_ms INTEGER,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);

CREATE TABLE IF NOT EXISTS incidents (
  id              BIGSERIAL PRIMARY KEY,
  code            TEXT    NOT NULL UNIQUE,
  session_id      TEXT,
  step_index      INTEGER,
  error_class     TEXT    NOT NULL,
  level           TEXT    NOT NULL DEFAULT 'ERROR',
  message         TEXT,
  stack           TEXT,
  context_digest  TEXT,
  source          TEXT,
  created_at      TEXT    NOT NULL,
  status          TEXT    NOT NULL DEFAULT 'open',
  resolution      TEXT,
  resolved_by     TEXT,
  resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON incidents(session_id, id);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);

CREATE TABLE IF NOT EXISTS admin_audit (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT    NOT NULL,
  action      TEXT    NOT NULL,
  target      TEXT,
  detail      TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at DESC);


-- ============================================================
-- 第三阶段：智能分析 / 升级迭代 / 用户与同意 专用表
-- 合规要点：只有 consent=1 的会话才进入迭代分析；查询留痕 append-only
-- ============================================================

CREATE TABLE IF NOT EXISTS platform_users (
  id            BIGSERIAL PRIMARY KEY,
  user_key      TEXT NOT NULL UNIQUE,       -- 稳定用户标识（账号优先，否则手机号）
  account       TEXT,
  phone         TEXT,
  display_name  TEXT,
  consent       INTEGER NOT NULL DEFAULT 0,  -- 0 未授权 / 1 已授权（可撤回）
  consent_at    TEXT,
  consent_note  TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pu_phone ON platform_users(phone);
CREATE INDEX IF NOT EXISTS idx_pu_account ON platform_users(account);

CREATE TABLE IF NOT EXISTS insight_runs (
  id             BIGSERIAL PRIMARY KEY,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  window_minutes INTEGER NOT NULL DEFAULT 60,
  health_score   INTEGER,
  status         TEXT NOT NULL DEFAULT 'running',
  method         TEXT NOT NULL DEFAULT 'rule-engine',
  summary        TEXT,
  metrics        TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON insight_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS insights (
  id            BIGSERIAL PRIMARY KEY,
  run_id        INTEGER NOT NULL,
  category      TEXT NOT NULL,
  severity      TEXT NOT NULL,
  title         TEXT NOT NULL,
  detail        TEXT,
  evidence      TEXT,
  metric_value  REAL,
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_insights_run ON insights(run_id, id);
CREATE INDEX IF NOT EXISTS idx_insights_created ON insights(created_at DESC);

CREATE TABLE IF NOT EXISTS iteration_items (
  id             BIGSERIAL PRIMARY KEY,
  category       TEXT NOT NULL,   -- demand | failure | topic | praise
  title          TEXT NOT NULL,
  evidence       TEXT,            -- 脱敏后的证据片段
  source_session TEXT,
  source_turn    INTEGER,
  priority       TEXT NOT NULL DEFAULT 'P2',
  status         TEXT NOT NULL DEFAULT 'new', -- new | triaged | planned | done | rejected
  votes          INTEGER NOT NULL DEFAULT 1,
  note           TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_iteration_dedup ON iteration_items(category, title);
CREATE INDEX IF NOT EXISTS idx_iteration_status ON iteration_items(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS query_audit (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,      -- user.bind | user.search | chatlog.read | consent.change | error.lookup
  user_key    TEXT,
  phone_mask  TEXT,
  account     TEXT,
  session_id  TEXT,
  purpose     TEXT,
  revealed    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_query_audit_created ON query_audit(created_at DESC);
