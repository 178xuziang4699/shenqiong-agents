"""统一异常与错误码。错误码是错误归类、重试策略、恢复动作的分派依据。"""

from __future__ import annotations


class AgentPlatformError(Exception):
    """所有平台异常的基类，携带 error_code 便于归类。"""

    error_code = "UNKNOWN"

    def __init__(self, message: str, *, error_code: str | None = None, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "message": self.message, "detail": self.detail}


class ConfigError(AgentPlatformError):
    error_code = "CONFIG_INVALID"


class ToolNotRegistered(AgentPlatformError):
    error_code = "SCHEMA_INVALID"


class SchemaValidationError(AgentPlatformError):
    error_code = "SCHEMA_INVALID"


class ToolPermissionDenied(AgentPlatformError):
    error_code = "HUMAN_REJECTED"


class ToolCallError(AgentPlatformError):
    error_code = "TOOL_ERROR"


class ToolTimeout(AgentPlatformError):
    error_code = "TIMEOUT"


class SkillNotFound(AgentPlatformError):
    error_code = "SCHEMA_INVALID"


class ValidationFailed(AgentPlatformError):
    error_code = "VALIDATION_FAILED"


class HumanRejected(AgentPlatformError):
    error_code = "HUMAN_REJECTED"


class EscalationRequired(AgentPlatformError):
    error_code = "ESCALATED"


class SubTaskTimeout(AgentPlatformError):
    error_code = "TIMEOUT"


class AgentInterrupt(AgentPlatformError):
    """模拟/真实进程中断：快照会被标记 interrupted，恢复流程据此定位断点。"""

    error_code = "INTERRUPTED"


class InjectedFault(AgentPlatformError):
    """演示与测试用的故障注入（瞬时故障，可被重试治愈）。"""

    error_code = "TOOL_ERROR"


ERROR_CLASSES = {
    "INTERRUPTED": "进程/任务被中断，存在未完成步骤",
    "TIMEOUT": "调用超时",
    "TOOL_ERROR": "外部工具调用失败",
    "SCHEMA_INVALID": "工具/技能契约不匹配（注册表缺失或参数不合规）",
    "VALIDATION_FAILED": "结果未通过管理者验收",
    "HUMAN_REJECTED": "人工确认被拒绝",
    "LLM_ERROR": "模型/规划调用失败",
    "ESCALATED": "已上报总控",
    "UNKNOWN": "未归类错误",
}


def classify(exc: BaseException) -> str:
    if isinstance(exc, AgentPlatformError):
        return exc.error_code
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    return "UNKNOWN"
