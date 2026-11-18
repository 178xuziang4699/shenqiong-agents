# 架构说明（原始设计文档）

> 本文件是第一阶段的原始 README，保留其架构与实现细节，作为历史设计文档。
> 最新使用方法以根目录 `README.md` 为准。

---

# Agent Platform · 单机多智能体编排平台

一个**可运行、可观测、可恢复**的单机多智能体编排平台，附带一套独立部署的**安全运维管理后台**。
用 Python 3.11 + FastAPI + SQLite 写成，不依赖 Kubernetes、消息队列或外部服务即可跑起来。

> 设计取舍：宁可少一层抽象。没有 ORM、没有事件总线、没有插件框架，
> 只有一个组装根 `app/runtime.py`、五个协作角色、一套落盘表结构。

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| 多智能体编排 | Supervisor 总控，Planner / Manager / Executor / Handoff 等 5 类角色，支持两级与 N 级分派 |
| 工具与技能 | 63 个注册工具、65 个可运行时增删的技能包，MCP 本地服务器接入 |
| 对话与办公双通道 | `chat` / `work` 两套**显式沙箱**，通道只由用户选择，不由消息内容推断；跨通道访问返回 409 |
| 可观测 | 每步落上下文快照、结构化日志强制带 `session_id / agent_id / step_index`、工具调用审计 |
| 可恢复 | 一键诊断定位首个失败点，从断点续跑并复用已完成步骤 |
| 错误代码核查 | 异常生成可反馈码 `AP-YYYYMMDD-XXXXXX`，后台凭码一次带出会话/快照/日志/任务/审计全链路 |
| 实时同步 | 后台与平台**共享同一个数据库**（单一数据源），叠加 SSE 事件流；任务每走一步后台即时可见 |
| 智能分析 | 常驻线程每 20 秒扫描真实库表，产出健康评分、8 项指标、洞察与告警，阈值全部公开 |
| 升级迭代闭环 | 只读取 `consent=1` 的已授权会话，提取需求/失败/高频主题，去重计票、P0–P3 定级、状态流转 |
| 用户排查 | 按手机号/账号检索用户与聊天记录用于修 bug，默认脱敏、每次查询留痕、明文需超管 |
| 安全运维后台 | 独立 FastAPI 应用 / 独立端口 / 独立鉴权 / 独立前端，15 个主菜单 / 54 个子菜单 |
| 容器化部署 | Docker Compose 一键启动，平台与后台双服务共享数据卷，数据库不对外暴露端口 |

---

## 快速开始

### 方式一：本地 Python（最快）

```bash
git clone <your-repo-url>.git
cd agent-platform-opensource

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 按需修改；不填也能跑（默认离线规则应答）
./start.sh                    # 平台 → http://localhost:8000/
```

浏览器打开 `http://localhost:8000/` 即可对话、办公、使用技能。

### 方式二：Docker（推荐用于长期运行）

```bash
./deploy.sh up                # 构建并启动平台(8000) + 管理后台(8001)
./deploy.sh status            # 查看状态与健康检查
./deploy.sh logs              # 跟随日志
./deploy.sh token             # 查看超管/只读令牌
./deploy.sh backup            # WAL 一致性数据库备份
./deploy.sh down              # 停止
```

首次运行会自动生成超管 / 只读双令牌并写入 `.env`（权限 600）。
管理后台入口 `http://localhost:8001/`，**必须手动输入令牌**才能进入。

### 方式三：独立启动管理后台

```bash
python3 -m uvicorn admin.server:app --host 0.0.0.0 --port 8001
```

在线单端口场景下也可挂载到平台：平台启动时会自动 `app.mount("/console", admin_app)`，
入口为 `/console/`。两种形态共用同一个数据库，数据完全一致。

### 命令行与演示

```bash
./run.sh demo                         # 跑完整链路演示（含快照/诊断/恢复）
./run.sh chat -m "你好"
./run.sh serve --port 8000
python3 -m app.cli diagnose <session_id>
```

---

## 依赖安装

### Python 依赖

```bash
pip install -r requirements.txt
```

核心编排层只需要 `fastapi` / `uvicorn` / `PyYAML` 三个包；其余为技能包里的
文档与数据处理能力所用（openpyxl、python-docx、python-pptx、reportlab、
matplotlib、pillow、beautifulsoup4、markdown），全部可离线运行。

