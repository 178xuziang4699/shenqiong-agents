#!/usr/bin/env bash
# ============================================================
# 一键接入本机 Ollama 私有模型并启动 Web 控制台
#
# 用法（在你自己的电脑上，cd 到 agent_platform 后）：
#   ./run_ollama.sh
#   AP_OLLAMA_HOST=http://192.168.1.5:11434 ./run_ollama.sh
#   AP_LLM_MODEL=deepseek-r1:14b ./run_ollama.sh     # 手动指定，留空则自动挑最强
#   PORT=9000 ./run_ollama.sh
#
# 自动选型规则（app/llm.py）：优先 DeepSeek 系列，再按 parameter_size 取最大；
# 选型理由会显示在页面顶部「模型状态」条里，可在日志中复核。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

OLLAMA_HOST="${AP_OLLAMA_HOST:-http://127.0.0.1:11434}"
PORT="${PORT:-8000}"

echo "==> 检查 Ollama 服务：$OLLAMA_HOST"
if ! curl -fsS -m 4 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
  echo "连不上 Ollama。请确认："
  echo "  1) 已安装并启动 ollama（终端执行 ollama serve，或打开 Ollama 应用）"
  echo "  2) 模型已拉取，例如：ollama pull deepseek-r1:14b"
  echo "  3) 若 Ollama 在另一台机器：AP_OLLAMA_HOST=http://<IP>:11434 ./run_ollama.sh"
  exit 1
fi

echo "==> 已安装的模型："
curl -fsS "$OLLAMA_HOST/api/tags" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('models', []):
    det = m.get('details') or {}
    size = (m.get('size') or 0) / 1e9
    print('  - %-28s %-8s %.2f GB' % (m.get('name'), det.get('parameter_size', '?'), size))
" || true

export AP_LLM_BASE_URL="$OLLAMA_HOST"
export AP_LLM_PROVIDER=ollama
export AP_LLM_MODEL="${AP_LLM_MODEL:-}"
export AP_LLM_AUTO_SELECT=1
export AP_LLM_TIMEOUT="${AP_LLM_TIMEOUT:-180}"
export AP_CONFIRM_MODE="${AP_CONFIRM_MODE:-prompt}"

echo
echo "==> 启动平台：http://localhost:$PORT"
echo "    模型：${AP_LLM_MODEL:-自动挑选（DeepSeek 优先，参数最大）}"
echo "    对话模式会带多轮上下文由模型直接回应；办事模式仍走原有编排链。"
echo
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level warning
