"""本地能力实现库：技能包与 MCP 工具共用的真实执行体。

设计约束：
- 只用 Python 标准库 + 环境已装依赖，不依赖网络、云凭证或宿主平台服务；
- 每个函数签名统一为 (params: dict) -> dict，返回可观测的结构化结果；
- 涉及文件路径一律限制在工作区内，越界直接抛错；
- 写类能力只输出到 var/capability_probe/，不污染业务数据。
"""

from __future__ import annotations

import ast
import base64
import calendar
import csv
import difflib
import hashlib
import html as html_mod
import io
import json
import math
import os
import platform
import re
import secrets
import statistics
import string
import subprocess
import sys
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(os.environ.get("AP_WORKSPACE") or Path(__file__).resolve().parents[2]).resolve()
PROBE_DIR = ROOT / "var" / "capability_probe"

_FALLBACK_TEXT = (
    "报销制度要求费用发生后 30 个自然日内提交报销单。审批链路按金额分三档："
    "2000 元以下由直属主管审批，2000 至 10000 元需部门负责人加签，10000 元以上需财务总监审批。"
    "所有审批节点超时 48 小时未处理会自动提醒，超时 96 小时自动升级。结论：单据必须附发票与行程单。"
)


def _sample_text() -> str:
    p = ROOT / "samples" / "notes.md"
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:20000] or _FALLBACK_TEXT
    except OSError:
        return _FALLBACK_TEXT


def _text(p: dict) -> str:
    return str(p.get("text") or _sample_text())


