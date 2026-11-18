"""能力目录的中文展示层：把英文技术 id 映射成中文名 + 图标，供三个接口统一注入。

为什么单独一层：能力实现体必须保持稳定的英文 id（注册表、manifest、日志都靠它），
但界面要给人看。把翻译放在这里，既不污染业务代码，也不用改动 100+ 个 manifest。
图标用语义 key，具体 SVG 由前端 ICONS 表渲染——避免依赖 emoji（无字体环境会变空方框）。
"""

from __future__ import annotations

# (中文名, 图标 key)：55 个本地能力，与 scripts/build_capabilities.py::CAPS 一一对应
ZH: dict[str, tuple[str, str]] = {
    # 文本处理
    "text_summarize": ("要点摘要", "text"),
    "text_wordcount": ("字数统计", "text"),
    "text_keywords": ("关键词提取", "text"),
    "text_diff": ("文本比对", "text"),
    "text_regex": ("正则抽取", "text"),
    "text_replace": ("术语替换", "text"),
    "text_case": ("命名风格转换", "text"),
    "text_humanize": ("去除 AI 腔", "text"),
    "text_glossary": ("术语表翻译", "text"),
    "text_chunk": ("长文本切分", "text"),
    "text_dedupe": ("按行去重", "text"),
    "text_sentiment": ("情感倾向打分", "text"),
    # 数据结构化
    "json_query": ("JSON 路径查询", "table"),
    "json_validate": ("JSON 字段校验", "table"),
    "json_pretty": ("JSON 格式化", "table"),
    "csv_profile": ("CSV 列统计", "table"),
    "csv_filter": ("CSV 条件筛选", "table"),
    "csv_to_markdown": ("CSV 转表格", "table"),
    "yaml_to_json": ("YAML 转 JSON", "table"),
    "xml_to_dict": ("XML 结构解析", "table"),
    "html_extract": ("HTML 正文提取", "table"),
    "markdown_to_html": ("Markdown 渲染", "table"),
    "table_sort": ("记录排序", "table"),
    "stats_describe": ("描述性统计", "table"),
    # 编码与安全
    "hash_digest": ("哈希摘要", "lock"),
    "base64_codec": ("Base64 编解码", "lock"),
    "url_codec": ("URL 编解码", "lock"),
    "uuid_gen": ("UUID 生成", "lock"),
    "password_gen": ("安全密码生成", "lock"),
    "jwt_payload": ("JWT 内容解码", "lock"),
    "color_convert": ("色值换算", "lock"),
    "cron_explain": ("定时规则解释", "clock"),
    "semver_compare": ("版本号比较", "lock"),
    "regex_explain": ("正则逐字解释", "lock"),
    # 文档与文件
    "pdf_extract": ("PDF 正文提取", "file"),
    "pdf_info": ("PDF 元信息", "file"),
    "docx_inspect": ("Word 结构读取", "file"),
    "xlsx_profile": ("Excel 结构读取", "file"),
    "pptx_outline": ("PPT 大纲提取", "file"),
    "file_tree": ("目录树扫描", "folder"),
    "image_info": ("图片元信息", "image"),
    "image_thumb": ("缩略图生成", "image"),
    "chart_bar": ("柱状图生成", "chart"),
    "report_html": ("HTML 报告生成", "file"),
    # 系统与工具
    "datetime_tool": ("时间换算", "clock"),
    "sys_info": ("运行环境信息", "gear"),
    "env_whitelist": ("环境变量白名单", "shield"),
    "calendar_gen": ("月历生成", "clock"),
    "math_eval": ("四则运算求值", "calc"),
    "unit_convert": ("单位换算", "calc"),
    "geo_solar": ("昼长估算", "globe"),
    "cron_next": ("下次触发时刻", "clock"),
    "file_diff": ("文件差异比对", "file"),
    "dir_checksum": ("目录校验和", "shield"),
    # 知识与检索
    "kb_lookup": ("知识库检索", "search"),
    # 代码工程（办事能力）
    "code_generate": ("代码生成", "code"),
    "code_check": ("代码语法自检", "shield"),
    # 办公交付（本次补齐：真实产出，非空壳）
    "doc_write": ("办公文档写作", "file"),
    "research_report": ("调研报告生成", "search"),
    "web_fetch": ("网页正文抓取", "globe"),
    "table_csv": ("CSV 表格生成", "table"),
    "data_profile": ("数值统计分析", "chart"),
}

# 编排链原有的 7 个 MCP 工具与 2 个技能包
NATIVE_TOOLS: dict[str, tuple[str, str]] = {
    "fs.read_text": ("读取文本文件", "file"),
    "fs.write_text": ("写入文本文件", "file"),
    "fs.list_dir": ("列出目录", "folder"),
    "fs.file_info": ("文件元信息", "file"),
    "calc.word_count": ("统计字数", "text"),
    "calc.eval": ("四则运算", "calc"),
    "kb.search": ("知识库检索", "search"),
    "kb.ingest": ("文档入库", "search"),
    "util.math_eval": ("四则运算求值", "calc"),
}

NATIVE_SKILLS: dict[str, tuple[str, str]] = {
    "summarize_notes": ("笔记要点整理", "text"),
    "compose_report": ("报告成文", "file"),
}

