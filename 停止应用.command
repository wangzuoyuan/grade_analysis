#!/bin/bash
# 停止本地“学情追踪”：只停止工作目录属于本项目的监听进程。
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"

say() { printf '%s\n' "$*"; }

listener_is_project() {
  local pid="$1" expected_cwd="$2" cwd args
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [ "$cwd" = "$expected_cwd" ] && return 0
  # 兼容从工作树根目录用 --app-dir 启动的本地后端；仍核对完整路径。
  if [ "$expected_cwd" = "$ROOT/backend" ] && [ "$cwd" = "$ROOT" ]; then
    args="$(ps -p "$pid" -o args=)"
    case "$args" in
      *"$ROOT/.venv/bin/uvicorn app.main:app"*"--app-dir $ROOT/backend") return 0 ;;
    esac
  fi
  return 1
}

stop_port() {
  local port="$1" name="$2" expected_cwd="$3" pids pid cwd i
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN -P -n 2>/dev/null)"
  if [ -z "$pids" ]; then
    say "• ${name}（端口 ${port}）：未在运行"
    return 0
  fi
  for pid in $pids; do
    if ! listener_is_project "$pid" "$expected_cwd"; then
      say "✗ ${name}端口 ${port} 由其他程序占用（PID ${pid}），拒绝停止"
      return 1
    fi
  done
  say "• ${name}（端口 ${port}）：停止本项目进程 $(echo $pids | tr '\n' ' ')"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do
    lsof -tiTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1 || { say "  已停止"; return 0; }
    sleep 1
  done
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN -P -n 2>/dev/null)"
  if [ -n "$pids" ]; then
    # 等待期间端口可能被其他进程接管；强制停止前再次核验身份。
    for pid in $pids; do
      if ! listener_is_project "$pid" "$expected_cwd"; then
        say "✗ ${name}端口 ${port} 已由其他程序接管（PID ${pid}），拒绝强制停止"
        return 1
      fi
    done
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null
    say "  已强制停止"
  fi
}

failed=0
stop_port 8000 "后端" "$ROOT/backend" || failed=1
stop_port 3000 "前端" "$ROOT/frontend" || failed=1
say ""
if [ "$failed" -eq 0 ]; then
  say "已全部停止。重新启动：双击「启动应用.command」"
else
  say "检测到非本项目进程，未对其执行停止操作。"
fi
read -r -p "按回车关闭…" _ || true
exit "$failed"
