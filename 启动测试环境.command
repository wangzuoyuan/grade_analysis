#!/bin/bash
# 每次取得生产最新数据副本，测试修改先备份再替换。
set -eu
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC_DB="/Users/xiaomeng/code/学情追踪/deploy/data/db.sqlite"
DATA_DIR="$ROOT/.test-data/dev/exam-tracker"
BACKUP_DIR="$ROOT/.test-data/dev/backups"
LOCK_DIR="$ROOT/.test-data/dev/.refresh-lock"

finish() {
  result=$?
  rmdir "$LOCK_DIR" 2>/dev/null || true
  if [ "$result" -ne 0 ]; then echo "✗ 更新未完成，请查看上方提示；生产应用不受影响。"; fi
  if [ -t 0 ]; then read -r -p "按回车关闭…" _ || true; fi
}
test -f "$SRC_DB" || { echo "✗ 未找到生产数据库：$SRC_DB"; exit 1; }
test -x "$ROOT/.venv/bin/python" || { echo "✗ 缺少本工作树的 Python 环境"; exit 1; }
mkdir -p "$DATA_DIR" "$BACKUP_DIR"
mkdir "$LOCK_DIR" 2>/dev/null || { echo "✗ 另一次同步尚未结束，请稍后再试。"; exit 1; }
trap finish EXIT

echo "• 正在停止本工作树的测试服务…"
bash "$ROOT/停止应用.command" </dev/null
# 停止后再检查端口及数据库句柄，避免覆盖仍被使用的副本。
for port in 8000 3000; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✗ 端口 $port 仍在使用，同步已取消。"
    exit 1
  fi
done
if [ -f "$DATA_DIR/db.sqlite" ] && lsof "$DATA_DIR/db.sqlite" >/dev/null 2>&1; then
  echo "✗ 测试数据库仍被其他进程使用，同步已取消。"
  exit 1
fi

echo "• 正在获取生产最新数据、校验并迁移测试副本…"
"$ROOT/.venv/bin/python" "$ROOT/scripts/refresh_test_data.py"

# 强制使用工作树副本，避免继承终端中的其他数据路径。
export EXAM_TRACKER_DIR="$DATA_DIR"
export EXAM_TRACKER_BACKUP_DIR="$BACKUP_DIR"
export EXAM_TRACKER_LOG_DIR="$ROOT/.test-data/dev/logs"
echo "• 最新数据已就绪。测试录入仅保存在副本，下次同步前会自动备份。"
bash "$ROOT/启动应用.command" </dev/null