# 宿主技能（WorkBuddy 当前会话可用清单）的中文名；未列出的用中文说明兜底
HOST_ZH: dict[str, str] = {
    "automation-task-manager": "定时任务管理",
    "computer-use": "电脑操作自动化",
    "figma-connector": "Figma 设计协作",
    "github-connector": "GitHub 代码协作",
    "gongfeng-enterprise-connector": "工蜂代码协作",
    "library": "资料库与知识库",
    "发布为应用": "发布为在线应用",
    "diagnose": "问题诊断",
    "whobot-phonecall-skill": "AI 电话数字员工",
    "workbuddy-local-office-edit": "本地 Office 编辑",
    "workbuddy-office-routing": "Office 任务路由",
    "agent-browser": "浏览器自动化",
    "docx": "Word 文档处理",
    "pdf": "PDF 文档处理",
    "pptx": "演示文稿处理",
    "xlsx": "表格处理",
    "agentmail": "智能体邮箱",
    "apple-notes": "备忘录管理",
    "apple-reminders": "提醒事项管理",
    "blogwatcher": "博客订阅监控",
    "gifgrep": "动图搜索",
    "github": "GitHub 命令行",
    "gog": "Google 工作区",
    "healthcheck": "健康打卡",
    "himalaya": "邮件客户端",
    "humanizer": "文本去 AI 腔",
    "imsg": "短信与 iMessage",
    "mcporter": "MCP 连接器管理",
    "nano-banana-pro": "AI 图像生成",
    "nano-pdf": "PDF 自然语言编辑",
    "obsidian": "Obsidian 笔记库",
    "openai-image-gen": "批量图像生成",
    "openai-whisper": "语音转文字",
    "openai-whisper-api": "语音转文字 API",
    "oracle": "第二模型评审",
    "peekaboo": "macOS 界面自动化",
    "qmd": "本地笔记检索",
    "sag": "语音合成",
    "skill-creator": "技能创建向导",
    "skill-vetter": "技能安全审查",
    "songsee": "音频频谱可视化",
    "summarize": "内容摘要",
    "things-mac": "Things 待办管理",
    "tmux": "终端会话控制",
    "trello": "Trello 看板",
    "video-frames": "视频抽帧",
    "wacli": "WhatsApp 消息",
    "weather": "天气查询",
    "xiaohongshu": "小红书内容工具",
    "xurl": "Twitter 内容分析",
    "workbuddy-docx": "专业 Word 创作",
    "workbuddy-pptx": "专业 PPT 创作",
    "workbuddy-sheetagent": "表格智能体",
    "tdoc-orchestrator": "文档创作编排",
    "ImageGen": "图像生成",
    "VideoGen": "视频生成",
    "LSP": "代码智能感知",
    "library_search": "资料库语义检索",
    "library_export": "资料库导出",
}

EXPERT_ZH: dict[str, tuple[str, str]] = {
    "supervisor": ("总控 Agent", "brain"),
    "mgr_content": ("内容管理者", "brain"),
    "mgr_data": ("数据管理者", "brain"),
    "exec_text": ("文本执行者", "gear"),
    "exec_tool": ("工具执行者", "gear"),
}

CATEGORY_STYLE: dict[str, tuple[str, str]] = {
    "文本处理": ("text", "#2563eb"),
    "数据结构化": ("table", "#7c3aed"),
    "编码与安全": ("lock", "#0d9488"),
    "文档与文件": ("file", "#d97706"),
    "系统与工具": ("gear", "#475569"),
    "知识与检索": ("search", "#db2777"),
    "自定义": ("spark", "#0891b2"),
}

BADGE_ZH = {
    "skill": "本地技能", "tool": "MCP 工具", "expert": "智能体",
    "host": "宿主技能", "connector": "宿主连接器",
}


def _host_title(name: str, row: dict) -> str:
    if name in HOST_ZH:
        return HOST_ZH[name]
    # 中文说明里括号前的一段作为可读标题，保证宿主卡片也不会只剩英文
    note = str(row.get("note") or "").strip()
    if note:
        head = note.split("（")[0].split("(")[0].strip()
        if head and not head.isascii():
            return head[:16]
    return f"宿主能力 · {name[:18]}"


def enrich(row: dict, kind: str) -> dict:
    """给一行能力数据补上展示字段，原始字段一律保留（业务与日志仍用英文 id）。"""
    name = str(row.get("name") or row.get("id") or "")
    category = str(row.get("category") or "")
    cat_icon, cat_color = CATEGORY_STYLE.get(category, ("spark", "#0891b2"))

    if kind == "skill":
        zh, icon = ZH.get(name) or NATIVE_SKILLS.get(name) or (name, cat_icon)
        color = cat_color
        desc = row.get("description") or ""
    elif kind == "tool":
        base = name.split(".", 1)[-1] if "." in name else name
        found = NATIVE_TOOLS.get(name) or ZH.get(base) or NATIVE_TOOLS.get(base)
        zh, icon = found or (name, "plug")
        perm = row.get("permission")
        write = perm in ("write", "dangerous")
        color = "#dc2626" if write else "#0d9488"
        desc = row.get("description") or ""
    elif kind == "expert":
        zh, icon = EXPERT_ZH.get(name) or (name, "brain")
        color = "#2563eb"
        desc = row.get("description") or ""
        category = str(row.get("role") or "编排节点")
    elif kind in ("host", "connector"):
        zh = _host_title(name, row)
        icon = "cloud" if kind == "connector" else "spark"
        color = "#0d9488" if kind == "connector" else "#7c3aed"
        desc = row.get("note") or row.get("description") or ""
        category = "宿主连接器" if kind == "connector" else "宿主技能"
    else:
        zh, icon, color = name, "spark", "#475569"
        desc = row.get("description") or ""

    return {
        **row,
        "display_name": zh,
        "icon": icon,
        "icon_color": color,
        "zh_desc": desc,
        "zh_category": category or "未分类",
        "badge_zh": BADGE_ZH.get(kind, "能力"),
    }