def _path(p: dict, key: str = "path", default: str = "samples/notes.md") -> Path:
    raw = str(p.get(key) or default)
    target = (ROOT / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
    if target != ROOT and ROOT not in target.parents:
        raise PermissionError(f"路径越界：{raw} 不在工作区 {ROOT} 内")
    return target


def _write(path: str, data: bytes | str) -> dict:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    dest = PROBE_DIR / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        dest.write_text(data, encoding="utf-8")
    else:
        dest.write_bytes(data)
    return {"path": str(dest.relative_to(ROOT)), "bytes": dest.stat().st_size}


def _json_default(o):
    return str(o)


# ==================== 文本处理 ====================
def text_summarize(p: dict) -> dict:
    text = _text(p)
    limit = int(p.get("max_points") or 5)
    parts = [s.strip() for s in re.split(r"(?<=[。！？；\n])|(?<=[.!?;])\s+", text) if s.strip()]
    scored = sorted(
        ((len(s) + 8 * bool(re.search(r"\d", s)), i, s) for i, s in enumerate(parts)),
        key=lambda x: (-x[0], x[1]),
    )[:limit]
    picked = [s for _, _, s in sorted(scored, key=lambda x: x[1])]
    return {"points": len(picked), "summary": picked, "summary_text": "；".join(picked),
            "source_chars": len(text)}


def text_wordcount(p: dict) -> dict:
    t = _text(p)
    han = len(re.findall(r"[\u4e00-\u9fff]", t))
    words = re.findall(r"[A-Za-z]+", t)
    return {"chars": len(t), "han_chars": han, "latin_words": len(words),
            "lines": len(t.splitlines()), "sentences": len(re.findall(r"[。！？.!?]", t)),
            "top_words": Counter(words).most_common(5)}


def text_keywords(p: dict) -> dict:
    t = _text(p)
    tokens = re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{3,}", t)
    stop = {"要求", "这个", "我们", "可以", "以及", "进行", "所有", "自动"}
    freq = Counter(x for x in tokens if x not in stop)
    top = int(p.get("top_k") or 8)
    return {"tokens": len(tokens), "keywords": [{"word": w, "count": c} for w, c in freq.most_common(top)]}


def text_diff(p: dict) -> dict:
    a = str(p.get("left") or _sample_text()[:120]).splitlines()
    b = str(p.get("right") or _sample_text()[60:200]).splitlines()
    lines = list(difflib.unified_diff(a, b, fromfile="left", tofile="right", lineterm=""))
    ratio = difflib.SequenceMatcher(None, "\n".join(a), "\n".join(b)).ratio()
    return {"similarity": round(ratio, 4), "added": sum(1 for x in lines if x.startswith("+") and not x.startswith("+++")),
            "removed": sum(1 for x in lines if x.startswith("-") and not x.startswith("---")),
            "diff": lines[:40]}


def text_regex(p: dict) -> dict:
    pattern = str(p.get("pattern") or r"\d+\s*元")
    matches = re.findall(pattern, _text(p))
    return {"pattern": pattern, "count": len(matches), "matches": [str(m)[:120] for m in matches[:20]]}


def text_replace(p: dict) -> dict:
    t = _text(p)
    mapping = p.get("mapping") or {"报销": "费用报销", "审批": "审核"}
    out = t
    n = 0
    for k, v in mapping.items():
        c = out.count(str(k))
        n += c
        out = out.replace(str(k), str(v))
    return {"replacements": n, "chars_before": len(t), "chars_after": len(out), "preview": out[:300]}


def text_case(p: dict) -> dict:
    t = str(p.get("text") or "agent_platform capability").strip()
    style = str(p.get("style") or "snake")
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", t)
    if style == "camel":
        out = words[0].lower() + "".join(w.capitalize() for w in words[1:])
    elif style == "kebab":
        out = "-".join(w.lower() for w in words)
    elif style == "upper":
        out = t.upper()
    else:
        out = "_".join(w.lower() for w in words)
    return {"style": style, "result": out, "original": t}


def text_humanize(p: dict) -> dict:
    t = str(p.get("text") or "综上所述，值得注意的是，本方案旨在赋能业务，助力降本增效，打造闭环。")
    rules = {
        "综上所述，": "", "值得注意的是，": "", "旨在赋能": "用于", "助力": "帮助",
        "打造闭环": "形成完整流程", "深度赋能": "支持", "全方位": "全面", "极大地": "明显",
        "在当前背景下，": "", "不难看出，": "",
    }
    out = t
    for k, v in rules.items():
        out = out.replace(k, v)
    return {"removed_phrases": len(rules), "chars_before": len(t), "chars_after": len(out), "result": out}


def text_glossary(p: dict) -> dict:
    table = p.get("glossary") or {"Agent": "智能体", "MCP": "工具协议", "Skill": "技能包"}
    t = str(p.get("text") or "Agent calls MCP tools and loads a Skill package.")
    hits = {k: t.count(k) for k in table}
    out = t
    for k, v in table.items():
        out = out.replace(k, str(v))
    return {"hits": hits, "result": out}


def text_chunk(p: dict) -> dict:
    size = int(p.get("size") or 200)
    overlap = int(p.get("overlap") or 20)
    t = _text(p)
    chunks, start = [], 0
    while start < len(t) and len(chunks) < 50:
        chunks.append({"index": len(chunks), "start": start, "text": t[start:start + size]})
        start += max(1, size - overlap)
    return {"chunk_size": size, "overlap": overlap, "count": len(chunks), "chunks": chunks[:3]}


def text_dedupe(p: dict) -> dict:
    lines = _text(p).splitlines()
    seen, out = set(), []
    for line in lines:
        key = line.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return {"input_lines": len(lines), "output_lines": len(out), "removed": len(lines) - len(out),
            "preview": out[:8]}


def text_sentiment(p: dict) -> dict:
    t = _text(p)
    pos = ["好", "优", "通过", "完成", "成功", "提升", "满意", "清晰"]
    neg = ["失败", "错误", "拒绝", "超时", "风险", "问题", "延迟", "异常"]
    ps = sum(t.count(w) for w in pos)
    ns = sum(t.count(w) for w in neg)
    score = (ps - ns) / max(1, ps + ns)
    return {"positive_hits": ps, "negative_hits": ns, "score": round(score, 4),
            "label": "偏正面" if score > 0.2 else "偏负面" if score < -0.2 else "中性"}


# ==================== 数据 / 结构化 ====================
def _load_json(p: dict):
    if isinstance(p.get("data"), (dict, list)):
        return p["data"]
    if p.get("path"):
        return json.loads(_path(p).read_text(encoding="utf-8"))
    return json.loads(str(p.get("json") or '{"name":"agent","skills":[{"id":1},{"id":2}],"ok":true}'))


def json_query(p: dict) -> dict:
    data = _load_json(p)
    path_str = str(p.get("path_expr") or "skills.0.id")
    cur = data
    for seg in path_str.split("."):
        cur = cur[int(seg)] if isinstance(cur, list) and seg.isdigit() else cur[seg]
    return {"path": path_str, "value": cur, "type": type(cur).__name__}


def json_validate(p: dict) -> dict:
    data = _load_json(p)
    required = p.get("required") or ["name"]
    missing = [k for k in required if not isinstance(data, dict) or k not in data]
    return {"valid": not missing, "required": list(required), "missing": missing,
            "keys": list(data.keys()) if isinstance(data, dict) else None}


def json_pretty(p: dict) -> dict:
    data = _load_json(p)
    out = json.dumps(data, ensure_ascii=False, indent=2)
    return {"chars": len(out), "preview": out[:600]}


def csv_profile(p: dict) -> dict:
    target = _path(p, default="samples/sample.csv")
    with target.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    cols = list(rows[0].keys()) if rows else []
    numeric = {}
    for c in cols:
        vals = []
        for r in rows:
            try:
                vals.append(float(r[c]))
            except (ValueError, TypeError):
                pass
        if vals:
            numeric[c] = {"count": len(vals), "min": min(vals), "max": max(vals),
                          "mean": round(statistics.mean(vals), 4)}
    return {"path": str(target.relative_to(ROOT)), "rows": len(rows), "columns": cols,
            "numeric_summary": numeric, "preview": rows[:3]}


def csv_filter(p: dict) -> dict:
    target = _path(p, default="samples/sample.csv")
    col = str(p.get("column") or "")
    op = str(p.get("op") or "gt")
    val = float(p.get("value") or 0)
    with target.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    def keep(r):
        try:
            x = float(r[col])
        except (KeyError, ValueError, TypeError):
            return False
        return {"gt": x > val, "lt": x < val, "eq": x == val, "gte": x >= val, "lte": x <= val}.get(op, False)
    kept = [r for r in rows if keep(r)]
    return {"rows_in": len(rows), "rows_out": len(kept), "filter": f"{col} {op} {val}", "preview": kept[:5]}


def csv_to_markdown(p: dict) -> dict:
    target = _path(p, default="samples/sample.csv")
    with target.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {"rows": 0, "markdown": ""}
    head = "| " + " | ".join(rows[0]) + " |\n|" + "|".join(["---"] * len(rows[0])) + "|\n"
    body = "".join("| " + " | ".join(r) + " |\n" for r in rows[1:11])
    return {"rows": len(rows) - 1, "markdown": (head + body)[:1200]}


def yaml_to_json(p: dict) -> dict:
    import yaml
    raw = _path(p, default="samples/sample.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return {"keys": list(data.keys()) if isinstance(data, dict) else None,
            "json": json.dumps(data, ensure_ascii=False, default=_json_default)[:800]}


def xml_to_dict(p: dict) -> dict:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_path(p, default="samples/sample.xml").read_text(encoding="utf-8"))
    def walk(e):
        children = list(e)
        return {e.tag: {"text": (e.text or "").strip(), "attrs": e.attrib,
                        "children": [walk(c) for c in children]}}
    return {"root_tag": root.tag, "structure": walk(root)}


