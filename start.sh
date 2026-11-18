#!/usr/bin/env bash
# 启动 Web 接入层。默认 8000；发布到在线环境时通过 PORT 环境变量注入端口。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
# Web 实例没有可交互终端，关键写操作默认自动放行；
# 本机命令行 ./run.sh 仍默认 prompt（真人在终端确认），需要时显式覆盖。
export AP_CONFIRM_MODE="${AP_CONFIRM_MODE:-approve}"
echo "[start] Agent Platform 监听 0.0.0.0:${PORT}（确认模式 ${AP_CONFIRM_MODE}）"
# 保留 uvicorn 的启动横幅：发布通道靠 "Uvicorn running on ..." 判定就绪
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
