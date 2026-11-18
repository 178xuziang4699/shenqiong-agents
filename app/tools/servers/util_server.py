"""内置 MCP 服务：通用本地能力（55 个工具）。

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

NAMES = ["text_summarize", "text_wordcount", "text_keywords", "text_diff", "text_regex", "text_replace", "text_case", "text_humanize", "text_glossary", "text_chunk", "text_dedupe", "text_sentiment", "json_query", "json_validate", "json_pretty", "csv_profile", "csv_filter", "csv_to_markdown", "yaml_to_json", "xml_to_dict", "html_extract", "markdown_to_html", "table_sort", "stats_describe", "hash_digest", "base64_codec", "url_codec", "uuid_gen", "password_gen", "jwt_payload", "color_convert", "cron_explain", "semver_compare", "regex_explain", "pdf_extract", "pdf_info", "docx_inspect", "xlsx_profile", "pptx_outline", "file_tree", "image_info", "image_thumb", "chart_bar", "report_html", "datetime_tool", "sys_info", "env_whitelist", "calendar_gen", "math_eval", "unit_convert", "geo_solar", "cron_next", "file_diff", "dir_checksum", "kb_lookup"]
TOOLS = [
    {
        "name": "text_summarize",
        "description": "按句子信息密度抽取要点摘要",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "max_points": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "text_wordcount",
        "description": "统计中英文字数、行数与高频词",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                }
            }
        }
    },
    {
        "name": "text_keywords",
        "description": "基于词频抽取关键词",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "top_k": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "text_diff",
        "description": "生成两段文本的 unified diff 与相似度",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "left": {
                    "type": "string",
                    "description": "参数 left"
                },
                "right": {
                    "type": "string",
                    "description": "参数 right"
                }
            }
        }
    },
    {
        "name": "text_regex",
        "description": "按正则抽取匹配片段",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "pattern": {
                    "type": "string",
                    "description": "参数 pattern"
                }
            }
        }
    },
    {
        "name": "text_replace",
        "description": "按术语映射批量替换",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "mapping": {
                    "type": "object"
                }
            }
        }
    },
    {
        "name": "text_case",
        "description": "命名风格转换 snake / camel / kebab / upper",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "style": {
                    "type": "string",
                    "description": "参数 style"
                }
            }
        }
    },
    {
        "name": "text_humanize",
        "description": "去除常见 AI 套话，压缩冗余表述",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                }
            }
        }
    },
    {
        "name": "text_glossary",
        "description": "按术语表做确定性翻译替换",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "glossary": {
                    "type": "object"
                }
            }
        }
    },
    {
        "name": "text_chunk",
        "description": "按长度与重叠度切分长文本",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "size": {
                    "type": "integer"
                },
                "overlap": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "text_dedupe",
        "description": "按行去重并保持原顺序",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                }
            }
        }
    },
    {
        "name": "text_sentiment",
        "description": "基于情感词典做倾向打分",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                }
            }
        }
    },
    {
        "name": "json_query",
        "description": "按点分路径读取 JSON 字段",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "json": {
                    "type": "string",
                    "description": "参数 json"
                },
                "path_expr": {
                    "type": "string",
                    "description": "参数 path_expr"
                }
            }
        }
    },
    {
        "name": "json_validate",
        "description": "校验必填字段是否齐全",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "json": {
                    "type": "string",
                    "description": "参数 json"
                },
                "required": {
                    "type": "array"
                }
            }
        }
    },
    {
        "name": "json_pretty",
        "description": "格式化 JSON 并输出摘要",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "json": {
                    "type": "string",
                    "description": "参数 json"
                }
            }
        }
    },
    {
        "name": "csv_profile",
        "description": "CSV 列统计与数值摘要",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "csv_filter",
        "description": "按列值条件筛选 CSV 行",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                },
                "column": {
                    "type": "string",
                    "description": "参数 column"
                },
                "op": {
                    "type": "string",
                    "description": "参数 op"
                },
                "value": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "csv_to_markdown",
        "description": "CSV 转 Markdown 表格",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "yaml_to_json",
        "description": "YAML 解析并转 JSON",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "xml_to_dict",
        "description": "XML 解析为结构树",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "html_extract",
        "description": "提取 HTML 正文、标题与链接",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "markdown_to_html",
        "description": "Markdown 渲染为 HTML",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                }
            }
        }
    },
    {
        "name": "table_sort",
        "description": "按指定键排序记录列表",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "rows": {
                    "type": "array"
                },
                "key": {
                    "type": "string",
                    "description": "参数 key"
                },
                "reverse": {
                    "type": "boolean"
                }
            }
        }
    },
    {
        "name": "stats_describe",
        "description": "描述性统计：均值/中位数/标准差",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "values": {
                    "type": "array"
                }
            }
        }
    },
    {
        "name": "hash_digest",
        "description": "计算文本摘要（sha256 / md5 等）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "参数 text"
                },
                "algorithm": {
                    "type": "string",
                    "description": "参数 algorithm"
                }
            }
        }
    },
    {
        "name": "base64_codec",
        "description": "Base64 编码与解码",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "data": {
                    "type": "string",
                    "description": "参数 data"
                },
                "mode": {
                    "type": "string",
                    "description": "参数 mode"
                }
            }
        }
    },
    {
        "name": "url_codec",
        "description": "URL 百分号编码与解码",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "data": {
                    "type": "string",
                    "description": "参数 data"
                },
                "mode": {
                    "type": "string",
                    "description": "参数 mode"
                }
            }
        }
    },
    {
        "name": "uuid_gen",
        "description": "生成 UUID v1 / v4",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "count": {
                    "type": "integer"
                },
                "version": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "password_gen",
        "description": "用安全随机数生成密码并估算熵",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "length": {
                    "type": "integer"
                },
                "symbols": {
                    "type": "boolean"
                }
            }
        }
    },
    {
        "name": "jwt_payload",
        "description": "解码 JWT payload（不验签，仅用于排查）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "token": {
                    "type": "string",
                    "description": "参数 token"
                }
            }
        }
    },
    {
        "name": "color_convert",
        "description": "HEX / RGB / HSV / 亮度换算",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "hex": {
                    "type": "string",
                    "description": "参数 hex"
                }
            }
        }
    },
    {
        "name": "cron_explain",
        "description": "把 cron 表达式翻译成自然语言",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "参数 expression"
                }
            }
        }
    },
    {
        "name": "semver_compare",
        "description": "语义化版本比较",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "a": {
                    "type": "string",
                    "description": "参数 a"
                },
                "b": {
                    "type": "string",
                    "description": "参数 b"
                }
            }
        }
    },
    {
        "name": "regex_explain",
        "description": "逐字符解释正则表达式结构",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "参数 pattern"
                }
            }
        }
    },
    {
        "name": "pdf_extract",
        "description": "用 pdftotext 提取 PDF 正文",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "pdf_info",
        "description": "读取 PDF 页数、尺寸等元信息",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "docx_inspect",
        "description": "读取 Word 段落与表格数",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "xlsx_profile",
        "description": "读取 Excel 工作表结构与预览",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "pptx_outline",
        "description": "提取 PPT 每页文本大纲",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "file_tree",
        "description": "列出目录树（深度与数量受限）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                },
                "depth": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "image_info",
        "description": "读取图片格式、尺寸与色彩模式",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "image_thumb",
        "description": "生成缩略图并落盘到 var/capability_probe",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                },
                "width": {
                    "type": "integer"
                },
                "height": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "chart_bar",
        "description": "用 matplotlib 生成柱状图 PNG",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "labels": {
                    "type": "array"
                },
                "values": {
                    "type": "array"
                }
            }
        }
    },
    {
        "name": "report_html",
        "description": "生成 HTML 报告并落盘",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "title": {
                    "type": "string",
                    "description": "参数 title"
                },
                "body": {
                    "type": "string",
                    "description": "参数 body"
                }
            }
        }
    },
    {
        "name": "datetime_tool",
        "description": "UTC / 本地时间 / 时间戳 / ISO 周",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "tz_offset_hours": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "sys_info",
        "description": "读取当前 Python 与主机环境信息",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {}
        }
    },
    {
        "name": "env_whitelist",
        "description": "仅列出允许的环境变量名（不暴露取值）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {}
        }
    },
    {
        "name": "calendar_gen",
        "description": "生成指定年月的月历矩阵",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "year": {
                    "type": "integer"
                },
                "month": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "math_eval",
        "description": "安全四则运算求值（AST 白名单）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "参数 expression"
                }
            }
        }
    },
    {
        "name": "unit_convert",
        "description": "长度单位换算",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "value": {
                    "type": "integer"
                },
                "from": {
                    "type": "string",
                    "description": "参数 from"
                },
                "to": {
                    "type": "string",
                    "description": "参数 to"
                }
            }
        }
    },
    {
        "name": "geo_solar",
        "description": "按纬度与日期估算昼长（纯天文公式）",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "latitude": {
                    "type": "number"
                },
                "day_of_year": {
                    "type": "integer"
                }
            }
        }
    },
    {
        "name": "cron_next",
        "description": "推算 */N 分钟cron的下几次触发时刻",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "参数 expression"
                }
            }
        }
    },
    {
        "name": "file_diff",
        "description": "比对两个文本文件的差异",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "left": {
                    "type": "string",
                    "description": "参数 left"
                },
                "right": {
                    "type": "string",
                    "description": "参数 right"
                }
            }
        }
    },
    {
        "name": "dir_checksum",
        "description": "计算目录下文件的聚合校验和",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "参数 path"
                }
            }
        }
    },
    {
        "name": "kb_lookup",
        "description": "检索知识库并返回带来源引用的结果",
        "inputSchema": {
            "type": "object",
            "required": [],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "参数 query"
                },
                "top_k": {
                    "type": "integer"
                }
            }
        }
    }
]


def _dispatch(name: str):
    def handler(args: dict) -> dict:
        if name not in OPS:
            raise KeyError(f"未实现的能力: {name}")
        return OPS[name](args)
    return handler


if __name__ == "__main__":
    serve("util", "1.0.0", TOOLS, {n: _dispatch(n) for n in NAMES})