def html_extract(p: dict) -> dict:
    from bs4 import BeautifulSoup
    raw = _path(p, default="samples/sample.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    links = [{"text": a.get_text(strip=True), "href": a.get("href")} for a in soup.find_all("a")]
    return {"title": soup.title.get_text(strip=True) if soup.title else "",
            "text": soup.get_text(" ", strip=True)[:600], "links": links[:10]}


def markdown_to_html(p: dict) -> dict:
    import markdown
    out = markdown.markdown(_text(p)[:2000])
    return {"chars": len(out), "html": out[:800]}


def table_sort(p: dict) -> dict:
    rows = p.get("rows") or [{"name": "b", "v": 2}, {"name": "a", "v": 3}, {"name": "c", "v": 1}]
    key = str(p.get("key") or "v")
    rev = bool(p.get("reverse") or False)
    ordered = sorted(rows, key=lambda r: (r.get(key) is None, r.get(key)), reverse=rev)
    return {"key": key, "reverse": rev, "rows": ordered}


def stats_describe(p: dict) -> dict:
    raw = p.get("values") or [2, 4, 4, 4, 5, 5, 7, 9]
    vals = [float(x) for x in raw]
    return {"count": len(vals), "min": min(vals), "max": max(vals),
            "mean": round(statistics.mean(vals), 4), "median": statistics.median(vals),
            "stdev": round(statistics.pstdev(vals), 4), "sum": sum(vals)}


# ==================== 编码 / 安全 ====================
def hash_digest(p: dict) -> dict:
    algo = str(p.get("algorithm") or "sha256")
    data = _text(p).encode("utf-8")
    h = hashlib.new(algo)
    h.update(data)
    return {"algorithm": algo, "input_bytes": len(data), "hex": h.hexdigest(),
            "base64": base64.b64encode(h.digest()).decode()}


def base64_codec(p: dict) -> dict:
    mode = str(p.get("mode") or "encode")
    raw = str(p.get("data") or "Agent Platform")
    if mode == "decode":
        return {"mode": mode, "result": base64.b64decode(raw).decode("utf-8", errors="replace")}
    return {"mode": mode, "result": base64.b64encode(raw.encode()).decode()}


def url_codec(p: dict) -> dict:
    mode = str(p.get("mode") or "encode")
    raw = str(p.get("data") or "报销审批 2026")
    return {"mode": mode, "result": unquote(raw) if mode == "decode" else quote(raw)}


def uuid_gen(p: dict) -> dict:
    n = min(int(p.get("count") or 3), 20)
    version = int(p.get("version") or 4)
    items = [str(uuid.uuid1() if version == 1 else uuid.uuid4()) for _ in range(n)]
    return {"count": n, "version": version, "uuids": items}


def password_gen(p: dict) -> dict:
    length = min(max(int(p.get("length") or 16), 8), 64)
    alphabet = string.ascii_letters + string.digits
    if p.get("symbols"):
        alphabet += "!@#$%^&*"
    value = "".join(secrets.choice(alphabet) for _ in range(length))
    return {"length": length, "alphabet_size": len(alphabet),
            "entropy_bits": round(length * math.log2(len(alphabet)), 1),
            "password": value}


def jwt_payload(p: dict) -> dict:
    token = str(p.get("token") or "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZW1vIiwiZXhwIjoxNzAwMDAwMDAwMH0.sig")
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("token 结构不合法")
    raw = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(raw))
    return {"header_segment": parts[0][:80], "payload": payload,
            "verified": False, "note": "仅解码 payload，未校验签名（不用于鉴权判断）"}


