"""技能包：代码语法自检。不执行代码，只做静态检查，避免任意代码执行风险。"""
import ast


def run(params: dict, ctx: dict | None = None) -> dict:
    code = str(params.get("code") or "")
    language = str(params.get("language") or "python").lower()
    errors: list[str] = []

    if language == "python":
        try:
            ast.parse(code)
            checked = "ast.parse（Python 语法树）"
        except SyntaxError as exc:
            errors.append(f"第 {exc.lineno} 行：{exc.msg}")
            checked = "ast.parse（Python 语法树）"
    else:
        checked = "括号配对与空内容检查"
        for left, right in (("(", ")"), ("{", "}"), ("[", "]")):
            if code.count(left) != code.count(right):
                errors.append(f"{left}{right} 数量不匹配")
        if not code.strip():
            errors.append("代码为空")

    return {
        "ok": not errors,
        "errors": errors,
        "language": language,
        "lines": len(code.splitlines()),
        "checked": checked,
    }
