-- ============================================================
-- SQLite 建表语句（PostgreSQL 版本见 schema_postgres.sql）
-- 说明：所有时间字段用 TEXT 存 ISO8601（带时区），便于跨库迁移；
--       JSON 字段用 TEXT 存序列化结果，读写由 repository 统一收口。
-- ============================================================

-- 会话：一次对话/任务容器，step_index 为该 session 的全局单调步号
CREATE TABLE IF NOT EXISTS sessions (
  session_id   TEXT PRIMARY KEY,
  mode         TEXT    NOT NULL,              -- 本轮动作：chat | office | task
  channel      TEXT    NOT NULL DEFAULT 'chat', -- 沙箱通道：chat | work（创建后不可变）
  owner        TEXT    NOT NULL DEFAULT 'default', -- 归属/租户：会话级隔离，创建后不可变
  user_phone   TEXT,                              -- 用户手机号（排查检索键，可空）
  user_account TEXT,                              -- 用户账号（排查检索键，可空）
  consent      INTEGER NOT NULL DEFAULT 0,        -- 是否同意聊天内容用于升级迭代分析
  status       TEXT    NOT NULL,              -- running | completed | interrupted | failed
  step_index   INTEGER NOT NULL DEFAULT 0,    -- 已分配的最大步号（断点定位用）
  intent       TEXT,
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
);

-- 任务计划：总控拆解的原始结构化产物（恢复时原样复用，不重新规划）
CREATE TABLE IF NOT EXISTS plans (
  plan_id      TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  intent       TEXT NOT NULL,
  payload      TEXT NOT NULL,                 -- TaskPlan JSON
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id);