def color_convert(p: dict) -> dict:
    import colorsys
    hexv = str(p.get("hex") or "#2563eb").lstrip("#")
    r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return {"hex": "#" + hexv, "rgb": [round(r * 255), round(g * 255), round(b * 255)],
            "hsv": [round(h * 360, 1), round(s * 100, 1), round(v * 100, 1)],
            "luminance": round(0.2126 * r + 0.7152 * g + 0.0722 * b, 4)}


def cron_explain(p: dict) -> dict:
    expr = str(p.get("expression") or "*/15 9-18 * * 1-5")
    fields = expr.split()
    names = ["分钟", "小时", "日", "月", "星期"]
    parts = []
    for name, val in zip(names, fields):
        if val == "*":
            desc = "每" + name
        elif val.startswith("*/"):
            desc = f"每 {val[2:]} {name}"
        elif "-" in val:
            desc = f"{name}范围 {val}"
        else:
            desc = f"{name}={val}"
        parts.append(desc)
    return {"expression": expr, "fields": len(fields), "explain": "，".join(parts)}


def semver_compare(p: dict) -> dict:
    def key(v):
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
    a, b = str(p.get("a") or "1.2.3"), str(p.get("b") or "1.10.0")
    ka, kb = key(a), key(b)
    return {"a": a, "b": b, "result": -1 if ka < kb else 1 if ka > kb else 0,
            "newer": b if kb > ka else a}


