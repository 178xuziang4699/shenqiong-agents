"""配置加载：.env → 环境变量展开 → YAML 解析。

规则：密钥只从环境变量读取（.env 仅作为本地开发载体，不入库不入日志）。
所有 YAML 字符串支持 ${VAR:-default} 形式的环境覆盖。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(path: Path) -> None:
    """极简 .env 解析，已存在的环境变量优先（不覆盖）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name)
            if got is None or got == "":
                return default if default is not None else ""
            return got
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是映射: {path}")
    return _expand(data)


@dataclass
class Settings:
    root: Path
    data_dir: Path
    logs_dir: Path
    workspace: Path
    db_dsn: str
    policy: dict
    routing: dict
    agents: dict
    tools: dict
    llm: dict

    # ---- 便捷取值 ----
    def p(self, *keys, default=None):
        return _dig(self.policy, keys, default)

    def a(self, *keys, default=None):
        return _dig(self.agents, keys, default)

    def t(self, *keys, default=None):
        return _dig(self.tools, keys, default)

    @property
    def max_workers(self) -> int:
        return int(self.p("concurrency", "max_workers", default=5))

    @property
    def subtask_timeout(self) -> int:
        return int(self.p("timeouts", "subtask_seconds", default=300))

    @property
    def tool_timeout(self) -> int:
        return int(self.p("timeouts", "tool_seconds", default=30))

    @property
    def retry_max_attempts(self) -> int:
        return int(self.p("retry", "max_attempts", default=3))

    @property
    def snapshot_keep(self) -> int:
        return int(self.p("snapshots", "keep_last", default=200))


def _dig(tree: Any, keys: tuple, default=None):
    cur = tree
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("AP_DATA_DIR") or (ROOT / "var")).resolve()
    logs_dir = (ROOT / "logs").resolve()
    workspace = Path(os.environ.get("AP_WORKSPACE") or ROOT).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    dsn = os.environ.get("AP_DB_DSN") or f"sqlite:///{data_dir / 'agent.db'}"

    # 界面「添加我的 API」保存的配置：环境变量优先，其次读本地受限文件。
    # 目的：服务重启 / 重新发布后，用户已验证的模型不会掉回离线规则应答。
    # 文件仅本机可读（chmod 600），密钥不进日志、不进数据库、不回传状态接口明文。
    saved: dict = {}
    cfg_file = data_dir / "llm_runtime.json"
    if cfg_file.is_file():
        try:
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}

    def llm_env(key: str, default: str = "") -> str:
        return os.environ.get(key) or str(saved.get(key.lower().replace("ap_llm_", ""), default))

    return Settings(
        root=ROOT,
        data_dir=data_dir,
        logs_dir=logs_dir,
        workspace=workspace,
        db_dsn=dsn,
        policy=_load_yaml(ROOT / "config" / "policy.yaml"),
        routing=_load_yaml(ROOT / "config" / "routing.yaml"),
        agents=_load_yaml(ROOT / "config" / "agents.yaml"),
        tools=_load_yaml(ROOT / "config" / "mcp_tools.yaml"),
        llm={
            # Ollama：填 http://<主机>:11434 即可；OpenAI 兼容：填 .../v1 或设 provider=openai
            "base_url": llm_env("AP_LLM_BASE_URL", ""),
            "api_key": llm_env("AP_LLM_API_KEY", ""),
            # 留空 = 启动时读 /api/tags，优先 DeepSeek 系列并自动挑参数规模最大的模型
            "model": llm_env("AP_LLM_MODEL", ""),
            "provider": llm_env("AP_LLM_PROVIDER", "auto"),
            "auto_select": os.environ.get("AP_LLM_AUTO_SELECT", "1").lower()
                            not in ("0", "false", "no", "off"),
            "timeout": int(os.environ.get("AP_LLM_TIMEOUT", "60")),
        },
    )
