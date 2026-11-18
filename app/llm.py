"""模型调用层：Ollama / OpenAI 兼容端点，配置驱动；未配置时走离线规则应答。

设计取舍（可运行 / 可观测 / 可恢复）：
- 双协议：Ollama 原生 /api/chat 与 OpenAI 兼容 /v1/chat/completions，由
  AP_LLM_PROVIDER（auto|ollama|openai）与 URL 形状自动判定，业务层零感知。
- 自动挑最强：未指定 AP_LLM_MODEL 时拉 /api/tags，优先 DeepSeek 系列，
  再按参数规模（parameter_size）取最大；选择理由通过 describe() 暴露，可查。
- 多轮：answer() 接收 history（来自落盘快照），原样传给模型；系统提示词
  明确禁止固定开场白与答非所问，知识库命中只作为「参考资料」由模型组织语言。
- 失败不静默：任何网络/结构异常抛 LLMError，由 supervisor 写 WARNING 日志并
  降级到离线应答，界面会显示「已降级」，不会假装模型还在。
- 不在本层打日志：结构化日志强制 session_id/agent_id/step_index，统一由
  supervisor 在 step 上下文里写，避免日志契约被破坏。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from app.errors import AgentPlatformError


class LLMError(AgentPlatformError):
    error_code = "LLM_ERROR"


SYSTEM_PROMPT = (
    "你是 Agent Platform 的智能助手，正在和用户进行简体中文对话。\n"
    "必须遵守：\n"
    "1. 直接回应用户当前这句话，不要每次重复自我介绍或固定开场白。\n"
    "2. 不要答非所问：闲聊就自然闲聊，问能力就答能力，问资料才用资料。\n"
    "   用户只是打招呼、寒暄或闲聊时，就自然接话，绝不主动把话题引到平台介绍、"
    "功能说明，也不要反问「想了解哪方面」「有什么可以帮你」之类的引导语。\n"
    "3. 若消息里带「参考资料」，只在与问题确实相关时采用，并用自己的话组织成"
    "连贯回答；不相关就完全忽略、正常回答，严禁罗列无关条文。\n"
    "4. 没有可靠依据时如实说明，不编造事实、不编造引用。\n"
    "5. 简洁自然，一般 2~6 句；确需列举才用短列表。\n"
    "6. 你能做的事：闲聊问答、写文档 / 调研 / 写代码等办公产出、基于已入库资料回答；"
    "只有用户主动问起平台能力时才介绍，不要主动推荐或引导。"
)

_SIZE_RE = re.compile(r"([\d.]+)\s*([BbMmTtKk])")
_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _param_count(text: str) -> float:
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _UNIT.get(m.group(2).upper(), 1)
    except ValueError:
        return 0.0


class LLM:
    def __init__(self, settings, log=None):
        self.settings = settings
        self.cfg = settings.llm or {}
        self.log = log
        self._models_cache: list[dict] | None = None
        self._selected: str | None = None
        self._reason: str = ""

    # ==================== 配置与状态 ====================
    @property
    def base_url(self) -> str:
        return (self.cfg.get("base_url") or "").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def provider(self) -> str:
        p = str(self.cfg.get("provider") or "auto").lower()
        if p in ("ollama", "openai"):
            return p
        u = self.base_url.lower()
        if u.endswith("/v1") or "/v1/" in u or "openai" in u:
            return "openai"
        return "ollama"

    @property
    def api_key(self) -> str:
        return self.cfg.get("api_key") or ""

    @property
    def timeout(self) -> float:
        return float(self.cfg.get("timeout", 60))

    def _request(self, path: str, payload: dict | None = None, method: str = "POST",
                 timeout: float | None = None) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=(timeout or self.timeout)) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"模型服务请求失败（{url}）：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型服务返回非 JSON（{url}）：{exc}") from exc

    def list_models(self) -> list[dict]:
        """Ollama /api/tags：只用于自动选型与界面展示，不参与业务调用。"""
        if self._models_cache is not None:
            return self._models_cache
        if not self.enabled or self.provider != "ollama":
            self._models_cache = []
            return self._models_cache
        try:
            data = self._request("/api/tags", None, "GET", timeout=3)
        except LLMError:
            self._models_cache = []
            return self._models_cache
        out = []
        for m in data.get("models", []) or []:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            details = m.get("details") or {}
            out.append({
                "name": name,
                "size": int(m.get("size") or 0),
                "params": _param_count(str(details.get("parameter_size") or "")),
                "family": str(details.get("family") or ""),
            })
        self._models_cache = out
        return out

    @property
    def model(self) -> str:
        configured = str(self.cfg.get("model") or "").strip()
        if configured:
            self._selected = configured
            if not self._reason:
                self._reason = "由 AP_LLM_MODEL 显式指定"
            return configured
        if self._selected:
            return self._selected
        if not (self.enabled and self.cfg.get("auto_select", True)):
            self._selected = "gpt-4o-mini"
            self._reason = "未开启自动选型，使用默认模型名"
            return self._selected
        models = self.list_models()
        if not models:
            self._selected = "gpt-4o-mini"
            self._reason = "未取到 Ollama 模型清单，回退默认模型名"
            return self._selected
        deep = [m for m in models if "deepseek" in m["name"].lower()]
        pool = deep or models
        best = max(pool, key=lambda m: (m["params"], m["size"]))
        self._selected = best["name"]
        scope = "DeepSeek 系列中" if deep else "全部模型中"
        size = f"{best['params'] / 1e9:.1f}B" if best["params"] else "规模未知"
        self._reason = f"自动挑选：{scope}参数规模最大（{size}）的 {best['name']}"
        return self._selected

    def set_model(self, name: str) -> None:
        """运行时切换模型（仅本次进程有效；固化请写 AP_LLM_MODEL 环境变量）。"""
        self.cfg["model"] = name.strip()
        self._selected = None
        self._reason = "由界面手动指定（重启后以环境变量为准）"
        self._verified = False
        self._verify_error = ""

    def describe(self) -> dict:
        models = self.list_models()
        # Ollama 有 /api/tags 可探活；OpenAI 兼容端点没有，只能靠实际调用验证
        reachable = (bool(models) if self.provider == "ollama" else True) if self.enabled else False
        return {
            "enabled": self.enabled,
            "reachable": reachable,
            "provider": self.provider if self.enabled else "offline",
            "model": self.model if self.enabled else "离线规则应答",
            "base_url": self.base_url or "-",
            "auto_reason": self._reason,
            "available_models": [m["name"] for m in models],
            "has_api_key": bool(self.api_key),
            "verified": getattr(self, "_verified", False),
            "verify_error": getattr(self, "_verify_error", ""),
        }

    def invalidate(self) -> None:
        """配置变更后清掉选型与验证缓存，避免拿旧状态冒充新配置。"""
        self._models_cache = None
        self._selected = None
        self._reason = ""
        self._verified = False
        self._verify_error = ""

    def probe(self) -> dict:
        """真实连通性测试：发一句极短对话，验证地址 / 密钥 / 模型是否都可用。

        结果只缓存在进程内存，配置变更或进程重启即失效；不落盘、不入库、不写日志。
        """
        if not self.enabled:
            self._verified, self._verify_error = False, "尚未配置服务地址"
            return {"ok": False, "error": self._verify_error}
        try:
            reply = self.chat(
                [{"role": "user", "content": "只回复两个字：可用"}], temperature=0
            )
        except LLMError as exc:
            self._verified, self._verify_error = False, str(exc)
            return {"ok": False, "error": str(exc), "provider": self.provider,
                    "model": self.model}
        self._verified, self._verify_error = True, ""
        return {"ok": True, "provider": self.provider, "model": self.model,
                "reply": reply[:60]}

    # ==================== 真实模型调用 ====================
    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        if not self.enabled:
            raise LLMError("未配置 AP_LLM_BASE_URL，不能调用模型")
        if self.provider == "ollama":
            data = self._request("/api/chat", {
                "model": self.model, "messages": messages, "stream": False,
                "options": {"temperature": temperature},
            })
            try:
                return str(data["message"]["content"]).strip()
            except (KeyError, TypeError) as exc:
                raise LLMError(f"Ollama 返回结构异常：{str(data)[:300]}") from exc
        suffix = "" if self.base_url.endswith("/v1") else "/v1"
        data = self._request(suffix + "/chat/completions", {
            "model": self.model, "messages": messages, "temperature": temperature,
        })
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"OpenAI 兼容端点返回结构异常：{str(data)[:300]}") from exc

    # ==================== 办公写作（调研报告 / 周报 / 方案 …）====================
    OFFICE_SYSTEM_PROMPT = (
        "你是一名资深的中文办公助理与行业研究员。用户会给你一句办公需求，"
        "请直接写出一份完整、专业、可交付的中文 Markdown 文档。\n"
        "严格要求：\n"
        "1. 只输出文档正文，不要任何解释、开场白、结语，不要 Markdown 代码块围栏。\n"
        "2. 第一行是一级标题（# 标题），之后用若干二级标题（## 一、…）分章节；"
        "至少 6 个章节，结构完整、层次清晰。\n"
        "3. 合理使用有序/无序列表与 Markdown 表格，让内容便于阅读和直接用于汇报。\n"
        "4. 内容要具体、有信息量、可执行，紧扣用户给的主题，不要写空话套话，"
        "不要输出 JSON、命令行、字段名或内部技术标识。\n"
        "5. 涉及市场规模、份额、增速、公司名单等具体事实，若不能确定，"
        "明确标注「待核实」，绝不编造数字、机构或引用来源。\n"
        "6. 文末给出「结论与建议」和「待核实事项」两节。\n"
        "7. 全文使用简体中文，篇幅 900~1800 字。\n"
    )

    def write_document(self, request: str, doc_type_zh: str, topic: str) -> str:
        """办公引擎调用：由模型撰写完整文档；超时与异常由调用方降级处理。"""
        if not self.enabled:
            raise LLMError("未配置模型服务，无法撰写文档")
        user = (f"文档类型：{doc_type_zh}\n主题：{topic}\n"
                f"用户原始需求：{request}\n\n请直接输出这份完整的中文 Markdown 文档。")
        if self.provider == "ollama":
            data = self._request("/api/chat", {
                "model": self.model,
                "messages": [{"role": "system", "content": self.OFFICE_SYSTEM_PROMPT},
                             {"role": "user", "content": user}],
                "stream": False, "options": {"temperature": 0.4, "num_predict": 3000},
            }, timeout=180)
            try:
                return str(data["message"]["content"]).strip()
            except (KeyError, TypeError) as exc:
                raise LLMError("Ollama 文档生成返回结构异常") from exc
        suffix = "" if self.base_url.endswith("/v1") else "/v1"
        data = self._request(suffix + "/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": self.OFFICE_SYSTEM_PROMPT},
                         {"role": "user", "content": user}],
            "temperature": 0.4, "max_tokens": 4000,
        }, timeout=180)
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("OpenAI 兼容端点文档生成返回结构异常") from exc

    # ==================== 写代码办事能力 ====================
    CODE_SYSTEM_PROMPT = (
        "你是一名资深工程师。根据用户的需求生成一份可直接运行的完整代码。\n"
        "严格要求：\n"
        "1. 只输出代码本身，不要任何解释、不要 Markdown 代码块围栏、不要写结语。\n"
        "2. 包含必要的 import、主逻辑函数、类型注解、中文 docstring 与关键注释。\n"
        "3. 结尾带可运行示例入口（Python 用 if __name__ == '__main__':）。\n"
        "4. 只使用标准库，不要引入未声明的第三方包。\n"
        "5. 需求里提到测试时，附上 unittest 用例。\n"
    )

    def generate_code(self, spec: str, language: str = "python",
                      filename: str = "output/main.py") -> tuple[str, str]:
        """返回 (代码, 来源)。接模型走模型；离线走确定性模板，并如实标注来源。"""
        if self.enabled:
            try:
                raw = self.chat([
                    {"role": "system", "content": self.CODE_SYSTEM_PROMPT},
                    {"role": "user", "content":
                        f"语言：{language}\n目标文件：{filename}\n需求：{spec}"},
                ], temperature=0.2)
                code = self._strip_fence(raw)
                if len(code.strip()) > 20:
                    return code, "model"
            except LLMError:
                pass  # 模型不可用 → 降级；来源标为 template，不冒充模型生成
        return self.template_code(spec, language, filename), "template"

    @staticmethod
    def _strip_fence(text: str) -> str:
        t = (text or "").strip()
        if t.startswith("```"):
            lines = t.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines)
        return t.strip() + "\n"

    @staticmethod
    def template_code(spec: str, language: str, filename: str) -> str:
        """离线兜底：按需求关键词产出真实可运行的代码，不是占位文本。"""
        lang = (language or "python").lower()
        note = (spec or "").strip().replace("\n", " ")[:120]
        header = (
            "# -*- coding: utf-8 -*-\n"
            '"""由 Agent Platform 离线模板生成（当前未接入模型）。\n\n'
            f"需求：{note}\n"
            f"目标文件：{filename}\n"
            "接入模型 API 后，同一办事流程会由模型按需求生成代码。\n"
            '"""\n'
        )
        if lang in ("javascript", "js", "node"):
            return header + _JS_TEMPLATE
        if lang in ("html", "html5"):
            return header + _HTML_TEMPLATE
        if lang not in ("python", "py"):
            return header + '"""该语言暂无离线模板，请接入模型 API 后重试。"""\nprint("unsupported")\n'
        low = (spec or "").lower()
        if "斐波" in low or "fib" in low:
            body = _PY_FIB
        elif "排序" in low or "sort" in low:
            body = _PY_SORT
        elif "csv" in low or "json" in low or "文件" in low:
            body = _PY_FILE
        else:
            body = _PY_GENERIC
        return header + body

    # ==================== 对话模式应答 ====================
    def answer(self, question: str, evidence: list[dict] | None = None,
               history: list[dict] | None = None) -> str:
        evidence = evidence or []
        history = history or []
        if not self.enabled:
            return self.rule_answer(question, evidence, history)

        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in history[-24:]:
            role, content = h.get("role"), h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
        user_text = question
        if evidence:
            blocks = "\n\n".join(
                f"[{i}] 来源 {hit.get('citation', '')}\n{str(hit.get('content', '')).strip()[:500]}"
                for i, hit in enumerate(evidence[:3], start=1)
            )
            user_text = (
                f"参考资料（仅在与问题确实相关时采用，不相关就忽略）：\n{blocks}\n\n"
                f"用户问题：{question}"
            )
        messages.append({"role": "user", "content": user_text})
        return self.chat(messages)

    @staticmethod
    def needs_knowledge(question: str) -> bool:
        """只有明确的资料查询才检索知识库。

        收窄这条判定是「不答非所问」的关键：寒暄、身份、能力询问一律不检索，
        否则会把报销制度条文硬塞给问「你会做什么」的用户。
        """
        q = question.strip()
        if not q:
            return False
        exclude = ("你是谁", "你叫什么", "能做什么", "会什么", "有什么功能",
                   "能帮我干什么", "介绍一下你自己", "你是", "支持哪些")
        if any(k in q for k in exclude):
            return False
        strong = ("规定", "制度", "政策", "标准是什么", "流程", "审批", "怎么申请",
                  "如何报销", "有哪几个", "有哪些要求", "查一下", "查询", "检索",
                  "资料里", "文档里", "知识库", "依据", "额度", "第几条")
        if any(k in q for k in strong):
            return True
        # 指代性追问：指向上一轮刚产出的内容，答案在对话历史里，不该翻资料库。
        # 否则会把无关示例文档塞进 prompt，模型只好先解释「参考资料无关」，显得答非所问。
        anaphora = ("这份", "上一轮", "刚才", "上面", "前面", "这篇", "那份",
                    "那个报告", "主要结论", "总结一下", "接着说", "继续说",
                    "展开说说", "详细说", "再说说", "其中的", "它的")
        if any(k in q for k in anaphora):
            return False
        # 泛问号兜底必须真的在问资料类事实，寒暄/闲聊不检索
        if ("?" in q or "？" in q) and len(q) >= 8:
            topic_words = ("报销", "制度", "规定", "资料", "文档", "条款", "流程",
                           "节点", "时限", "金额", "额度", "标准", "要求", "政策", "条目")
            return any(k in q for k in topic_words)
        return False

    # ============ 模型驱动：意图理解 / 动态子任务 / 办事结果汇总 ============
    INTENT_SYSTEM = (
        "你是任务分流器，判断用户这句话希望系统做什么。只输出一行 JSON，"
        "不要解释、不要 Markdown 围栏。格式：\n"
        '{"mode":"chat|task|office","intent":"不超过12字的意图短语","reason":"一句话理由"}\n'
        "判定标准：\n"
        "1. chat —— 用户想要的是一段回答、解释、比较、评价、观点、建议、闲聊或追问。"
        "即使句子里出现「请」「帮我」「分析一下」「对比一下」「比较」，只要没有要求"
        "系统改动文件或执行一串工具，一律判 chat。例如「请问小李飞刀和东方不败谁厉害」"
        "「帮我分析一下这两个方案的优劣」都是 chat。\n"
        "2. task —— 明确要求系统执行会读写文件、调用工具链、生成并落盘代码，"
        "或按流程跑多个步骤的操作。强信号：「写入」「保存到」「存到」「落盘」"
        "「生成到 output/」「把 X 文件整理成 Y 文件」、出现具体文件路径"
        "（samples/xxx、output/xxx.py）且要求产出文件。"
        "「写一个 Python 函数保存到 output/fib.py」是 task。\n"
        "3. office —— 明确要求产出一份可直接交付的中文文档：报告、周报、方案、"
        "调研、总结、纪要、说明书、立项书等，且没有指定具体落盘文件路径。\n"
        "4. 只是提问、求证、比较、闲聊，绝不因为出现动词就判 task 或 office。\n"
        "5. 结合下方「最近的对话」理解追问所指；指代上一轮内容的问题仍是 chat。\n"
    )

    # 通道内动作判定：只决定「这一轮怎么答」，绝不决定用户处于哪个通道。
    # 通道由用户显式选择并随请求传入，消息内容（含任何关键词）都不参与。
    ACTION_SYSTEM = (
        "你只判断用户这一轮话需要「直接回答」，还是需要「实际动手产出」。"
        "只输出一行 JSON，不要解释、不要 Markdown 围栏。格式：\n"
        '{"action":"reply|document|task","reason":"一句话理由"}\n'
        "判定标准（从严，宁可少做事也不要误做事）：\n"
        "1. reply —— 用户想要的是回答、解释、比较、评价、观点、建议、闲聊、追问。"
        "以下词一律不是办事信号，绝不能因为它们就判成 document 或 task："
        "「请」「帮我」「麻烦」「分析一下」「对比一下」「比较一下」「帮我办件事」"
        "「看一下」「说说」「你觉得」「需要你」。例如「请问小李飞刀和东方不败谁厉害」"
        "「帮我分析一下这两个方案的优劣」「请帮我办件事」全是 reply。\n"
        "2. document —— 明确要求产出一份可直接交付的中文文档，且点名了文种："
        "报告、调研报告、周报、方案、立项书、总结、纪要、说明书等。\n"
        "3. task —— 明确要求系统读写文件、落盘、跑工具链或按多步流程办事；"
        "强信号是出现具体文件路径（samples/xxx、output/xxx）并要求产出/写入文件，"
        "或「保存到」「写入」「生成到 output/」「把 X 文件整理成 Y 文件」。\n"
        "4. 只是提问、求证、比较、闲聊，即使带动词和礼貌词，一律 reply。\n"
        "5. 结合下方「最近的对话」理解追问；指代上一轮内容的问题仍是 reply。\n"
        "6. 你只决定这一轮怎么答，绝不决定用户处于哪个通道；"
        "通道由用户自己选择，与消息内容无关。\n"
    )

    PLAN_SYSTEM = (
        "你是任务规划器。把用户的办事需求拆成可执行子任务，只输出 JSON 数组，不要解释。\n"
        '每个元素：{"task_id":"T1","title":"...","assignee":"管理者ID",'
        '"depends_on":[],"pipeline":[{"action":"tool|skill","target":"名称","params":{}}]}\n'
        "硬性约束：\n"
        "1. 2~4 个子任务，task_id 依次 T1、T2…；depends_on 只能引用编号更小的任务。\n"
        "2. assignee 只能从给定管理者清单里选；target 只能从给定工具/技能清单里选，"
        "不得编造不存在的名称。\n"
        "3. pipeline 至少一步；params 必须是对象。\n"
        "4. 用户要求落盘时，最后一步用 fs.write_text 写文件。\n"
        "5. 不输出 Markdown 围栏、注释或多余文字。\n"
    )

    @staticmethod
    def _json_from(text: str):
        """容错解析模型返回的 JSON（允许 ```json 围栏与前后多余文字）。"""
        if not text:
            return None
        s = str(text).strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M).strip()
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            pass
        m = re.search(r"[\{\[][\s\S]*[\}\]]", s)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def understand(self, text: str, history: list[dict] | None = None,
                   rule_hint: dict | None = None) -> dict:
        """模型判定意图；结构异常 / 网络异常一律回退规则结果，不阻塞主流程。"""
        hint = rule_hint or {}
        fallback = {
            "mode": hint.get("mode") or "chat",
            "intent": "",
            "reason": hint.get("reason") or "模型不可用，采用规则判定",
            "source": "rule",
        }
        if not self.enabled:
            return fallback
        messages: list[dict] = [{"role": "system", "content": self.INTENT_SYSTEM}]
        for h in (history or [])[-6:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": str(h["content"])[:400]})
        rule_note = (f"\n（规则引擎初判：{hint.get('mode')}，理由：{hint.get('reason')}。"
                     "规则只是参考，若与上述标准冲突，以你的判断为准。）")
        messages.append({"role": "user", "content": f"用户消息：{text}{rule_note}"})
        try:
            got = self._json_from(self.chat(messages, temperature=0))
            if isinstance(got, dict) and got.get("mode") in ("chat", "task", "office"):
                return {
                    "mode": got["mode"],
                    "intent": str(got.get("intent") or "")[:40],
                    "reason": str(got.get("reason") or "模型判定")[:120],
                    "source": "model",
                }
            fallback["reason"] = "模型返回结构不合法，采用规则判定"
        except Exception as exc:  # noqa: BLE001 - 理解失败必须能降级
            fallback["reason"] = f"模型理解失败（{type(exc).__name__}），采用规则判定"
        return fallback

    def understand_action(self, text: str, history: list[dict] | None = None) -> dict:
        """语义判定本轮动作；结果只影响「怎么答」，不改变通道归属。

        与旧的 understand() 有本质区别：
        - 不输出 mode，不接收也不参考任何关键词规则 hint；
        - 模型不可用 / 结构异常一律降级 reply（直接对话回答），绝不误触发办事；
        - 通道由请求参数显式给定，本方法完全看不到、也改不了它。
        """
        fallback = {"action": "reply", "reason": "模型不可用，保守按对话回答",
                    "source": "fallback"}
        if not self.enabled:
            return fallback
        messages: list[dict] = [{"role": "system", "content": self.ACTION_SYSTEM}]
        for h in (history or [])[-6:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": str(h["content"])[:400]})
        messages.append({"role": "user", "content": f"用户消息：{text}"})
        try:
            got = self._json_from(self.chat(messages, temperature=0))
            if isinstance(got, dict) and got.get("action") in ("reply", "document", "task"):
                return {"action": got["action"],
                        "reason": str(got.get("reason") or "模型判定")[:120],
                        "source": "model"}
            fallback["reason"] = "模型返回结构不合法，保守按对话回答"
        except Exception as exc:  # noqa: BLE001 - 判定失败必须能降级，绝不阻塞对话
            fallback["reason"] = f"模型判定失败（{type(exc).__name__}），保守按对话回答"
        return fallback

    def plan_subtasks(self, request: str, catalog: dict) -> list[dict] | None:
        """让模型生成子任务；白名单校验不通过返回 None，由规划器回退成熟模板。"""
        if not self.enabled:
            return None
        managers = catalog.get("managers") or []
        tools = (catalog.get("tools") or [])[:120]
        skills = (catalog.get("skills") or [])[:120]
        user = (
            f"用户需求：{request}\n\n"
            f"可选管理者：{json.dumps(managers, ensure_ascii=False)}\n"
            f"可用工具：{json.dumps(tools, ensure_ascii=False)}\n"
            f"可用技能：{json.dumps(skills, ensure_ascii=False)}\n"
            "请输出 JSON 数组。"
        )
        try:
            raw = self.chat([
                {"role": "system", "content": self.PLAN_SYSTEM},
                {"role": "user", "content": user},
            ], temperature=0)
            got = self._json_from(raw)
            if isinstance(got, dict):
                got = got.get("subtasks")
            return self._validate_subtasks(
                got, set(managers), set(tools), set(skills))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _validate_subtasks(got, managers: set, tools: set, skills: set) -> list[dict] | None:
        if not isinstance(got, list) or not (2 <= len(got) <= 4):
            return None
        out: list[dict] = []
        seen: set[str] = set()
        for i, t in enumerate(got, start=1):
            if not isinstance(t, dict) or t.get("assignee") not in managers:
                return None
            tid = f"T{i}"
            deps = t.get("depends_on") or []
            if not isinstance(deps, list) or any(d not in seen for d in deps):
                return None
            pipe = t.get("pipeline") or []
            if not isinstance(pipe, list) or not pipe:
                return None
            for step in pipe:
                if not isinstance(step, dict):
                    return None
                act, target = step.get("action"), step.get("target")
                if act not in ("tool", "skill"):
                    return None
                if target not in (tools if act == "tool" else skills):
                    return None
                params = step.get("params")
                if params is None:
                    step["params"] = {}
                elif not isinstance(params, dict):
                    return None
                # 跨步骤引用只能指向编号更小的上游任务，防止引用未来结果
                for ref in re.findall(r"\{\{\s*T(\d+)",
                                      json.dumps(params, ensure_ascii=False)):
                    if int(ref) >= i:
                        return None
            out.append({
                "task_id": tid,
                "title": str(t.get("title") or f"子任务 {tid}")[:60],
                "assignee": t["assignee"],
                "depends_on": deps,
                "pipeline": pipe,
            })
            seen.add(tid)
        return out

    def summarize_outcome(self, request: str, plan: dict, outcome: dict,
                          artifacts: list[dict]) -> str:
        """办事模式收口：把结构化执行结果汇总成用户能直接读的自然语言结论。"""
        tasks = [
            {"id": t.get("task_id"), "title": t.get("title"),
             "assignee": t.get("assignee")}
            for t in (plan or {}).get("subtasks", [])
        ]
        summary = {
            "status": (outcome or {}).get("status"),
            "failed": (outcome or {}).get("failed", []),
            "skipped": (outcome or {}).get("skipped", []),
            "artifacts": artifacts or [],
            "task_count": len(tasks), "tasks": tasks,
        }
        fallback = self._rule_outcome(request, summary)
        if not self.enabled:
            return fallback
        blob = json.dumps(summary, ensure_ascii=False)[:2500]
        try:
            raw = self.chat([
                {"role": "system", "content": (
                    "你在向用户汇报一次多智能体任务的执行结果。用简体中文写 2~5 句，"
                    "先说做完了什么、产出了什么文件，再说失败或跳过的环节（若有）。"
                    "语气自然、像同事汇报，不要罗列 JSON、字段名、命令行或内部标识，"
                    "不要编造未发生的结果，不要写标题，不要 Markdown 列表。")},
                {"role": "user", "content": f"用户原始需求：{request}\n执行结果：{blob}"},
            ], temperature=0.2)
            text = str(raw).strip()
            return text[:600] if text else fallback
        except Exception:  # noqa: BLE001
            return fallback

    @staticmethod
    def _rule_outcome(request: str, summary: dict) -> str:
        arts = summary.get("artifacts") or []
        bits = [f"已按「{request[:40]}」派发 {summary.get('task_count') or 0} 个子任务，"
                f"执行状态 {summary.get('status')}。"]
        if arts:
            bits.append("产出文件：" + "、".join(
                str(a.get("path") or a.get("title") or "") for a in arts[:3]) + "。")
        if summary.get("failed"):
            bits.append("未完成环节：" + "、".join(
                str(f.get("task_id") or "") for f in summary["failed"][:3])
                + "，可在管理页诊断后断点续跑。")
        if summary.get("skipped"):
            bits.append(f"另有 {len(summary['skipped'])} 个环节跳过。")
        return "".join(bits)

    # ==================== 离线兜底（仍保持多轮、不答非所问）====================
    def rule_answer(self, question: str, evidence: list[dict] | None = None,
                    history: list[dict] | None = None) -> str:
        evidence = evidence or []
        history = history or []
        q = question.strip()
        turns = sum(1 for h in history if h.get("role") == "user")
        last_answer = next(
            (str(h["content"]) for h in reversed(history) if h.get("role") == "assistant" and h.get("content")),
            "",
        )

        def pick(options: list[str]) -> str:
            return options[turns % len(options)]

        # 1) 告别
        if any(k in q for k in ("再见", "拜拜", "先走了", "下次聊", "晚安", "bye", "goodbye")):
            return pick(["好，先到这儿。需要办事随时把事情说完整就行。",
                         "聊得挺好，回头见。任务中断了也能从断点续跑，不用重来。"])

        # 2) 感谢 / 客气
        if any(k in q for k in ("谢谢", "感谢", "多谢", "thanks", "thank you")):
            return pick(["不客气。还有想问的，或者要办的事，直接说。",
                         "应该的。你可以继续问，也可以把一件具体的事交给我。"])

        # 3) 身份
        if any(k in q for k in ("你是谁", "你叫什么", "什么平台", "是不是机器人",
                               "你是模型吗", "你是什么")):
            return ("我是这套多智能体平台的总控 Agent。平台分五层：接入、编排、执行、存储、"
                    "可观测；办事时由我拆成子任务，交给管理者二次派发，执行者干活并逐步落盘。"
                    "现在是对话模式，我只答不做。")

        # 4) 能力清单（不含知识库检索，避免答非所问）
        if any(k in q for k in ("能做什么", "会什么", "有什么功能", "能帮我干什么",
                               "支持哪些", "介绍一下", "有哪些能力", "你能干嘛")):
            return ("我能做两类事：\n"
                    "· 直接聊：闲聊、问答、解释这套平台；问已入库的资料会带来源引用。\n"
                    "· 动手办：把一件具体的事说完整（例如「把 notes.md 整理成报告写入 report.md」），"
                    "我会拆子任务 → 管理者二次派发 → 执行者调用 MCP 工具，过程逐步落盘，"
                    "关键写操作前会有人工确认，失败自动重试，中断可断点续跑。\n"
                    "另外「专家技能和浏览器」页里 50+ 个本地技能、60+ 个 MCP 工具都能当场试运行。")

        # 5) 问候（首轮介绍，后续不再重复）
        if any(k in q.lower() for k in ("你好", "您好", "hi", "hello", "嗨", "早上好", "下午好", "晚上好")):
            if turns == 0:
                return "你好。我是总控 Agent，闲聊问答我直接回；有具体要办的事，把事情说完整就行。"
            return pick(["嗯，我在，接着聊。", "还在。想问什么或者要办什么，直接说。"])

        # 6) 闲聊 / 情绪
        if any(k in q for k in ("天气", "心情", "累", "无聊", "玩笑", "笑话", "周末",
                               "吃饭", "吃了吗", "忙不忙", "怎么样啊")):
            return pick(["这个我只能陪你聊两句——我没联网也没天气数据，不过听着挺不错。"
                         "要是想让我干点实际的，把任务说清楚就行。",
                         "收到。闲聊我陪着，真要查资料或者办事，直接说正事，我不会把两者搞混。"])

        # 7) 追问 / 承接上一轮
        if any(k in q for k in ("继续", "然后呢", "还有呢", "详细说说", "展开", "举个例子",
                               "为什么", "真的吗", "是吗", "第一个", "第二个", "再说说")):
            if last_answer:
                head = last_answer.split("\n")[0][:80]
                return (f"接着刚才那句「{head}」说：我这边没有额外的新资料，"
                        "可以把你想深挖的点说得更具体些，或者把任务直接交给我去办——"
                        "那样每一步都会有落盘记录，比口头追问更靠谱。")
            return "想让我展开哪一块？说具体一点，我按那个点接着讲。"

        # 8) 明确的资料查询：把命中组织成回答，而不是罗列无关条文
        if LLM.needs_knowledge(q) and evidence:
            lines = ["我在资料库里查到这些内容：", ""]
            for i, hit in enumerate(evidence[:3], start=1):
                snippet = re.sub(r"[#>*`]+", " ", str(hit.get("content", "")))
                snippet = re.sub(r"\s+", " ", snippet).strip()
                if len(snippet) > 180:
                    snippet = snippet[:180] + "…"
                lines.append(f"{i}. {snippet}")
                lines.append(f"   来源：{hit.get('citation', '')}")
            lines += ["", "以上来自已入库文档，引用可定位到原文切块；需要我整理成文档，把任务说完整即可。"]
            return "\n".join(lines)

        # 9) 被判成对话模式的办事意图：明确引导，不假装做了
        if any(k in q for k in ("帮我写", "帮我整理", "生成报告", "导出", "创建文件",
                               "统计一下", "处理数据", "算一下")):
            return ("这属于办事模式。把事情说完整（包含源文件、目标和输出位置），"
                    "例如「把 samples/notes.md 整理成报告写入 output/report.md」，"
                    "我就会拆成子任务并派发执行。")

        # 10) 兜底：诚实、承接、给可操作引导
        if LLM.needs_knowledge(q):
            return (f"资料库里没有查到与「{q[:30]}」相关的内容，我也没有联网，"
                    "所以不编答案。你可以先把资料入库再问，或者把需求写成一个具体任务交给我办。")
        if len(q) <= 8:
            return pick(["嗯，我在。想聊点什么，或者直接把要办的事说给我听？",
                         "收到。你可以问我平台怎么用、查已入库的资料，或者派一件具体的活。"])
        return ("这句我记下了。当前是离线应答（未接外部模型），我只能基于已入库资料和规则回复，"
                "不会凭空编内容。\n你可以：① 问资料库里已有的内容；② 问我这套平台怎么用；"
                "③ 把一件具体的事说完整，我切到办事模式去执行。")


# ==================== 离线代码模板（真实可运行，不是占位符）====================
_PY_FIB = """import unittest


def fib(n: int) -> list[int]:
    '''返回斐波那契数列前 n 项；n 小于等于 0 时返回空列表。'''
    if n <= 0:
        return []
    a, b = 0, 1
    out: list[int] = []
    for _ in range(n):
        out.append(a)
        a, b = b, a + b
    return out


class TestFib(unittest.TestCase):
    def test_first_ten(self):
        self.assertEqual(fib(10), [0, 1, 1, 2, 3, 5, 8, 13, 21, 34])

    def test_zero(self):
        self.assertEqual(fib(0), [])


if __name__ == '__main__':
    print('斐波那契前 10 项：', fib(10))
    unittest.main(verbosity=1, exit=False)
"""

_PY_SORT = """import unittest


def merge_sort(items: list) -> list:
    '''归并排序：稳定、O(n log n)，不修改原列表。'''
    if len(items) <= 1:
        return list(items)
    mid = len(items) // 2
    left, right = merge_sort(items[:mid]), merge_sort(items[mid:])
    out, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out


class TestSort(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(merge_sort([5, 3, 8, 1, 3]), [1, 3, 3, 5, 8])

    def test_empty(self):
        self.assertEqual(merge_sort([]), [])


if __name__ == '__main__':
    print('排序结果：', merge_sort([5, 3, 8, 1, 3]))
    unittest.main(verbosity=1, exit=False)
"""

_PY_FILE = """import csv
import json
from pathlib import Path


def read_rows(path: str) -> list[dict]:
    '''读取 CSV 文件并转成字典列表；文件不存在时返回空列表。'''
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding='utf-8', newline='') as f:
        return [dict(row) for row in csv.DictReader(f)]


def write_json(path: str, rows: list[dict]) -> int:
    '''把记录写成 UTF-8 JSON 文件，返回写入条数。'''
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(rows)


if __name__ == '__main__':
    sample = [{'name': '示例', 'value': 1}, {'name': '演示', 'value': 2}]
    n = write_json('output/data.json', sample)
    print(f'已写入 {n} 条记录 → output/data.json')
    print('读回校验：', read_rows('samples/notes.md')[:2])
"""

_PY_GENERIC = """from __future__ import annotations

import argparse
import json
from typing import Any


def process(value: Any, **options: Any) -> dict:
    '''通用处理入口：把内部逻辑替换成实际业务需求即可。'''
    return {'input': value, 'options': options, 'ok': True}


def main() -> None:
    parser = argparse.ArgumentParser(description='Agent Platform 生成的通用工具')
    parser.add_argument('value', nargs='?', default='demo', help='待处理的输入值')
    parser.add_argument('--upper', action='store_true', help='把字符串输入转成大写')
    args = parser.parse_args()
    value = args.value.upper() if args.upper else args.value
    print(json.dumps(process(value, upper=args.upper), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
"""

_JS_TEMPLATE = """'use strict';

// 通用处理入口，按实际需求替换内部逻辑
function process(input) {
  return { input, ok: true, length: String(input).length };
}

if (require.main === module) {
  console.log(JSON.stringify(process('demo'), null, 2));
}

module.exports = { process };
"""

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Agent Platform 生成页</title>
  <style>
    body{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;background:#f7f8fa;
         color:#101828;margin:0;padding:48px;display:grid;place-items:center}
    .card{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:28px 32px;
          box-shadow:0 4px 16px rgba(16,24,40,.06);max-width:560px}
    h1{font-size:20px;margin:0 0 12px} p{color:#475467;line-height:1.8}
    button{border:0;background:#2f6bff;color:#fff;border-radius:8px;padding:8px 16px;cursor:pointer}
  </style>
</head>
<body>
  <div class="card">
    <h1>页面已生成</h1>
    <p>这是由 Agent Platform 离线模板生成的可直接打开的 HTML 页面。接入模型 API 后可由模型按需求生成完整页面。</p>
    <button onclick="alert('运行正常')">点我测试</button>
  </div>
</body>
</html>
"""


# ==================== 提示词优化（一边写草稿，一边出标准提示词）====================
PROMPT_OPT_SYSTEM = (
    "你是一名提示词工程专家。用户会给你一段口语化、模糊的原始想法（草稿），"
    "请在不改变原意与主题的前提下，把它改写成为一份清晰、可复用的「标准提示词」。\n"
    "要求：\n"
    "1. 第一行用一句话概括「你是谁 + 要做什么」，例如"
    "「你是一名资深中文行业研究员，请撰写一份关于 X 的调研报告」。\n"
    "2. 之后用 2~5 个要点补全：角色与背景、具体任务与范围、可提供的输入材料（若有）、"
    "输出格式与篇幅、约束（语言、风格、不可编造事实等）。\n"
    "3. 只输出改写后的标准提示词本身，不要任何解释、不要 Markdown 代码块、不要前后缀。\n"
    "4. 必须保留用户原话的关键信息与主题，不要擅自替换或发散。\n"
    "5. 使用简体中文，篇幅控制在 80~220 字。"
)

_PROMPT_PREFIX = ("帮我", "我想", "我要", "能不能帮我", "麻烦你", "麻烦", "帮我一下",
                  "请帮我", "拜托", "那个", "就是", "呃", "额", "然后呢", "是不是可以",
                  "我想问下", "我想问一下", "给我搞个", "搞一个", "弄一个", "整一个")


def _strip_filler(text: str) -> str:
    s = re.sub(r"\s+", " ", text.strip())
    for p in _PROMPT_PREFIX:
        if s.startswith(p):
            s = s[len(p):].strip()
    s = s.strip("，。,.；;：: ").strip()
    return s


def optimize_prompt(llm: "LLM", draft: str, context: str = "") -> tuple[str, str]:
    """把口语化草稿改写成标准提示词。

    返回 (优化后的提示词, 来源)。来源为 'model' 表示由模型改写，'rule' 表示离线规则。
    """
    draft = (draft or "").strip()
    if not draft:
        return "", "empty"

    if not llm.enabled:
        return _optimize_rule(draft, context), "rule"

    user = f"用户原始草稿：{draft}"
    if context:
        user += f"\n补充上下文：{context}"
    try:
        out = llm.chat([
            {"role": "system", "content": PROMPT_OPT_SYSTEM},
            {"role": "user", "content": user},
        ], temperature=0.3)
        out = (out or "").strip()
        if not out:
            return _optimize_rule(draft, context), "rule"
        return out, "model"
    except LLMError:
        # 模型不可用：降级到离线规则，不影响体验
        return _optimize_rule(draft, context), "rule"


def _optimize_rule(draft: str, context: str = "") -> str:
    """离线规则改写：清理口语词 + 按意图补成「角色 + 任务 + 约束」三段式。"""
    clean = _strip_filler(draft) or draft.strip()
    if len(clean) <= 1:
        clean = draft.strip()

    if re.search(r"报告|方案|周报|日报|总结|纪要|邮件|通知|计划|调研|文档|文章|文案|汇报", clean):
        role = "资深中文办公助理"
        task = f"撰写一份关于「{clean}」的结构化中文文档"
        fmt = "含一级标题与多个二级章节，合理使用列表与表格，条理清晰、可直接用于汇报"
    elif re.search(r"写代码|函数|脚本|程序|\.py|\.js|\.html|\.ts|算法|开发|实现", clean):
        role = "资深软件工程师"
        task = f"实现以下需求：{clean}"
        fmt = "给出完整可运行代码，附简短说明；必要时包含单元测试"
    elif re.search(r"查|检索|什么|怎么|为什么|如何|哪里|哪几个|吗|呢", clean):
        role = "严谨的问答助手"
        task = f"回答这个问题：{clean}"
        fmt = "基于可靠依据，简明准确；不确定时如实说明"
    else:
        role = "高效的中文助理"
        task = clean
        fmt = "给出清晰、有条理、可直接使用的回答"

    parts = [
        f"你是一名{role}，{task}。",
        f"要求：{fmt}。",
        "约束：使用简体中文；涉及事实与数据若不能确定须标注「待核实」，不得编造；"
        "不输出无关解释或固定开场白。",
    ]
    if context:
        parts.append(f"已知上下文：{context}")
    return "\n".join(parts)