def regex_explain(p: dict) -> dict:
    pattern = str(p.get("pattern") or r"^(帮我|请).*(写|整理)(报告|笔记)$")
    tokens = []
    i = 0
    simple = {"^": "行首", "$": "行尾", ".": "任意字符", "*": "重复 0 次或多次",
              "+": "重复 1 次或多次", "?": "可选", "\\d": "数字", "\\w": "单词字符"}
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            tokens.append(simple.get(pattern[i:i + 2], "转义字符"))
            i += 2
        else:
            tokens.append(simple.get(ch, "字面量 " + ch))
            i += 1
    return {"pattern": pattern, "token_count": len(tokens),
            "groups": len(re.findall(r"\((?!\?)", pattern)), "tokens": tokens[:24]}


# ==================== 文档 / 文件 ====================
def pdf_extract(p: dict) -> dict:
    target = _path(p, default="samples/sample.pdf")
    proc = subprocess.run(["pdftotext", "-layout", str(target), "-"],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext 失败: {proc.stderr.strip()[:200]}")
    return {"path": str(target.relative_to(ROOT)), "chars": len(proc.stdout),
            "text": proc.stdout[:800]}


def pdf_info(p: dict) -> dict:
    target = _path(p, default="samples/sample.pdf")
    proc = subprocess.run(["pdfinfo", str(target)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"pdfinfo 失败: {proc.stderr.strip()[:200]}")
    info = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return {"path": str(target.relative_to(ROOT)), "info": info}


def docx_inspect(p: dict) -> dict:
    import docx
    doc = docx.Document(str(_path(p, default="samples/sample.docx")))
    paras = [x.text for x in doc.paragraphs if x.text.strip()]
    return {"paragraphs": len(paras), "tables": len(doc.tables),
            "chars": sum(len(x) for x in paras), "preview": paras[:6]}


def xlsx_profile(p: dict) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(_path(p, default="samples/sample.xlsx")), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(max_row=min(ws.max_row or 1, 50), values_only=True))
        sheets.append({"name": ws.title, "rows": ws.max_row, "columns": ws.max_column,
                       "preview": [list(r) for r in rows[:3]]})
    wb.close()
    return {"sheet_count": len(sheets), "sheets": sheets}


def pptx_outline(p: dict) -> dict:
    from pptx import Presentation
    prs = Presentation(str(_path(p, default="samples/sample.pptx")))
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        texts = [sh.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text.strip()]
        slides.append({"index": i, "texts": texts[:5]})
    return {"slides": len(slides), "outline": slides}


def file_tree(p: dict) -> dict:
    root = _path(p, default="samples")
    depth = int(p.get("depth") or 2)
    items = []
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if len(rel.parts) > depth:
            continue
        items.append({"path": str(rel), "dir": f.is_dir(), "size": f.stat().st_size if f.is_file() else 0})
        if len(items) >= 60:
            break
    return {"root": str(root.relative_to(ROOT)), "count": len(items), "items": items[:30]}


def image_info(p: dict) -> dict:
    from PIL import Image
    target = _path(p, default="samples/sample.png")
    with Image.open(target) as im:
        return {"path": str(target.relative_to(ROOT)), "format": im.format,
                "size": list(im.size), "mode": im.mode, "bytes": target.stat().st_size}


def image_thumb(p: dict) -> dict:
    from PIL import Image
    target = _path(p, default="samples/sample.png")
    size = (int(p.get("width") or 64), int(p.get("height") or 64))
    with Image.open(target) as im:
        im.thumbnail(size)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return {"source": str(target.relative_to(ROOT)), "size": list(size),
            **_write("thumb.png", buf.getvalue())}


def chart_bar(p: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    raw_labels = p.get("labels") or ["Chat", "Task", "Resume", "Diagnose"]
    # 服务器常无中文字体，非 ASCII 标签降级为 L0/L1…，避免渲染出方块与字体告警
    labels = [str(l) if str(l).isascii() else f"L{i}" for i, l in enumerate(raw_labels)]
    values = p.get("values") or [12, 9, 4, 7]
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.bar(labels, values, color="#2563eb")
    ax.set_title("Capability Probe")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90)
    plt.close(fig)
    return {"labels": labels, "values": values, **_write("chart.png", buf.getvalue())}


def report_html(p: dict) -> dict:
    title = str(p.get("title") or "能力探测报告")
    body = str(p.get("body") or _sample_text()[:400])
    doc = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
           f"<title>{html_mod.escape(title)}</title>"
           "<body style='font-family:system-ui;max-width:720px;margin:40px auto;line-height:1.8'>"
           f"<h1>{html_mod.escape(title)}</h1><p>{html_mod.escape(body)}</p>"
           f"<footer>generated at {datetime.now().isoformat(timespec='seconds')}</footer></body></html>")
    return {"title": title, **_write("report.html", doc)}


