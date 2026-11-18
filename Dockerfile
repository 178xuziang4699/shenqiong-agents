# Agent Platform + 管理后台 · 单镜像双服务
# 平台与后台共用同一份代码与同一个数据卷，通过不同启动命令区分进程。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AP_DATA_DIR=/app/var \
    AP_CONFIRM_MODE=approve

WORKDIR /app

# 系统依赖默认不安装：构建最快、最省事，健康检查用 Python 标准库即可。
# 需要 PDF→图片（poppler）等能力时再显式开启：
#   docker build --build-arg WITH_SYSTEM_DEPS=1 -t agent-platform:2.0 .
# 注：不开此项时，依赖 poppler 的 PDF 转图能力会按平台既有降级逻辑处理。
ARG WITH_SYSTEM_DEPS=0
RUN if [ "$WITH_SYSTEM_DEPS" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            poppler-utils curl fonts-dejavu-core \
        && rm -rf /var/lib/apt/lists/* ; \
    fi

COPY requirements.txt ./
# 默认走国内镜像源：实测 2.8MB/s，比官方源（约 41KB/s）快约 70 倍，部署更快。
# 海外环境可覆盖：--build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

# 非 root 运行，降低容器逃逸风险
RUN useradd -m -u 10001 appuser 2>/dev/null || true; \
    mkdir -p /app/var /app/logs /app/output && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8001

# 平台进程用 PORT，后台独立进程用 ADMIN_PORT（compose 里另有显式 healthcheck 覆盖）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT',os.environ.get('ADMIN_PORT','8000'))+'/health',timeout=4)"]

# 默认启动平台；后台服务由 compose 覆盖 command 启动 admin.server:app
CMD ["sh", "-c", "exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_WORKERS:-2}"]
