#!/usr/bin/env bash
# 统一启动脚本：run.sh <子命令> [...]
# 例：./run.sh demo / ./run.sh chat -m "你好" / ./run.sh serve --port 8000
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ "${1:-}" = "demo" ]; then
  shift
  exec "$PY" demo/demo_full_chain.py "$@"
fi

exec "$PY" -m app.cli "$@"