# ==================== 系统 / 工具 ====================
def datetime_tool(p: dict) -> dict:
    now = datetime.now(timezone.utc)
    tz_h = float(p.get("tz_offset_hours") or 8)
    local = now.astimezone(timezone(timedelta(hours=tz_h)))
    return {"utc": now.isoformat(timespec="seconds"), "local": local.isoformat(timespec="seconds"),
            "tz_offset_hours": tz_h, "epoch": int(now.timestamp()),
            "iso_week": local.isocalendar()[1], "weekday": local.strftime("%A")}


def sys_info(p: dict) -> dict:
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "machine": platform.machine(), "processor_count": os.cpu_count(),
            "cwd": str(ROOT), "pid": os.getpid()}


def env_whitelist(p: dict) -> dict:
    allow = ("AP_", "PORT", "LANG", "TZ")
    names = sorted(k for k in os.environ if k.startswith(allow))
    return {"count": len(names), "names": names,
            "note": "仅返回变量名，不返回取值（避免密钥泄露）"}


def calendar_gen(p: dict) -> dict:
    year = int(p.get("year") or datetime.now().year)
    month = int(p.get("month") or datetime.now().month)
    return {"year": year, "month": month, "weeks": calendar.monthcalendar(year, month),
            "first_weekday": calendar.weekday(year, month, 1), "days": calendar.monthrange(year, month)[1]}


def math_eval(p: dict) -> dict:
    expr = str(p.get("expression") or "(12 + 8) * 3 / 4")
    if not re.fullmatch(r"[0-9+\-*/(). ]+", expr):
        raise ValueError("表达式只允许数字与四则运算符号")
    expr_ast = ast.parse(expr, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
    for sub in ast.walk(expr_ast):
        if not isinstance(sub, allowed):
            raise ValueError("表达式包含不允许的语法节点")
    return {"expression": expr,
            "result": eval(compile(expr_ast, "<math>", "eval"), {"__builtins__": {}}, {})}


def unit_convert(p: dict) -> dict:
    value = float(p.get("value") or 1)
    src = str(p.get("from") or "km")
    dst = str(p.get("to") or "m")
    factors = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
               "mi": 1609.344, "ft": 0.3048, "in": 0.0254}
    if src not in factors or dst not in factors:
        raise ValueError(f"不支持的单位：{src} -> {dst}（支持 {', '.join(factors)}）")
    return {"from": src, "to": dst, "value": value,
            "result": round(value * factors[src] / factors[dst], 6)}


def geo_solar(p: dict) -> dict:
    """根据纬度与日期估算昼长（NOAA 简化公式），纯计算、无需网络。"""
    lat = float(p.get("latitude") or 39.9)
    doy = int(p.get("day_of_year") or datetime.now().timetuple().tm_yday)
    decl = 23.44 * math.sin(math.radians(360 / 365 * (doy - 81)))
    cos_h = -math.tan(math.radians(lat)) * math.tan(math.radians(decl))
    if cos_h > 1:
        hours, note = 0.0, "极夜"
    elif cos_h < -1:
        hours, note = 24.0, "极昼"
    else:
        hours, note = 2 * math.degrees(math.acos(cos_h)) / 15, "正常"
    return {"latitude": lat, "day_of_year": doy, "declination_deg": round(decl, 3),
            "daylight_hours": round(hours, 2), "note": note,
            "disclaimer": "天文估算，非实时天气；不接入外部气象服务"}


