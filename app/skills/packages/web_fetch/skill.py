"""技能包：真实抓取公开网页正文。不是占位——发真实 HTTP 请求并清洗 HTML。"""

import html
import re
import urllib.request


def run(params: dict, ctx: dict | None = None) -> dict:
    url = str(params.get("url") or "").strip()
    limit = int(params.get("limit") or 2000)
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AgentPlatform/1.0)",
        "Accept": "text/html,application/json,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(300000).decode("utf-8", "replace")
            status = resp.status
    except Exception as exc:  # noqa: BLE001 - 网络失败要如实回报，不伪装成功
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": url}
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return {"ok": True, "url": url, "http_status": status, "title": title,
            "chars": len(text), "content": text[:limit]}
