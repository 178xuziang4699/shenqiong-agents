#!/usr/bin/env bash
# Agent Platform + 管理后台 · 一键部署（只依赖 Docker + Compose）
set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-up}"

# 首次运行生成访问令牌并写入 .env（600 权限），避免令牌每次重建都变化
ensure_env() {
  if [ ! -f .env ]; then
    local admin_tok view_tok
    admin_tok="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
    view_tok="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
    cat > .env <<EOF
# 自动生成，请勿提交到版本库
AP_ADMIN_TOKEN=${admin_tok}
AP_ADMIN_VIEW_TOKEN=${view_tok}
WEB_WORKERS=2
EOF
    chmod 600 .env
    echo "[deploy] 已生成访问令牌，写入 .env（权限 600）"
  fi
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
}

case "$CMD" in
  up)
    ensure_env
    echo "[deploy] 构建并启动平台(8000) + 管理后台(8001)…"
    docker compose up -d --build
    echo
    echo "  智能体平台 : http://localhost:8000/"
    echo "  管理后台   : http://localhost:8001/"
    echo "  超管令牌   : ${AP_ADMIN_TOKEN}"
    echo "  只读令牌   : ${AP_ADMIN_VIEW_TOKEN}"
    echo
    echo "  令牌保存在 .env（权限 600）；也可用 ./deploy.sh token 随时查看。"
    ;;
  pg)
    ensure_env
    : "${PG_PASSWORD:=ap}"
    export AP_DB_DSN="postgresql://ap:${PG_PASSWORD}@postgres:5432/agent"
    echo "[deploy] 启用 PostgreSQL 模式（支撑更高写入并发）…"
    docker compose --profile pg up -d --build
    echo "  数据库仅容器网络内可达，不对外暴露端口。"
    ;;
  down)
    docker compose --profile pg down
    ;;
  status)
    docker compose ps
    echo "--- health ---"
    curl -fsS http://localhost:8000/health | head -c 400 || echo "平台未就绪"; echo
    curl -fsS http://localhost:8001/health | head -c 200 || echo "控制台未就绪"; echo
    ;;
  logs)
    docker compose logs -f --tail=100
    ;;
  token)
    ensure_env
    echo "超管令牌: ${AP_ADMIN_TOKEN}"
    echo "只读令牌: ${AP_ADMIN_VIEW_TOKEN}"
    ;;
  backup)
    ensure_env
    tok="${AP_ADMIN_TOKEN}"
    curl -fsS -X POST -H "X-Admin-Token: ${tok}" \
      http://localhost:8001/api/admin/backup || echo "备份失败，请确认服务已启动"
    echo
    ;;
  *)
    echo "用法: ./deploy.sh {up|pg|down|status|logs|token|backup}"
    exit 1
    ;;
esac