| 场景 | 额外安装 |
|---|---|
| PDF 文本抽取 | **系统依赖** `poppler-utils`：`sudo apt-get install -y poppler-utils`（macOS：`brew install poppler`） |
| 迁移到 PostgreSQL | `pip install "psycopg[binary]>=3.1"`，并设 `AP_DB_DSN=postgresql://...` |
| 真实浏览器渲染（可选） | `pip install playwright && playwright install chromium`；未安装时按既有降级逻辑抓取并明确标注 |
| Android 客户端构建（可选） | `aapt` / `apksigner` / `zipalign` / JDK，见 `mobile/build_apk.sh` |

> 容器镜像默认跳过系统依赖以保证构建速度；需要时
> `docker build --build-arg WITH_SYSTEM_DEPS=1 -t agent-platform:2.0 .`。

---

## 配置

复制 `.env.example` 为 `.env` 后按需修改。**密钥只从环境变量读取，不写进代码或 `config/*.yaml`。**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AP_DB_DSN` | `sqlite:///var/agent.db` | 数据库 DSN；也支持 PostgreSQL |
| `AP_DATA_DIR` | `var` | 数据、令牌、备份目录 |
| `AP_WORKSPACE` | `.` | 工具与知识库允许访问的根目录 |
| `AP_CONFIRM_MODE` | `prompt` | `prompt` 人工确认 / `approve` 自动放行 / `reject` / `scripted` |
| `AP_MAX_WORKERS` | `5` | 并发上限 |
| `AP_SUBTASK_TIMEOUT` / `AP_TOOL_TIMEOUT` | `300` / `30` | 超时秒数 |
| `AP_SNAPSHOT_KEEP` | `200` | 快照保留条数 |
| `AP_LLM_BASE_URL` | 空 | Ollama 或任意 OpenAI 兼容端点 |
| `AP_LLM_API_KEY` | 空 | 仅 OpenAI 兼容端点需要；也可在网页「模型状态」里临时填写 |
| `AP_LLM_MODEL` / `AP_LLM_PROVIDER` | 空 / `auto` | 模型名与协议 |
| `AP_ADMIN_TOKEN` / `AP_ADMIN_VIEW_TOKEN` | 自动生成 | 管理后台超管 / 只读令牌 |
| `AP_ANALYSIS_INTERVAL` | `20` | 智能分析引擎扫描间隔（秒） |
| `AP_KEYSTORE_STOREPASS` / `AP_KEYSTORE_KEYPASS` | `changeit` | Android 签名口令，构建 APK 时自行设置 |

不配置模型也能运行：平台使用内置离线规则应答；配置 `AP_LLM_*` 后切换为真实模型
（Ollama 详见 `./run_ollama.sh`，DeepSeek 等 OpenAI 兼容端点直接填 `BASE_URL` + `API_KEY`）。

---

## 访问入口

| 入口 | 地址 | 说明 |
|---|---|---|
| 智能体平台 | `http://localhost:8000/` | 对话、办公、技能、资料库 |
| 管理后台（独立端口） | `http://localhost:8001/` | 需输入令牌 |
| 管理后台（挂载形态） | `http://localhost:8000/console/` | 需输入令牌 |
| 健康检查 | `http://localhost:8000/health` | 返回状态与工具/技能/Agent 数量 |
| APK 下载页 | `http://localhost:8000/download` | APK 需自行用 `mobile/build_apk.sh` 构建 |

管理后台令牌：`./deploy.sh token` 查看，或读取 `var/admin_token.txt`（权限 600）。
超管可处置事故、备份、手动分析、迭代写回、查看聊天原文；只读令牌只能查看，写操作返回 403。

---

## 目录结构

```
agent-platform-opensource/
├── README.md                    # 本文件
├── LICENSE                      # MIT
├── SECURITY.md                  # 密钥与漏洞报告约定
├── requirements.txt             # Python 依赖
├── .env.example                 # 环境变量模板（无真实密钥）
├── .gitignore                   # 排除 var/、.env、keystore、构建产物
├── Dockerfile / docker-compose.yml / deploy.sh / .dockerignore
├── start.sh / run.sh / run_ollama.sh
├── docs/ARCHITECTURE.md         # 第一阶段原始架构文档
├── config/                      # routing / policy / agents / mcp_tools / catalog
├── app/
│   ├── main.py                  # FastAPI 接入层、全局异常、事件与事故接入
│   ├── cli.py / runtime.py / config.py / models.py / errors.py / llm.py
│   ├── core/                    # supervisor / planner / manager / executor /
│   │                            # workflow / handoff / recovery / router / util
│   ├── db/                      # database / repository / 两套 schema + 幂等迁移
│   ├── obs/                     # logger / tracer / diagnose / console / intelligence
│   ├── knowledge/               # 文档入库、切块、检索
│   ├── skills/                  # loader + 65 个技能包（manifest.yaml + skill.py）
│   ├── tools/                   # 工具实现与 MCP 本地服务器
│   ├── office.py / display.py
│   └── static/                  # 平台前端（index.html / app.css / app.js / admin.html）
├── admin/                       # 独立管理后台
│   ├── server.py                # 独立 FastAPI + 鉴权中间件 + 数据接口
│   └── static/                  # index.html（SPA）+ console_ext.js
├── scripts/build_capabilities.py
├── demo/                        # 演示与验证脚本
├── samples/                     # 文本样例数据
└── mobile/                      # Android WebView 客户端源码（不含 keystore / APK）
```

