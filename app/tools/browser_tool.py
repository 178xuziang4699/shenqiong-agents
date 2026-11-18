"""真实浏览器工具：Playwright 驱动本机 Chromium 打开网页，返回渲染后的 DOM 内容。

与 web_fetch（真实 HTTP 抓取）分工明确：
- web_fetch      —— 发 HTTP 请求、清洗原始 HTML，不执行 JS；
- browser.browse —— 启动真实浏览器渲染页面（执行 JS），取渲染后的标题 / 正文 / 链接。

诚实约束：浏览器二进制缺失、目标不可达、渲染超时都如实回报 driver 与错误，
不降级伪装成成功，也不把「HTTP 抓取」冒充成「浏览器渲染」。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

DRIVER = "playwright/chromium (headless)"


def _launch(p):
    """优先用 Playwright 自带的配套 Chromium；不可用时回退系统 chromium。"""
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return p.chromium.launch(headless=True, args=args)
    except Exception:  # noqa: BLE001 - 回退系统浏览器，仍属真实浏览器驱动
        return p.chromium.launch(headless=True,
                                 executable_path="/usr/bin/chromium", args=args)


def browse(payload: dict) -> dict:
    url = str(payload.get("url") or "").strip()
    try:
        limit = int(payload.get("limit") or 2000)
    except (TypeError, ValueError):
        limit = 2000
    limit = max(100, min(limit, 20000))

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "url": url, "driver": DRIVER,
                "error": "url 必须是合法的 http:// 或 https:// 地址"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"ok": False, "url": url, "driver": DRIVER,
                "error": f"浏览器驱动不可用：{exc}"}

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                resp = page.goto(url, timeout=15000, wait_until="domcontentloaded")
                status = resp.status if resp else None
                title = page.title()
                body = page.evaluate(
                    "() => document.body ? document.body.innerText : ''") or ""
                body = re.sub(r"\s+", " ", body).strip()
                links = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.href).slice(0, 20)")
                final_url = page.url
            finally:
                browser.close()
        return {
            "ok": True, "url": url, "final_url": final_url, "driver": DRIVER,
            "http_status": status, "title": title, "chars": len(body),
            "content": body[:limit], "links": links, "link_count": len(links),
        }
    except Exception as exc:  # noqa: BLE001 - 网络/渲染失败必须如实回报
        return {"ok": False, "url": url, "driver": DRIVER,
                "error": f"{type(exc).__name__}: {exc}"}
