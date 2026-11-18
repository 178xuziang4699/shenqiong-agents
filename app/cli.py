"""命令行入口：对话、办事、续跑、诊断、回放、入库、查看注册表。

退出码约定（脚本与 demo 依赖）：
  0 成功
  2 需要人工介入 / 诊断不可自动恢复
  3 会话被中断（可续跑）
  4 执行失败
"""

from __future__ import annotations

import argparse
import json
import sys

from app.runtime import Runtime


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _build_runtime(args) -> Runtime:
    rt = Runtime(echo=not args.quiet, confirm_mode=args.confirm_mode)
    rt.startup()
    return rt


def cmd_chat(args) -> int:
    rt = _build_runtime(args)
    try:
        out = rt.chat(args.message, args.session)
        print("\n===== 应答 =====")
        print(out["answer"])
        if out.get("citations"):
            print("\n引用来源：" + "；".join(out["citations"]))
        print(f"\nsession_id = {out['session_id']}　mode = {out['mode']}　dispatched = {out['dispatched']}")
        return 0
    finally:
        rt.shutdown()


def cmd_run(args) -> int:
    rt = _build_runtime(args)
    try:
        out = rt.run_task(args.message, args.session)
        if out["mode"] == "chat":
            print("\n（路由判定为对话模式，直接应答，未派发）")
            print(out["answer"])
        else:
            outcome = out["outcome"]
            print("\n===== 执行结果 =====")
            print(f"状态: {out['status']}　会话: {out['session_id']}")
            for task_id, payload in outcome["results"].items():
                keys = list(payload.keys())[:6]
                print(f"  {task_id}: 完成，字段 {keys}")
            for item in outcome.get("skipped", []):
                print(f"  {item['task_id']}: 跳过（{item['reason']}）")
            for item in outcome.get("failed", []):
                print(f"  {item['task_id']}: 失败（{item['error']}）")
        return 0 if out.get("status", "completed") == "completed" else 4
    except Exception as exc:  # noqa: BLE001 - CLI 层统一兜底并返回可识别退出码
        from app.errors import AgentInterrupt
        if isinstance(exc, AgentInterrupt):
            print(f"\n[中断] {exc.message}；session_id = {args.session or '（见上方日志）'}")
            print("可用 `python -m app.cli resume --session <id>` 从断点续跑。")
            return 3
        print(f"[失败] {type(exc).__name__}: {exc}")
        return 4
    finally:
        rt.shutdown()


def cmd_resume(args) -> int:
    rt = _build_runtime(args)
    try:
        out = rt.resume(args.session)
        print("\n===== 恢复报告 =====")
        if out.get("report"):
            print(out["report"])
        if not out.get("resumed"):
            print(f"\n未续跑：{out.get('reason') or out.get('error') or '需人工介入'}")
            return 2
        print(f"\n修复动作: {out['action']}")
        print(f"复用已完成任务: {out['reused_tasks']}")
        print(f"重置任务: {out['reset_tasks']}")
        outcome = out["outcome"]
        print(f"续跑结果: {outcome['status']}")
        for task_id, payload in outcome["results"].items():
            print(f"  {task_id}: 完成，字段 {list(payload.keys())[:6]}")
        return 0 if out["ok"] else 4
    finally:
        rt.shutdown()


def cmd_diagnose(args) -> int:
    rt = _build_runtime(args)
    try:
        print(rt.diagnose_text(args.session))
        diag = rt.diagnose(args.session)
        if args.json:
            print()
            _print_json(diag)
        return 0 if diag.get("found") else 2
    finally:
        rt.shutdown()


def cmd_replay(args) -> int:
    rt = _build_runtime(args)
    try:
        chain = rt.replay(args.session)
        print(f"===== 执行链回放 session={args.session}（{len(chain)} 步）=====")
        for row in chain:
            flag = {"ok": "✓", "failed": "✗", "interrupted": "⏸"}.get(row["status"], "?")
            print(f"{flag} step {row['step_index']:>3} | {row['agent_id']:<12} | {row['phase']:<28} | {row['status']}")
            if row.get("error"):
                print(f"        错误: {row['error']}")
            if row.get("handoff"):
                h = row["handoff"]
                print(f"        流转: {h['from_agent']} → {h['to_agent']}｜{h['reason']}")
        return 0
    finally:
        rt.shutdown()


def cmd_graph(args) -> int:
    rt = _build_runtime(args)
    try:
        print(f"===== 任务流转图 session={args.session} =====")
        print(rt.graph(args.session))
        return 0
    finally:
        rt.shutdown()


def cmd_ingest(args) -> int:
    rt = _build_runtime(args)
    try:
        from app.knowledge.ingest import ingest_path
        result = ingest_path(rt.repo, args.path, rt.settings.workspace, args.title)
        _print_json(result)
        return 0
    finally:
        rt.shutdown()


def cmd_tools(args) -> int:
    rt = _build_runtime(args)
    try:
        for item in rt.registry.catalogue():
            print(f"- {item['name']:<16} [{item['permission']:<9}] {item['transport']:<5} "
                  f"timeout={item['timeout_seconds']}s  schema={item['schema_source']}")
            print(f"    {item['description']}")
        return 0
    finally:
        rt.shutdown()


def cmd_skills(args) -> int:
    rt = _build_runtime(args)
    try:
        for pkg in rt.skills.catalogue():
            print(f"- {pkg['name']:<16} v{pkg['version']:<8} entry={pkg['entry']}")
            print(f"    触发: {pkg['triggers']}")
            print(f"    入参: {list((pkg['inputs'].get('properties') or {}).keys())}")
        return 0
    finally:
        rt.shutdown()


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-platform", description="多智能体编排平台 CLI")
    parser.add_argument("--quiet", action="store_true", help="不打印结构化日志到终端")
    parser.add_argument("--confirm-mode", choices=["prompt", "approve", "reject", "scripted"],
                        default=None, help="人工确认模式（默认取配置/环境变量）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("chat", help="对话模式")
    p.add_argument("--message", "-m", required=True)
    p.add_argument("--session", "-s", default=None)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("run", help="办事模式（自动路由）")
    p.add_argument("--message", "-m", required=True)
    p.add_argument("--session", "-s", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="从断点续跑")
    p.add_argument("--session", "-s", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("diagnose", help="一键诊断")
    p.add_argument("--session", "-s", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("replay", help="按 session 回放执行链")
    p.add_argument("--session", "-s", required=True)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("graph", help="输出任务流转图")
    p.add_argument("--session", "-s", required=True)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("ingest", help="文档入库")
    p.add_argument("--path", required=True)
    p.add_argument("--title", default=None)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("tools", help="查看 MCP 工具注册表")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("skills", help="查看已安装技能包")
    p.set_defaults(func=cmd_skills)

    p = sub.add_parser("serve", help="启动最小 Web 接入层")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
