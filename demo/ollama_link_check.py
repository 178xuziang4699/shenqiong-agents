#!/usr/bin/env python3
"""Ollama 协议链路自检服务 —— 这不是模型，只用来验证接入链路是否真的跑通。

在沙箱 / CI 这类装不了 Ollama 的环境里，用它验证平台以下四件事：
  1) /api/tags 模型发现，以及「优先 DeepSeek、按参数规模挑最大」的自动选型；
  2) /api/chat 的多轮 messages 是否原样送达（回答里回显轮数与上下文条数）；
  3) 中文系统提示词是否随请求进入 messages；
  4) 停掉本服务后，平台是否写 WARNING 日志、降级离线应答并提示「已降级」。

启动：  python3 demo/ollama_link_check.py --port 11435
平台侧： AP_LLM_BASE_URL=http://127.0.0.1:11435 ./run.sh serve
验证完请把 AP_LLM_BASE_URL 换成你本机真实的 Ollama 地址。
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 故意混入一个参数更大的非 DeepSeek 模型，检验「DeepSeek 优先」是否生效
MODELS = [
    {"name": "deepseek-r1:1.5b", "size": 1_100_000_000,
     "details": {"parameter_size": "1.5B", "family": "deepseek"}},
    {"name": "deepseek-r1:7b", "size": 4_700_000_000,
     "details": {"parameter_size": "7.0B", "family": "deepseek"}},
    {"name": "deepseek-r1:14b", "size": 9_000_000_000,
     "details": {"parameter_size": "14.0B", "family": "deepseek"}},
    {"name": "qwen2.5:32b", "size": 20_000_000_000,
     "details": {"parameter_size": "32.0B", "family": "qwen"}},
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默，日志由平台侧统一落盘
        return

    def _send(self, obj: dict, code: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._send({"models": MODELS})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send({"error": "bad json"}, 400)
            return
        if not self.path.startswith("/api/chat"):
            self._send({"error": "not found"}, 404)
            return
        messages = body.get("messages", []) or []
        users = [m for m in messages if m.get("role") == "user"]
        last = str(users[-1].get("content", "")) if users else ""
        system = next((str(m.get("content", "")) for m in messages
                       if m.get("role") == "system"), "")
        answer = (
            f"【链路自检｜这不是真实模型】已收到第 {len(users)} 轮提问，"
            f"本次共传入 {len(messages)} 条上下文，系统提示 {len(system)} 字。\n"
            f"你这句是：{last[:60]}\n"
            "平台已把多轮历史与系统提示正确送进模型。把地址换成你本机 Ollama 后，"
            "这里就会变成私有 DeepSeek 的真实回答。"
        )
        self._send({
            "model": body.get("model"),
            "message": {"role": "assistant", "content": answer},
            "done": True,
        })


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11435)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Ollama 协议自检服务已启动：http://{args.host}:{args.port}", flush=True)
    srv.serve_forever()
