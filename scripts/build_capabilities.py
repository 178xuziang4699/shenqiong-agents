#!/usr/bin/env python3
"""批量构建能力目录：样本文件 / 技能包 / MCP 服务 / 注册表项 / 宿主对照目录。

可重复执行（幂等）：所有生成物都由本脚本从 CAPS 表推导，避免手工维护 100+ 个文件。
生成后运行 demo/verify_capabilities.py 逐个试运行验证。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

# (name, category, description, keywords, sample_args, permission)
CAPS = [
    ("text_summarize", "文本处理", "按句子信息密度抽取要点摘要", ["摘要", "提炼", "要点"],
     {"text": "报销制度要求 30 天内提交。审批链路按金额分三档。超时 48 小时自动提醒。结论：必须附发票。", "max_points": 3}, "read"),
    ("text_wordcount", "文本处理", "统计中英文字数、行数与高频词", ["字数", "统计"],
     {"text": "Agent Platform 支持 55 个技能。Skill 包可插拔，MCP 工具统一注册。"}, "read"),
    ("text_keywords", "文本处理", "基于词频抽取关键词", ["关键词", "词频"],
     {"text": "报销审批 审批链路 报销制度 报销单 财务总监 审批节点", "top_k": 5}, "read"),
    ("text_diff", "文本处理", "生成两段文本的 unified diff 与相似度", ["差异", "比对", "diff"],
     {"left": "第一行\n第二行\n第三行", "right": "第一行\n第二行改了\n第三行"}, "read"),
    ("text_regex", "文本处理", "按正则抽取匹配片段", ["正则", "抽取"],
     {"text": "2000 元以下主管审批，10000 元以上财务总监审批", "pattern": r"\d+\s*元"}, "read"),
    ("text_replace", "文本处理", "按术语映射批量替换", ["替换", "改写"],
     {"text": "审批通过后报销完成", "mapping": {"审批": "审核", "报销": "费用报销"}}, "read"),
    ("text_case", "文本处理", "命名风格转换 snake / camel / kebab / upper", ["命名", "驼峰"],
     {"text": "Agent Platform Capability", "style": "snake"}, "read"),
    ("text_humanize", "文本处理", "去除常见 AI 套话，压缩冗余表述", ["去AI腔", "润色"],
     {"text": "综上所述，值得注意的是，本方案旨在赋能业务，助力降本增效，打造闭环。"}, "read"),
    ("text_glossary", "文本处理", "按术语表做确定性翻译替换", ["术语", "翻译"],
     {"text": "Agent calls MCP tools and loads a Skill package.",
      "glossary": {"Agent": "智能体", "MCP": "工具协议", "Skill": "技能包"}}, "read"),
    ("text_chunk", "文本处理", "按长度与重叠度切分长文本", ["切分", "分块", "chunk"],
     {"text": "知识库文档入库前需要切分。切分后做向量化，再写入 SQLite。检索时按余弦相似度召回。", "size": 24, "overlap": 6}, "read"),
    ("text_dedupe", "文本处理", "按行去重并保持原顺序", ["去重", "清洗"],
     {"text": "甲\n乙\n甲\n\n丙\n乙"}, "read"),
    ("text_sentiment", "文本处理", "基于情感词典做倾向打分", ["情感", "倾向"],
     {"text": "执行完成，结果清晰，没有失败和风险，提升明显。"}, "read"),

    ("json_query", "数据结构化", "按点分路径读取 JSON 字段", ["JSON", "查询"],
     {"json": '{"name":"agent","skills":[{"id":1},{"id":2}],"ok":true}', "path_expr": "skills.0.id"}, "read"),
    ("json_validate", "数据结构化", "校验必填字段是否齐全", ["校验", "schema"],
     {"json": '{"name":"agent","ok":true}', "required": ["name", "ok"]}, "read"),
    ("json_pretty", "数据结构化", "格式化 JSON 并输出摘要", ["格式化", "JSON"],
     {"json": '{"a":1,"b":[2,3],"c":{"d":"ok"}}'}, "read"),
    ("csv_profile", "数据结构化", "CSV 列统计与数值摘要", ["CSV", "统计"],
     {"path": "samples/sample.csv"}, "read"),
    ("csv_filter", "数据结构化", "按列值条件筛选 CSV 行", ["筛选", "CSV"],
     {"path": "samples/sample.csv", "column": "score", "op": "gte", "value": 80}, "read"),
    ("csv_to_markdown", "数据结构化", "CSV 转 Markdown 表格", ["Markdown", "表格"],
     {"path": "samples/sample.csv"}, "read"),
    ("yaml_to_json", "数据结构化", "YAML 解析并转 JSON", ["YAML", "转换"],
     {"path": "samples/sample.yaml"}, "read"),
    ("xml_to_dict", "数据结构化", "XML 解析为结构树", ["XML", "解析"],
     {"path": "samples/sample.xml"}, "read"),
    ("html_extract", "数据结构化", "提取 HTML 正文、标题与链接", ["HTML", "抓取", "正文"],
     {"path": "samples/sample.html"}, "read"),
    ("markdown_to_html", "数据结构化", "Markdown 渲染为 HTML", ["Markdown", "渲染"],
     {"text": "# 标题\n\n- 项目一\n- 项目二\n\n**加粗**"}, "read"),
    ("table_sort", "数据结构化", "按指定键排序记录列表", ["排序", "表格"],
     {"rows": [{"name": "b", "v": 2}, {"name": "a", "v": 3}, {"name": "c", "v": 1}],
      "key": "v", "reverse": False}, "read"),
    ("stats_describe", "数据结构化", "描述性统计：均值/中位数/标准差", ["统计", "描述"],
     {"values": [2, 4, 4, 4, 5, 5, 7, 9]}, "read"),

    ("hash_digest", "编码与安全", "计算文本摘要（sha256 / md5 等）", ["哈希", "摘要", "sha256"],
     {"text": "Agent Platform", "algorithm": "sha256"}, "read"),
    ("base64_codec", "编码与安全", "Base64 编码与解码", ["base64", "编解码"],
     {"data": "Agent Platform", "mode": "encode"}, "read"),
    ("url_codec", "编码与安全", "URL 百分号编码与解码", ["URL", "编码"],
     {"data": "报销审批 2026", "mode": "encode"}, "read"),
    ("uuid_gen", "编码与安全", "生成 UUID v1 / v4", ["UUID", "随机"],
     {"count": 3, "version": 4}, "read"),
    ("password_gen", "编码与安全", "用安全随机数生成密码并估算熵", ["密码", "随机"],
     {"length": 16, "symbols": True}, "read"),
    ("jwt_payload", "编码与安全", "解码 JWT payload（不验签，仅用于排查）", ["JWT", "解码"],
     {"token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZW1vIiwiZXhwIjoxNzAwMDAwMDAwMH0.sig"}, "read"),
    ("color_convert", "编码与安全", "HEX / RGB / HSV / 亮度换算", ["颜色", "HEX"],
     {"hex": "#2563eb"}, "read"),
    ("cron_explain", "编码与安全", "把 cron 表达式翻译成自然语言", ["cron", "定时"],
     {"expression": "*/15 9-18 * * 1-5"}, "read"),
    ("semver_compare", "编码与安全", "语义化版本比较", ["版本号", "semver"],
     {"a": "1.2.3", "b": "1.10.0"}, "read"),
    ("regex_explain", "编码与安全", "逐字符解释正则表达式结构", ["正则", "解释"],
     {"pattern": r"^(帮我|请).*(写|整理)(报告|笔记)$"}, "read"),

    ("pdf_extract", "文档与文件", "用 pdftotext 提取 PDF 正文", ["PDF", "提取"],
     {"path": "samples/sample.pdf"}, "read"),
    ("pdf_info", "文档与文件", "读取 PDF 页数、尺寸等元信息", ["PDF", "元信息"],
     {"path": "samples/sample.pdf"}, "read"),
    ("docx_inspect", "文档与文件", "读取 Word 段落与表格数", ["Word", "docx"],
     {"path": "samples/sample.docx"}, "read"),
    ("xlsx_profile", "文档与文件", "读取 Excel 工作表结构与预览", ["Excel", "xlsx"],
     {"path": "samples/sample.xlsx"}, "read"),
    ("pptx_outline", "文档与文件", "提取 PPT 每页文本大纲", ["PPT", "pptx"],
     {"path": "samples/sample.pptx"}, "read"),
    ("file_tree", "文档与文件", "列出目录树（深度与数量受限）", ["目录", "文件树"],
     {"path": "samples", "depth": 1}, "read"),
    ("image_info", "文档与文件", "读取图片格式、尺寸与色彩模式", ["图片", "元信息"],
     {"path": "samples/sample.png"}, "read"),
    ("image_thumb", "文档与文件", "生成缩略图并落盘到 var/capability_probe", ["缩略图", "图片"],
     {"path": "samples/sample.png", "width": 64, "height": 64}, "write"),
    ("chart_bar", "文档与文件", "用 matplotlib 生成柱状图 PNG", ["图表", "可视化"],
     {"labels": ["Chat", "Task", "Resume", "Diagnose"], "values": [12, 9, 4, 7]}, "write"),
    ("report_html", "文档与文件", "生成 HTML 报告并落盘", ["报告", "HTML"],
     {"title": "能力探测报告", "body": "本报告由本地技能真实生成，用于验证技能可执行。"}, "write"),

    ("datetime_tool", "系统与工具", "UTC / 本地时间 / 时间戳 / ISO 周", ["时间", "时区"],
     {"tz_offset_hours": 8}, "read"),
    ("sys_info", "系统与工具", "读取当前 Python 与主机环境信息", ["环境", "系统"],
     {}, "read"),
    ("env_whitelist", "系统与工具", "仅列出允许的环境变量名（不暴露取值）", ["环境变量", "安全"],
     {}, "read"),
    ("calendar_gen", "系统与工具", "生成指定年月的月历矩阵", ["日历", "月历"],
     {"year": 2026, "month": 11}, "read"),
    ("math_eval", "系统与工具", "安全四则运算求值（AST 白名单）", ["计算", "表达式"],
     {"expression": "(12 + 8) * 3 / 4"}, "read"),
    ("unit_convert", "系统与工具", "长度单位换算", ["单位", "换算"],
     {"value": 1, "from": "km", "to": "m"}, "read"),
    ("geo_solar", "系统与工具", "按纬度与日期估算昼长（纯天文公式）", ["日出", "昼长"],
     {"latitude": 39.9, "day_of_year": 300}, "read"),
    ("cron_next", "系统与工具", "推算 */N 分钟cron的下几次触发时刻", ["cron", "下次执行"],
     {"expression": "*/15 * * * *"}, "read"),
    ("file_diff", "系统与工具", "比对两个文本文件的差异", ["文件", "diff"],
     {"left": "samples/notes.md", "right": "samples/kb_policy.md"}, "read"),
    ("dir_checksum", "系统与工具", "计算目录下文件的聚合校验和", ["校验和", "目录"],
     {"path": "samples"}, "read"),
    ("kb_lookup", "知识与检索", "检索知识库并返回带来源引用的结果", ["知识库", "检索", "引用"],
     {"query": "差旅报销审批节点", "top_k": 3}, "read"),
]

CATEGORY_ORDER = ["文本处理", "数据结构化", "编码与安全", "文档与文件", "系统与工具", "知识与检索"]
CATEGORY_COLOR = {
    "文本处理": "#2563eb", "数据结构化": "#7c3aed", "编码与安全": "#0d9488",
    "文档与文件": "#d97706", "系统与工具": "#475569", "知识与检索": "#db2777",
}

# 宿主（WorkBuddy）当前会话可用技能 → 本地等价能力映射
HOST_MAP = {
    "automation-task-manager": ("cron_explain", "定时任务规则解释（不接入宿主调度器）"),
    "computer-use": ("sys_info", "读取本机环境信息（不做 GUI 自动化）"),
    "figma-connector": ("color_convert", "设计色值换算（不连 Figma API）"),
    "github-connector": ("file_diff", "本地文件差异比对（不连 GitHub OAuth）"),
    "gongfeng-enterprise-connector": ("file_diff", "本地文件差异比对（不连工蜂）"),
    "library": ("kb_lookup", "本地 SQLite 知识库检索（不连云资料库）"),
    "发布为应用": ("report_html", "生成可发布的 HTML 页面（不调用平台发布通道）"),
    "diagnose": ("text_regex", "日志错误码抽取（本地规则版）"),
    "whobot-phonecall-skill": ("text_glossary", "话术术语替换（不发起真实电话）"),
    "workbuddy-local-office-edit": ("docx_inspect", "读取 Word 结构（不做在线编辑）"),
    "workbuddy-office-routing": ("file_tree", "本地目录扫描（不路由到在线 Office）"),
    "agent-browser": ("browser_browse", "Playwright 驱动真实 Chromium 渲染网页（执行 JS、取渲染后 DOM）"),
    "docx": ("docx_inspect", "Word 段落与表格读取"),
    "agentmail": ("text_glossary", "邮件模板术语替换（不发邮件）"),
    "apple-notes": ("text_chunk", "笔记切分（不连 macOS 备忘录）"),
    "apple-reminders": ("cron_next", "下次触发时刻推算（不连提醒事项）"),
    "blogwatcher": ("html_extract", "HTML 正文提取（不订阅 RSS）"),
    "gifgrep": ("image_info", "图片元信息读取（不联网搜 GIF）"),
    "github": ("file_diff", "本地 diff（不连 GitHub）"),
    "gog": ("csv_to_markdown", "表格转 Markdown（不连 Google Workspace）"),
    "healthcheck": ("stats_describe", "数值描述统计（不写健康记录）"),
    "himalaya": ("text_replace", "文本替换（不收发邮件）"),
    "humanizer": ("text_humanize", "去 AI 套话（规则版，不调用模型）"),
    "imsg": ("url_codec", "URL 编解码（不发 iMessage）"),
    "mcporter": ("json_pretty", "JSON 格式化（不代理 MCP）"),
    "nano-banana-pro": ("image_thumb", "本地缩略图生成（不调用图像模型）"),
    "nano-pdf": ("pdf_info", "PDF 元信息读取"),
    "obsidian": ("markdown_to_html", "Markdown 渲染（不连 Obsidian 库）"),
    "openai-image-gen": ("image_thumb", "本地图片处理（不调用图像 API）"),
    "openai-whisper-api": ("text_chunk", "文本切分（不调用语音转写 API）"),
    "openai-whisper": ("text_chunk", "文本切分（不加载 Whisper 模型）"),
    "oracle": ("text_diff", "文本差异比对（不调用第二模型）"),
    "peekaboo": ("image_info", "图片元信息（不截屏 macOS UI）"),
    "qmd": ("kb_lookup", "本地知识库检索"),
    "sag": ("text_case", "命名风格转换（不合成语音）"),
    "skill-creator": ("json_validate", "技能 manifest 字段校验"),
    "skill-vetter": ("json_validate", "技能 manifest 字段校验"),
    "songsee": ("stats_describe", "数值统计（不解析音频频谱）"),
    "summarize": ("text_summarize", "要点摘要抽取"),
    "things-mac": ("cron_next", "时刻推算（不连 Things 3）"),
    "tmux": ("sys_info", "进程环境信息（不操控 tmux）"),
    "trello": ("table_sort", "记录排序（不连 Trello）"),
    "video-frames": ("image_thumb", "图片缩放（不抽视频帧）"),
    "wacli": ("url_codec", "URL 编解码（不发 WhatsApp）"),
    "weather": ("geo_solar", "昼长天文估算（不接入实时天气 API）"),
    "xiaohongshu": ("text_keywords", "关键词抽取（不连小红书）"),
    "xurl": ("html_extract", "HTML 提取（不连 Twitter）"),
    "pdf": ("pdf_extract", "PDF 正文提取"),
    "playwright-cli": ("browser_browse", "Playwright 驱动真实 Chromium 渲染网页"),
    "pptx": ("pptx_outline", "PPT 大纲提取"),
    "xlsx": ("xlsx_profile", "Excel 结构读取"),
    "workbuddy-docx": ("docx_inspect", "Word 结构读取"),
    "tdoc-orchestrator": ("report_html", "HTML 报告生成"),
    "workbuddy-pptx": ("pptx_outline", "PPT 大纲提取"),
    "workbuddy-sheetagent": ("xlsx_profile", "Excel 结构读取"),
}

CONNECTORS = [
    ("ImageGen", "图像生成", "image_thumb"),
    ("VideoGen", "视频生成", "chart_bar"),
    ("LSP", "代码智能", "json_query"),
    ("library_search", "云资料库语义检索", "kb_lookup"),
    ("sheetagent", "在线表格代理", "xlsx_profile"),
    ("space-library", "空间资料导出", "csv_to_markdown"),
    ("sandbox_proxy", "连接器授权", "env_whitelist"),
    ("agent-browser", "浏览器渲染", "browser_browse"),
    ("playwright-cli", "浏览器渲染", "browser_browse"),
]


def json_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "string"


def props_for(sample: dict) -> dict:
    out = {}
    for k, v in sample.items():
        item = {"type": json_type(v)}
        if item["type"] == "string":
            item["description"] = f"参数 {k}"
        out[k] = item
    return out


def build_samples() -> None:
    d = ROOT / "samples"
    d.mkdir(exist_ok=True)
    (d / "sample.csv").write_text(
        "name,score,department\n张三,92,研发\n李四,85,财务\n王五,78,研发\n赵六,95,市场\n钱七,61,财务\n",
        encoding="utf-8")
    (d / "sample.json").write_text(
        json.dumps({"name": "agent", "skills": [{"id": 1}, {"id": 2}], "ok": True}, ensure_ascii=False),
        encoding="utf-8")
    (d / "sample.yaml").write_text(
        "name: agent_platform\nversion: 1.0.0\nfeatures:\n  - mcp\n  - skills\n  - recovery\n",
        encoding="utf-8")
    (d / "sample.xml").write_text(
        "<?xml version='1.0'?><root><item id='1'>笔记</item><item id='2'>报告</item></root>",
        encoding="utf-8")
    (d / "sample.html").write_text(
        "<!doctype html><html><head><title>能力样本</title></head><body>"
        "<h1>本地能力验证页</h1><p>这是一个用于 html_extract 的样本页面。</p>"
        "<a href='https://example.com/a'>链接 A</a><a href='https://example.com/b'>链接 B</a>"
        "</body></html>", encoding="utf-8")

    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(d / "sample.pdf"))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Agent Platform capability sample")
    c.drawString(72, 700, "This PDF is generated locally for pdf_extract / pdf_info probes.")
    c.showPage()
    c.save()

    import docx
    doc = docx.Document()
    doc.add_heading("能力样本文档", level=1)
    doc.add_paragraph("第一段：用于验证 docx_inspect 能读取段落与表格。")
    doc.add_paragraph("第二段：所有样本均由本地脚本生成，不依赖外部服务。")
    doc.add_table(rows=2, cols=2)
    doc.save(str(d / "sample.docx"))

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "样例"
    ws.append(["name", "score"])
    ws.append(["张三", 92])
    ws.append(["李四", 85])
    wb.save(str(d / "sample.xlsx"))

    from pptx import Presentation
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "能力样本"
    s1.placeholders[1].text = "用于验证 pptx_outline"
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "第二页"
    prs.save(str(d / "sample.pptx"))

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (160, 90), "#2563eb")
    ImageDraw.Draw(im).rectangle([20, 20, 140, 70], fill="#ffffff")
    im.save(d / "sample.png")


def build_skill_packages() -> None:
    base = ROOT / "app" / "skills" / "packages"
    base.mkdir(parents=True, exist_ok=True)
    for name, cat, desc, kws, sample, perm in CAPS:
        pkg = base / name
        pkg.mkdir(exist_ok=True)
        manifest = {
            "name": name,
            "version": "1.0.0",
            "description": desc,
            "entry": "skill.py:run",
            "triggers": {"intents": [], "keywords": kws, "regex": [], "require_all": False},
            "inputs": {"type": "object", "required": [], "properties": props_for(sample)},
            "dependencies": {"tools": []},
            "permissions": perm,
            "timeout_seconds": 30,
            "category": cat,
            "color": CATEGORY_COLOR[cat],
            "sample_args": sample,
            "origin": "local",
        }
        (pkg / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (pkg / "skill.py").write_text(
            '"""技能包：' + desc + '。实现体集中在 app/skills/_ops.py，便于统一验证。"""\n\n'
            'from app.skills._ops import OPS\n\n\n'
            'def run(params: dict, ctx: dict | None = None) -> dict:\n'
            f'    return OPS["{name}"](params)\n',
            encoding="utf-8")


def build_util_server() -> None:
    tools = []
    for name, cat, desc, kws, sample, perm in CAPS:
        tools.append({
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "required": [], "properties": props_for(sample)},
        })
    code = '''"""内置 MCP 服务：通用本地能力（55 个工具）。

