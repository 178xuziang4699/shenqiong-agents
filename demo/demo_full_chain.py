#!/usr/bin/env python3
"""最小可运行 demo：覆盖完整链路。

覆盖点（与需求 13 一一对应）：
  对话 → 派发 → 执行 → 回传 → 落日志 → 人为中断 → 断点恢复
并且额外验证：失败重试（指数退避）、验收打回重做、人工确认闸门、知识库检索带引用、
              任务流转图、执行链回放、一键诊断。

每个阶段都跑在独立子进程里，唯一通信介质是磁盘（SQLite + 快照 + 日志），
所以"中断后能续跑"不是模拟，而是真的从落盘断点继续。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
DB = ROOT / "var" / "agent.db"
REPORT = ROOT / "output" / "report.md"

RESULTS: list[tuple[str, bool, str]] = []


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run_cli(args: list[str], env_extra: dict | None = None) -> tuple[int, str]:
    env = {**os.environ}
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    cmd = [PY, "-m", "app.cli", *args]
    print(f"\n$ {' '.join(cmd)}")
    if env_extra:
        print(f"  注入环境: {env_extra}")
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr.rstrip())
    print(f"  (退出码 {proc.returncode})")
    return proc.returncode, proc.stdout + proc.stderr


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def clean_state() -> None:
    section("阶段 0 · 清理历史状态（保证从零可复现）")
    for path in (ROOT / "var", ROOT / "output"):
        if path.exists():
            shutil.rmtree(path)
    (ROOT / "var").mkdir(parents=True, exist_ok=True)
    (ROOT / "output").mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "logs").glob("*.jsonl"):
        f.unlink()
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    print(f"  已清空 {ROOT/'var'}、{ROOT/'output'}、{ROOT/'logs/*.jsonl'}")


def main() -> int:
    clean_state()

    # ---------------- 阶段1：知识入库 ----------------
    section("阶段 1 · 知识库入库（切分 → 向量化 → 落库）")
    code, out = run_cli(["ingest", "--path", "samples/kb_policy.md", "--title", "报销制度"],
                        {"AP_CONFIRM_MODE": "approve"})
    check("文档入库成功", code == 0 and '"chunks"' in out, "kb_policy.md 已切分入库")

    # ---------------- 阶段2：对话模式 ----------------
    section("阶段 2 · 对话模式（不触发派发）")
    code, out = run_cli(["chat", "-m", "差旅报销有哪几个审批节点？", "-s", "demo_chat"],
                        {"AP_CONFIRM_MODE": "approve"})
    dispatched_false = "dispatched = False" in out
    has_citation = "来源：" in out and "#chunk=" in out
    check("对话模式未触发派发", dispatched_false)
    check("回答带来源引用", has_citation, "命中 kb_policy.md 切块")
    handoffs = query("SELECT COUNT(*) AS c FROM handoffs WHERE session_id = ?", ("demo_chat",))
    check("对话模式无任何流转记录", handoffs[0]["c"] == 0, f"handoffs={handoffs[0]['c']}")

    # ---------------- 阶段3：办事模式全链路 ----------------
    section("阶段 3 · 办事模式全链路（并行 + 条件 + 人工确认 + 失败重试 + 打回重做）")
    code, out = run_cli(
        ["run", "-m", "帮我把 samples/notes.md 整理成一份报告，写入 output/report.md", "-s", "demo_full"],
        {
            "AP_CONFIRM_MODE": "approve",
            "AP_FAULT_TOOL": "calc.word_count:1",   # 首次工具调用失败 → 触发重试退避
            "AP_FAULT_VALIDATION": "T3:1",          # 首次验收不通过 → 触发打回重做
        },
    )
    check("全链路执行完成", code == 0)
    check("报告文件已落盘", REPORT.exists() and REPORT.stat().st_size > 200,
          f"{REPORT.relative_to(ROOT)} {REPORT.stat().st_size if REPORT.exists() else 0} bytes")

    logs = query("SELECT message FROM logs WHERE session_id = ?", ("demo_full",))
    all_msg = "\n".join(r["message"] for r in logs)
    check("失败重试有日志（指数退避）", "后重试" in all_msg)
    check("验收打回有日志", "打回重做" in all_msg)
    check("人工确认节点有落痕", "human_gate" in all_msg and "confirm" in all_msg)

    tasks = {r["task_id"]: r["status"] for r in query(
        "SELECT task_id, status FROM tasks WHERE session_id = ? ORDER BY task_id", ("demo_full",))}
    check("5 个子任务全部完成", all(v == "done" for v in tasks.values()), str(tasks))
    jobs = query("SELECT job_id, status FROM jobs WHERE session_id = ?", ("demo_full",))
    check("作业全部落盘为 done", all(j["status"] == "done" for j in jobs), f"{len(jobs)} 个作业")
    audits = query("SELECT tool, status FROM tool_audit WHERE session_id = ?", ("demo_full",))
    check("工具调用有审计", len(audits) >= 5, f"{len(audits)} 条审计")

    # ---------------- 阶段4：知识库检索链路（办事模式）----------------
    section("阶段 4 · 办事模式下的知识检索（带来源引用）")
    code, out = run_cli(["run", "-m", "检索一下知识库里关于报销时限的规定", "-s", "demo_lookup"],
                        {"AP_CONFIRM_MODE": "approve"})
    check("检索型任务完成", code == 0)

    # ---------------- 阶段5：人为中断 ----------------
    section("阶段 5 · 人为中断（在写盘作业前模拟进程被杀）")
    code, out = run_cli(
        ["run", "-m", "帮我把 samples/notes.md 整理成一份报告，写入 output/report.md", "-s", "demo_interrupt"],
        {
            "AP_CONFIRM_MODE": "approve",
            "AP_INTERRUPT_ON_JOB": "T4/j0",   # 执行写文件作业前中断
        },
    )
    check("中断退出码为 3", code == 3, f"实际 {code}")
    sess = query("SELECT status FROM sessions WHERE session_id = ?", ("demo_interrupt",))
    check("会话状态落盘为 interrupted", sess and sess[0]["status"] == "interrupted")
    snap = query(
        "SELECT step_index, phase, status FROM snapshots WHERE session_id = ? ORDER BY step_index DESC LIMIT 1",
        ("demo_interrupt",),
    )
    check("中断步骤已写快照", bool(snap) and snap[0]["status"] in ("interrupted", "ok"),
          f"最后一步 = {snap[0]['phase'] if snap else '无'} / {snap[0]['status'] if snap else '-'}")
    done_before = {r["task_id"]: r["status"] for r in query(
        "SELECT task_id, status FROM tasks WHERE session_id = ?", ("demo_interrupt",))}
    check("中断时前三步已完成、T4 未完成",
          done_before.get("T1") == "done" and done_before.get("T3") == "done"
          and done_before.get("T4") != "done", str(done_before))

    # ---------------- 阶段6：一键诊断 ----------------
    section("阶段 6 · 一键诊断（输入 session_id 出结论）")
    code, out = run_cli(["diagnose", "-s", "demo_interrupt"])
    check("诊断输出最后成功步骤", "最后成功步骤" in out)
    check("诊断输出首个失败点", "首个失败点" in out)
    check("错误归类为 INTERRUPTED", "INTERRUPTED" in out)
    check("建议动作正确", "resume_from_breakpoint" in out)

    # ---------------- 阶段7：断点恢复 ----------------
    section("阶段 7 · 断点恢复（禁止从头重跑）")
    t1_runs_before = query(
        "SELECT COUNT(*) AS c FROM logs WHERE session_id = ? AND message LIKE ?",
        ("demo_interrupt", "%执行作业 T1/j0%"),
    )[0]["c"]
    code, out = run_cli(["resume", "-s", "demo_interrupt"], {"AP_CONFIRM_MODE": "approve"})
    check("恢复后执行完成", code == 0)
    check("恢复流程复用了已完成任务", "复用已完成任务" in out and "T1" in out)
    t1_runs_after = query(
        "SELECT COUNT(*) AS c FROM logs WHERE session_id = ? AND message LIKE ?",
        ("demo_interrupt", "%执行作业 T1/j0%"),
    )[0]["c"]
    check("T1 未因恢复而重跑", t1_runs_after == t1_runs_before, f"恢复前 {t1_runs_before} 次 / 恢复后 {t1_runs_after} 次")
    final_tasks = {r["task_id"]: r["status"] for r in query(
        "SELECT task_id, status FROM tasks WHERE session_id = ?", ("demo_interrupt",))}
    check("恢复后全部任务 done", all(v == "done" for v in final_tasks.values()), str(final_tasks))

    # ---------------- 阶段7b：细粒度断点（作业级复用）----------------
    section("阶段 7b · 细粒度断点：子任务半途中断后从作业边界续跑")
    code, out = run_cli(
        ["run", "-m", "帮我把 samples/notes.md 整理成一份报告，写入 output/report.md", "-s", "demo_interrupt2"],
        {"AP_CONFIRM_MODE": "approve", "AP_INTERRUPT_ON_JOB": "T1/j1"},
    )
    check("作业级中断退出码为 3", code == 3)
    t1j0_before = query(
        "SELECT COUNT(*) AS c FROM logs WHERE session_id = ? AND message LIKE ?",
        ("demo_interrupt2", "%执行作业 T1/j0%"),
    )[0]["c"]
    code, out = run_cli(["resume", "-s", "demo_interrupt2"], {"AP_CONFIRM_MODE": "approve"})
    check("细粒度恢复执行完成", code == 0)
    reuse_job_logs = query(
        "SELECT COUNT(*) AS c FROM logs WHERE session_id = ? AND message LIKE ?",
        ("demo_interrupt2", "%复用已完成作业%"),
    )[0]["c"]
    check("作业级复用生效", reuse_job_logs >= 1, f"{reuse_job_logs} 条作业复用日志")
    t1j0_after = query(
        "SELECT COUNT(*) AS c FROM logs WHERE session_id = ? AND message LIKE ?",
        ("demo_interrupt2", "%执行作业 T1/j0%"),
    )[0]["c"]
    check("已完成的 T1/j0 未重复执行", t1j0_after == t1j0_before, f"{t1j0_before} → {t1j0_after}")

    # ---------------- 阶段8：流转图与回放 ----------------
    section("阶段 8 · 任务流转图 + 执行链回放")
    code, out = run_cli(["graph", "-s", "demo_interrupt"])
    check("流转图含 supervisor → 管理者", "supervisor" in out and "mgr_content" in out)
    check("流转图含管理者 → 执行者", "exec_text" in out)
    check("流转图含回传边", "回传" in out)

    code, out = run_cli(["replay", "-s", "demo_interrupt"])
    steps = query("SELECT COUNT(*) AS c FROM snapshots WHERE session_id = ?", ("demo_interrupt",))[0]["c"]
    check("执行链可回放", code == 0 and "执行链回放" in out, f"{steps} 步快照")

    # ---------------- 阶段9：汇总 ----------------
    section("阶段 9 · 汇总")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for name, ok, detail in RESULTS:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"（{detail}）" if detail else ""))
    print(f"\n结果：{passed}/{total} 项通过")
    if REPORT.exists():
        print(f"产出文件：{REPORT.relative_to(ROOT)}（{REPORT.stat().st_size} bytes）")
    db_stats = query(
        "SELECT (SELECT COUNT(*) FROM snapshots) AS snapshots,"
        " (SELECT COUNT(*) FROM logs) AS logs,"
        " (SELECT COUNT(*) FROM handoffs) AS handoffs,"
        " (SELECT COUNT(*) FROM tool_audit) AS audits,"
        " (SELECT COUNT(*) FROM knowledge_chunks) AS chunks"
    )[0]
    print(f"数据库：{json.dumps(db_stats, ensure_ascii=False)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
