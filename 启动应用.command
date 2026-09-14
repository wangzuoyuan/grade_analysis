#!/bin/bash
# 一键启动“学情追踪”（班主任 + 教学工作台）
# 双击运行：先后启动后端(8000)与前端(3000)，就绪后自动打开浏览器。
# 服务日志：.test-data/dev/logs/；数据目录与旧栈完全隔离。
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${EXAM_TRACKER_LOG_DIR:-$ROOT/.test-data/dev/logs}"
DATA_DIR="${EXAM_TRACKER_DIR:-$ROOT/.test-data/dev/exam-tracker}"
BACKUP_DIR="${EXAM_TRACKER_BACKUP_DIR:-$ROOT/.test-data/dev/backups}"
mkdir -p "$LOG_DIR" "$DATA_DIR" "$BACKUP_DIR"

say() { printf '%s\n' "$*"; }

port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN -P -n >/dev/null 2>&1; }

listener_is_project() {
  local port="$1" expected_cwd="$2" pid cwd
  for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN -P -n 2>/dev/null); do
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    [ "$cwd" = "$expected_cwd" ] || return 1
  done
  return 0
}

# wait_http <url> <最大秒数>；仅 2xx 响应视为就绪
wait_http() {
  local url="$1" tries="$2" i=0
  until curl -fs -o /dev/null --max-time 2 "$url"; do
    i=$((i + 1))
    [ "$i" -ge "$tries" ] && return 1
    sleep 1
  done
  return 0
}

if [ ! -x "$ROOT/.venv/bin/uvicorn" ]; then
  say "✗ 未找到后端虚拟环境 .venv，请先在仓库根目录执行："
  say "    python3.11 -m venv .venv"
  say "    .venv/bin/pip install -e backend pytest httpx --constraint backend/requirements-lock.txt"
  read -r -p "按回车关闭…" _
  exit 1
fi

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  say "✗ 前端依赖未安装，请先执行：cd frontend && npm ci"
  read -r -p "按回车关闭…" _
  exit 1
fi

# ---- 后端 ----
if port_busy 8000; then
  if listener_is_project 8000 "$ROOT/backend" && wait_http http://127.0.0.1:8000/api/health 1; then
    say "• 后端：本项目服务已在 8000 端口运行"
  else
    say "✗ 8000 端口被其他程序占用，未启动后端，也不会停止该程序"
    read -r -p "按回车关闭…" _ || true
    exit 1
  fi
elif (cd "$ROOT/backend" &&
  EXAM_TRACKER_DIR="$DATA_DIR" \
  EXAM_TRACKER_BACKUP_DIR="$BACKUP_DIR" \
  nohup "$ROOT/.venv/bin/uvicorn" app.main:app --port 8000 >>"$LOG_DIR/backend.log" 2>&1 &); then
  if wait_http http://127.0.0.1:8000/api/health 30; then
    say "• 后端就绪：http://127.0.0.1:8000"
  else
    say "✗ 后端 30 秒内未就绪，请查看 $LOG_DIR/backend.log"
    read -r -p "按回车关闭…" _ || true
    exit 1
  fi
else
  say "✗ 后端启动命令执行失败，请查看 $LOG_DIR/backend.log"
  read -r -p "按回车关闭…" _ || true
  exit 1
fi

# ---- 前端 ----
if port_busy 3000; then
  if listener_is_project 3000 "$ROOT/frontend" && wait_http http://127.0.0.1:3000 1; then
    say "• 前端：本项目服务已在 3000 端口运行"
  else
    say "✗ 3000 端口被其他程序占用，未启动前端，也不会停止该程序"
    read -r -p "按回车关闭…" _ || true
    exit 1
  fi
elif (cd "$ROOT/frontend" &&
  nohup npm run dev >>"$LOG_DIR/frontend.log" 2>&1 &); then
  if wait_http http://127.0.0.1:3000 60; then
    say "• 前端就绪：http://localhost:3000"
  else
    say "✗ 前端 60 秒内未就绪，请查看 $LOG_DIR/frontend.log"
    read -r -p "按回车关闭…" _ || true
    exit 1
  fi
else
  say "✗ 前端启动命令执行失败，请查看 $LOG_DIR/frontend.log"
  read -r -p "按回车关闭…" _ || true
  exit 1
fi

say ""
say "═══════════════════════════════════════════"
say "  应用已启动：http://localhost:3000（本地免登录）"
say "  数据目录：$DATA_DIR"
say "  备份目录：$BACKUP_DIR"
say "  停止服务：双击「停止应用.command」"
say "═══════════════════════════════════════════"
open "http://localhost:3000"
read -r -p "按回车关闭本窗口（服务会继续在后台运行）…" _ || true
exit 0