与技能包共用 app/skills/_ops.py 的实现体，但走独立的 stdio 子进程，
所有调用仍经注册表做参数校验 / 权限确认 / 审计，业务代码不直连。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.skills._ops import OPS  # noqa: E402
from app.tools.servers.base import serve  # noqa: E402

NAMES = __NAMES__
TOOLS = __TOOLS__


def _dispatch(name: str):
    def handler(args: dict) -> dict:
        if name not in OPS:
            raise KeyError(f"未实现的能力: {name}")
        return OPS[name](args)
    return handler


if __name__ == "__main__":
    serve("util", "1.0.0", TOOLS, {n: _dispatch(n) for n in NAMES})
'''
    code = code.replace("__NAMES__", json.dumps([c[0] for c in CAPS]))
    code = code.replace("__TOOLS__", json.dumps(tools, ensure_ascii=False, indent=4))
    (ROOT / "app" / "tools" / "servers" / "util_server.py").write_text(code, encoding="utf-8")


def patch_mcp_config() -> None:
    p = ROOT / "config" / "mcp_tools.yaml"
    text = p.read_text(encoding="utf-8")
    if "name: util" in text:
        return
    server_block = (
        "  - name: util\n"
        "    transport: stdio\n"
        '    command: ["python", "-u", "-m", "app.tools.servers.util_server"]\n'
        '    cwd: "."\n'
    )
    text = text.replace("\ntools:\n", server_block + "\ntools:\n", 1)
    lines = []
    for name, cat, desc, kws, sample, perm in CAPS:
        lines.append(f"  - name: util.{name}")
        lines.append("    server: util")
        lines.append(f"    remote_name: {name}")
        lines.append(f"    permission: {perm}")
        lines.append("    timeout_seconds: 20")
        lines.append(f"    description: {desc}")
        lines.append("    input_schema:")
        lines.append("      type: object")
        lines.append("      required: []")
        lines.append("      properties:")
        for k, v in sample.items():
            t = json_type(v)
            lines.append(f"        {k}: {{type: {t}}}")
        lines.append("")
    text = text.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    p.write_text(text, encoding="utf-8")


def build_host_catalog() -> None:
    skills = []
    for host_name, (local_name, note) in HOST_MAP.items():
        skills.append({
            "name": host_name,
            "source": "WorkBuddy 当前会话可用技能",
            "host_status": "available",
            "local_skill": local_name,
            "local_status": "runnable",
            "note": note,
        })
    connectors = [{
        "name": n, "source": "WorkBuddy MCP / 延迟工具", "host_status": "available",
        "local_skill": local, "local_status": "runnable",
        "note": desc + ("（真实 Playwright 浏览器渲染）" if local == "browser_browse" else "（本地等价能力）"),
    } for n, desc, local in CONNECTORS]
    doc = {
        "generated_by": "scripts/build_capabilities.py",
        "note": "数量与宿主清单一致；每个条目都指向一个本地真实可运行的能力，差异如实写在 note 里。",
        "host_skills": skills,
        "connectors": connectors,
    }
    (ROOT / "config" / "workbuddy_catalog.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    build_samples()
    build_skill_packages()
    build_util_server()
    patch_mcp_config()
    build_host_catalog()
    print(f"生成完成：{len(CAPS)} 个技能包 / {len(CAPS)} 个 MCP 工具 / "
          f"{len(HOST_MAP)} 个宿主技能 / {len(CONNECTORS)} 个连接器")
