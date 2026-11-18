"""技能包：用真实浏览器渲染网页。

实现体集中在 app/tools/browser_tool.py（Playwright 驱动 Chromium），
这里只做薄封装，保证技能直达 / 全量验证 / 工具注册三条路径共用同一份真实实现。
不是占位：会启动 headless Chromium 打开目标页、执行 JS、取渲染后的 DOM 文本。
"""

from app.tools.browser_tool import browse


def run(params: dict, ctx: dict | None = None) -> dict:
    return browse(params)