-- 子任务：状态机 pending|running|done|failed|skipped|rejected
CREATE TABLE IF NOT EXISTS tasks (
  session_id   TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  plan_id      TEXT NOT NULL,
  title        TEXT NOT NULL,
  assignee     TEXT NOT NULL,
  status       TEXT NOT NULL,
  depends_on   TEXT NOT NULL DEFAULT '[]',
  acceptance   TEXT NOT NULL DEFAULT '[]',
  payload      TEXT NOT NULL DEFAULT '{}',    -- SubTask JSON（含 skill/tool/params）
  result       TEXT,                          -- 结果 JSON
  error        TEXT,                          -- {error_code, message}
  attempts     INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (session_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, status);

-- 作业：管理者二次派发的原子执行单元，job_id 确定性生成 → 断点续跑可复用
CREATE TABLE IF NOT EXISTS jobs (
  session_id   TEXT NOT NULL,
  job_id       TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  agent_id     TEXT NOT NULL,
  action       TEXT NOT NULL,                 -- tool | skill
  target       TEXT NOT NULL,
  status       TEXT NOT NULL,                 -- pending | done | failed | interrupted
  attempts     INTEGER NOT NULL DEFAULT 0,
  params       TEXT NOT NULL DEFAULT '{}',
  output       TEXT,
  error        TEXT,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (session_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id);
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id, status);

-- 上下文快照：每一步必须落一条（含输入/输出/状态/时间戳）
CREATE TABLE IF NOT EXISTS snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT    NOT NULL,
  step_index   INTEGER NOT NULL,
  agent_id     TEXT    NOT NULL,
  phase        TEXT    NOT NULL,
  status       TEXT    NOT NULL,              -- ok | failed | interrupted
  input        TEXT,
  output       TEXT,
  error        TEXT,
  created_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshots_step ON snapshots(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id, id);

-- 快照滚动归档：超过 keep_last 的最老快照搬到这里
CREATE TABLE IF NOT EXISTS snapshots_archive (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- 结构化运行日志：session_id / agent_id / step_index 三件套缺一不可（写入层强制校验）
CREATE TABLE IF NOT EXISTS logs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT    NOT NULL,
  agent_id       TEXT    NOT NULL,
  step_index     INTEGER NOT NULL,
  level          TEXT    NOT NULL,            -- DEBUG | INFO | WARNING | ERROR
  module         TEXT    NOT NULL,
  message        TEXT    NOT NULL,
  error_code     TEXT,                        -- 错误码，用于错误归类
  stack          TEXT,
  context_digest TEXT,                        -- 上下文摘要（脱敏+截断）
  created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_session_step ON logs(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(session_id, level, id);

-- 任务流转记录：from_agent / to_agent / 原因，构成可追溯流转图
CREATE TABLE IF NOT EXISTS handoffs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT    NOT NULL,
  step_index  INTEGER NOT NULL,
  task_id     TEXT,
  from_agent  TEXT    NOT NULL,
  to_agent    TEXT    NOT NULL,
  reason      TEXT    NOT NULL,
  verdict     TEXT,                           -- pass | bounce | escalate | dispatch | return
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_session ON handoffs(session_id, id);

-- 工具调用审计：每次 MCP 调用都留痕（入参只存摘要，避免落敏感原文）
CREATE TABLE IF NOT EXISTS tool_audit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT    NOT NULL,
  agent_id     TEXT    NOT NULL,
  step_index   INTEGER NOT NULL,
  tool         TEXT    NOT NULL,
  permission   TEXT    NOT NULL,
  args_digest  TEXT,
  status       TEXT    NOT NULL,              -- ok | error | rejected_by_human | denied
  error_code   TEXT,
  duration_ms  INTEGER,
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON tool_audit(session_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_audit(tool, status);

-- 知识库：文档与切块（embedding 以 JSON 数组存 TEXT，迁移 PG 时可换 vector 类型）
CREATE TABLE IF NOT EXISTS documents (
  doc_id     TEXT PRIMARY KEY,
  source     TEXT NOT NULL,                   -- 来源文件路径 / URL
  title      TEXT,
  chunks     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_source ON documents(source);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      TEXT    NOT NULL,
  chunk_index INTEGER NOT NULL,
  content     TEXT    NOT NULL,
  embedding   TEXT    NOT NULL,               -- JSON: [float, ...]
  source      TEXT    NOT NULL,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON knowledge_chunks(doc_id, chunk_index);

-- 统一对话历史：chat / task / office / skill 四种模式共用一张表，
-- 跨模式追问不再断档（旧版只从 chat.respond 快照还原，办事轮次会丢）。
CREATE TABLE IF NOT EXISTS chat_turns (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT    NOT NULL,
  turn_index  INTEGER NOT NULL,
  role        TEXT    NOT NULL,                 -- user | assistant
  content     TEXT    NOT NULL,
  mode        TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON chat_turns(session_id, turn_index);

-- ============================================================
-- 第二阶段：Agent Platform 管理后台 专用表
-- 设计要点：管理后台与平台「共享同一个库」，不做数据复制/同步，
--          因此前端每写一步，后台查的就是同一行，物理上不存在两份数据。
-- ============================================================

-- 实时事件流：平台每执行一步即写一条；后台 SSE 以自增 id 为游标轮询（跨进程可靠）。
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT,
  channel      TEXT,
  kind         TEXT    NOT NULL,              -- session.created | task.step | task.done | incident.raised | artifact.created ...
  actor        TEXT,                          -- supervisor / executor / api / system
  status       TEXT,
  summary      TEXT,
  payload      TEXT,                          -- JSON 摘要（已脱敏）
  duration_ms  INTEGER,                       -- 真实耗时（仅 chat.done 等有测量的事件写入）
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);

-- 错误事故：用户反馈的「错误代码」就是这里的 code，后台凭码反查全链路证据。
CREATE TABLE IF NOT EXISTS incidents (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  code            TEXT    NOT NULL UNIQUE,     -- 可反馈错误代码，如 AP-20261115-K7M2QX
  session_id      TEXT,
  step_index      INTEGER,
  error_class     TEXT    NOT NULL,           -- 内部归类：TOOL_ERROR / TIMEOUT / CHANNEL_ISOLATION ...
  level           TEXT    NOT NULL DEFAULT 'ERROR',
  message         TEXT,
  stack           TEXT,
  context_digest  TEXT,
  source          TEXT,                       -- api.chat / workflow / tool / ...
  created_at      TEXT    NOT NULL,
  status          TEXT    NOT NULL DEFAULT 'open',  -- open | investigating | resolved
  resolution      TEXT,
  resolved_by     TEXT,
  resolved_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_incidents_code ON incidents(code);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON incidents(session_id, id);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);

-- 后台审计：登录、处置、配置变更均留痕（append-only）。
CREATE TABLE IF NOT EXISTS admin_audit (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
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
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
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
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
