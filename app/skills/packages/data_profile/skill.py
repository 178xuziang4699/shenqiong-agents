"""技能包：描述性统计。返回完整的统计指标，不是固定占位结果。"""

import statistics


def run(params: dict, ctx: dict | None = None) -> dict:
    label = str(params.get("label") or "数据样本").strip()
    raw = params.get("values") or [12, 18, 15, 22, 19, 25, 17, 21, 20, 24]
    try:
        nums = [float(x) for x in raw]
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"数值格式错误：{exc}"}
    if not nums:
        return {"ok": False, "error": "数值列表为空"}
    s = sorted(nums)
    n = len(s)

    def q(p: float) -> float:
        idx = (n - 1) * p
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return {
        "ok": True, "label": label, "count": n,
        "sum": round(sum(s), 4), "mean": round(statistics.mean(s), 4),
        "median": round(statistics.median(s), 4),
        "stdev": round(statistics.pstdev(s), 4) if n > 1 else 0,
        "min": s[0], "max": s[-1],
        "q1": round(q(0.25), 4), "q3": round(q(0.75), 4),
        "range": round(s[-1] - s[0], 4),
    }
