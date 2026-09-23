#!/usr/bin/env bash
# 一次性校验：代码测试 -> 前端构建检查 -> API 冒烟。任一失败即非零退出。
set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
SMOKE_PORT="${SMOKE_PORT:-8137}"

echo "==> [1/3] 后端代码测试 (pytest)"
cd "${APP_DIR}/backend"
python -m pytest tests -q

echo "==> [2/3] 前端构建检查 (tsc + vite build)"
cd "${APP_DIR}/frontend"
npm run build

echo "==> [3/3] API 冒烟测试"
cd "${APP_DIR}/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port "${SMOKE_PORT}" &
SERVER_PID=$!
cleanup() {
  kill "${SERVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT

python "${APP_DIR}/scripts/smoke_test.py" "http://127.0.0.1:${SMOKE_PORT}"

echo "==> VERIFY PASSED"
