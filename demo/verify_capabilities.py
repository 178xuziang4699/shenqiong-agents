#!/usr/bin/env python3
"""逐个试运行所有技能与 MCP 工具，把真实结果写成证据报告。

用法：python3 demo/verify_capabilities.py
产物：var/capability_report.json（后端 /api/capabilities 读取）
      demo/capability_report.md（给人看的摘要）
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from app.config import get_settings
from app.runtime import Runtime
from app.tools.registry import ToolContext

EXTRA_SAMPLES = {
    "fs.list_dir": {"path": "samples"},
    "fs.read_text": {"path": "samples/notes.md", "max_chars": 500},
    "fs.write_text": {"path": "var/capability_probe/fs_probe.txt", "content": "probe", "overwrite": True},
    "calc.word_count": {"text": "Agent Platform 能力验证"},
    "calc.eval": {"expression": "1+2*3"},
    "kb.search": {"query": "差旅报销审批", "top_k": 2},
    "kb.ingest": {"path": "samples/kb_policy.md", "title": "报销制度"},
    "browser.browse": {"url": "http://127.0.0.1:8000/health", "limit": 300},
}


def brief(result, limit: int = 220) -> str:
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        s = str(result)
    return s[:limit] + ("…" if len(s) > limit else "")


def main() -> int:
    settings = get_settings()
    rt = Runtime(settings, echo=False, confirm_mode="approve")
    rt.startup()

    import importlib.util
    caps_path = ROOT / "scripts" / "build_capabilities.py"
    s = importlib.util.spec_from_file_location("build_caps", caps_path)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    generated_samples = {c[0]: c[4] for c in mod.CAPS}

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "skills": [], "tools": [], "failed": [],
    }

    # ---------- 技能 ----------
    for m in sorted((ROOT / "app" / "skills" / "packages").glob("*/manifest.yaml")):
        d = yaml.safe_load(m.read_text(encoding="utf-8"))
        name = d["name"]
        args = dict(d.get("sample_args") or generated_samples.get(name) or {})
        t0 = time.time()
        try:
            out = rt.skills.run(name, args, {"source": "capability_probe"})
            report["skills"].append({
                "name": name, "ok": True, "ms": round((time.time() - t0) * 1000),
                "category": d.get("category", "未分类"), "description": d.get("description", ""),
                "sample_args": args, "output": brief(out),
            })
        except Exception as exc:  # noqa: BLE001
            report["skills"].append({
                "name": name, "ok": False, "ms": round((time.time() - t0) * 1000),
                "category": d.get("category", "未分类"), "description": d.get("description", ""),
                "sample_args": args, "error": f"{type(exc).__name__}: {exc}",
            })
            report["failed"].append(f"skill:{name}: {exc}")

    # ---------- MCP 工具 ----------
    for name in sorted(rt.registry.tools):
        short = name.split(".", 1)[-1]
        args = dict(EXTRA_SAMPLES.get(name) or generated_samples.get(short) or {})
        t0 = time.time()
        try:
            # 每次调用独立 session_id：工具调用会落一条快照，固定 step_index 会撞复合主键
            out = rt.registry.call(
                name, args,
                ToolContext(session_id=f"capability_probe_{short}", agent_id="catalog", job_id="probe"),
            )
            report["tools"].append({
                "name": name, "ok": True, "ms": round((time.time() - t0) * 1000),
                "permission": rt.registry.tools[name].permission,
                "description": rt.registry.tools[name].description,
                "sample_args": args, "output": brief(out),
            })
        except Exception as exc:  # noqa: BLE001
            report["tools"].append({
                "name": name, "ok": False, "ms": round((time.time() - t0) * 1000),
                "permission": rt.registry.tools[name].permission,
                "description": rt.registry.tools[name].description,
                "sample_args": args, "error": f"{type(exc).__name__}: {exc}",
            })
            report["failed"].append(f"tool:{name}: {exc}")

    ok_s = sum(1 for x in report["skills"] if x["ok"])
    ok_t = sum(1 for x in report["tools"] if x["ok"])
    report["summary"] = {
        "skills_total": len(report["skills"]), "skills_ok": ok_s,
        "tools_total": len(report["tools"]), "tools_ok": ok_t,
        "all_ok": not report["failed"],
    }

    out_json = ROOT / "var" / "capability_report.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 能力验证报告", "",
        f"- 生成时间：{report['generated_at']}",
        f"- 技能：{ok_s}/{len(report['skills'])} 通过",
        f"- MCP 工具：{ok_t}/{len(report['tools'])} 通过",
        f"- 结论：{'全部可用' if not report['failed'] else '存在失败项'}",
        "", "## 失败明细", "",
    ]
    lines += [f"- {x}" for x in report["failed"]] or ["- 无"]
    lines += ["", "> 每个条目都通过统一入口真实调用（技能经 SkillLoader.run，工具经 ToolRegistry.call），"
              "不是静态清单。"]
    (ROOT / "demo" / "capability_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False))
    for x in report["failed"]:
        print("FAIL", x)
    rt.shutdown()
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
