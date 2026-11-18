"""最小 JSON Schema 校验器（够用即止，不引第三方库）。

覆盖：type / required / properties / enum / minimum / maximum /
     minLength / maxLength / pattern / items / additionalProperties。
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_MAP: dict[str, tuple] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    allowed = _TYPE_MAP.get(expected)
    if allowed is None:
        return True  # 未知类型不阻断
    if expected in ("object", "array", "string"):
        return isinstance(value, allowed) and not (expected == "string" and isinstance(value, bool))
    return isinstance(value, allowed)


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict) or not schema:
        return errors

    expected_type = schema.get("type")
    if expected_type and not _type_ok(instance, expected_type):
        errors.append(f"{path}: 期望类型 {expected_type}，实际 {type(instance).__name__}")
        return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: 取值必须是 {schema['enum']} 之一，实际 {instance!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: 长度小于 {schema['minLength']}")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path}: 长度大于 {schema['maxLength']}")
        if schema.get("pattern"):
            try:
                if not re.search(str(schema["pattern"]), instance):
                    errors.append(f"{path}: 不匹配模式 {schema['pattern']}")
            except re.error:
                errors.append(f"{path}: 模式非法 {schema['pattern']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: 大于最大值 {schema['maximum']}")

    if isinstance(instance, dict):
        for req in schema.get("required", []) or []:
            if req not in instance:
                errors.append(f"{path}: 缺少必填字段 {req}")
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            for key in sorted(extra):
                errors.append(f"{path}: 不允许的额外字段 {key}")

    if isinstance(instance, (list, tuple)) and isinstance(schema.get("items"), dict):
        for idx, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{path}[{idx}]"))

    return errors