---

## API 概览

| 分类 | 主要端点 |
|---|---|
| 对话与任务 | `POST /api/chat`、`GET /api/sessions`、`POST /api/optimize` |
| 能力目录 | `GET /api/tools`、`GET /api/skills`、`POST /api/capabilities/probe` |
| 模型配置 | `GET /api/llm`、`POST /api/llm/config` |
| 运维 | `GET /health`、`GET /api/artifact`、`GET /console-status` |
| 后台总览 | `GET /api/admin/overview`、`/consistency`、`/sessions`、`/events`、`/stream`(SSE) |
| 错误核查 | `GET /api/admin/incidents/lookup`、`/errors/timeline`、`/errors/similar`、`/errors/rootcause`、`/errors/trends` |
| 智能分析 | `GET /api/admin/analysis/latest`、`/runs`、`/insights`、`/trend`；`POST /analysis/run`、`/brief` |
| 升级迭代 | `GET /api/admin/iteration/stats`、`/items`；`POST /iteration/scan`、`/update` |
| 用户与同意 | `GET /api/admin/users`、`/chatlog`、`/query-audit`；`POST /users/consent` |
| 处置与备份 | `POST /api/admin/incidents/resolve`、`POST /api/admin/backup` |

所有 `/api/admin/*` 数据接口都需要 `X-Admin-Token` 请求头；写操作额外要求超管令牌。

---

## 验证

```bash
python3 demo/demo_full_chain.py          # 完整编排链路断言
python3 demo/verify_capabilities.py      # 技能 / 工具可用性验证
curl -s http://localhost:8000/health     # 服务状态
```

---

## 安全须知（开源前必读）

**本项目已按开源要求清理过，但你仍需注意：**

1. `var/`、`output/`、`logs/`、`.env`、`*.db`、`*.keystore`、`*.apk` 全部被 `.gitignore` 排除，
   **任何情况下都不要提交**：其中含 API Key、后台令牌、用户聊天记录与 Android 签名私钥。
2. 密钥只走环境变量 `AP_LLM_API_KEY` 或网页「模型状态」临时填写（仅存进程内存，重启失效）。
3. 管理后台默认**必须手动输入令牌**，不做 URL 自动登录、不做 localStorage 恢复。
4. 用户聊天内容默认脱敏；只有显式授权（`consent=1`）的会话才会进入升级迭代分析；
   按手机号排查属于运维用途，每次读取都写 `query_audit` 留痕，查看原文需超管。
5. `mobile/src/.../MainActivity.java` 的默认地址、`mobile/build_apk.sh` 的签名口令都是占位符，
   部署前请改成你自己的值，并用 `AP_KEYSTORE_*` 环境变量提供口令。
6. 数据库端口在 Docker 部署下不对外暴露；查询全部参数化，写操作走白名单。

如果发现安全问题，请按 `SECURITY.md` 说明私下报告，不要直接提公开 Issue。

---

## 诚实边界

- 当前默认是**单机 SQLite + WAL**，多进程并发读没问题，写入单写者串行化；
  上百人日常使用具备基础，**上千人高强度写入未在压测环境验证**，需切换 PostgreSQL profile 并实测。
- 「智能分析」是规则驱动的确定性统计与异常检测（标注 `rule-engine`），不是大模型自主推理；
  AI 解读按需手动触发、标注 `rule+llm`，模型不可用时如实返回失败原因。
- 鉴权是超管 / 只读双令牌，不是完整 RBAC / SSO。
- 在线单端口形态下，管理后台是同进程挂载（独立应用 / 鉴权 / 页面），不是独立容器；
  独立容器边界在 Docker 部署时成立。

---

## 许可证

[MIT](LICENSE) — 可自由使用、修改、商用，请保留版权声明。