def cron_next(p: dict) -> dict:
    """简易 cron：只解析 */N 分钟表达式，给出接下来 5 个匹配时刻。"""
    minute_field = str(p.get("expression") or "*/15 * * * *").split()[0]
    step = int(minute_field[2:]) if minute_field.startswith("*/") else 1
    start = datetime.now().replace(second=0, microsecond=0)
    hits = []
    for i in range(1, 60 * 24):
        cand = start + timedelta(minutes=i)
        if cand.minute % max(1, step) == 0:
            hits.append(cand.isoformat(timespec="minutes"))
        if len(hits) >= 5:
            break
    return {"expression_minute": minute_field, "step": max(1, step), "next": hits}


def file_diff(p: dict) -> dict:
    a = _path(p, "left", "samples/notes.md").read_text(encoding="utf-8", errors="replace").splitlines()
    b = _path(p, "right", "samples/kb_policy.md").read_text(encoding="utf-8", errors="replace").splitlines()
    lines = list(difflib.unified_diff(a, b, lineterm=""))
    return {"left_lines": len(a), "right_lines": len(b), "diff_lines": len(lines),
            "preview": lines[:30]}


def dir_checksum(p: dict) -> dict:
    root = _path(p, default="samples")
    files = sorted(f for f in root.rglob("*") if f.is_file())[:100]
    h = hashlib.sha256()
    for f in files:
        h.update(str(f.relative_to(ROOT)).encode())
        h.update(f.read_bytes()[:4096])
    return {"root": str(root.relative_to(ROOT)), "files": len(files), "sha256": h.hexdigest()}


def kb_lookup(p: dict) -> dict:
    query = str(p.get("query") or "差旅报销审批节点")
    try:
        from app.runtime import get_runtime
        from app.knowledge.retriever import search
        res = search(get_runtime().repo, query, top_k=int(p.get("top_k") or 3))
        return {"query": query, "hits": len(res["hits"]), "citations": res["citations"],
                "preview": [h["content"][:160] for h in res["hits"]], "backend": "sqlite knowledge_chunks"}
    except Exception as exc:  # noqa: BLE001 - 明确回报不可用，不静默
        return {"query": query, "available": False, "error": f"{type(exc).__name__}: {exc}"}



# 显式登记，避免把 helper 误当能力
OPS = {
    "text_summarize": text_summarize, "text_wordcount": text_wordcount, "text_keywords": text_keywords,
    "text_diff": text_diff, "text_regex": text_regex, "text_replace": text_replace,
    "text_case": text_case, "text_humanize": text_humanize, "text_glossary": text_glossary,
    "text_chunk": text_chunk, "text_dedupe": text_dedupe, "text_sentiment": text_sentiment,
    "json_query": json_query, "json_validate": json_validate, "json_pretty": json_pretty,
    "csv_profile": csv_profile, "csv_filter": csv_filter, "csv_to_markdown": csv_to_markdown,
    "yaml_to_json": yaml_to_json, "xml_to_dict": xml_to_dict, "html_extract": html_extract,
    "markdown_to_html": markdown_to_html, "table_sort": table_sort, "stats_describe": stats_describe,
    "hash_digest": hash_digest, "base64_codec": base64_codec, "url_codec": url_codec,
    "uuid_gen": uuid_gen, "password_gen": password_gen, "jwt_payload": jwt_payload,
    "color_convert": color_convert, "cron_explain": cron_explain,
    "semver_compare": semver_compare, "regex_explain": regex_explain,
    "pdf_extract": pdf_extract, "pdf_info": pdf_info, "docx_inspect": docx_inspect,
    "xlsx_profile": xlsx_profile, "pptx_outline": pptx_outline, "file_tree": file_tree,
    "image_info": image_info, "image_thumb": image_thumb, "chart_bar": chart_bar,
    "report_html": report_html, "datetime_tool": datetime_tool, "sys_info": sys_info,
    "env_whitelist": env_whitelist, "calendar_gen": calendar_gen, "math_eval": math_eval,
    "unit_convert": unit_convert, "geo_solar": geo_solar, "cron_next": cron_next,
    "file_diff": file_diff, "dir_checksum": dir_checksum, "kb_lookup": kb_lookup,
}
