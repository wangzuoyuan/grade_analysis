#!/usr/bin/env bash
# D01 端口/配置预检（合成环境可跑；真实 NAS 预检留现场清单）。
#
# 用法：bash deploy/check-ports.sh [端口...]   # 缺省检查 CADDY_PORT 候选 8081
# 退出码：0=全部通过（或 docker 缺失跳过）；1=端口被占用或配置校验失败。
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORTS=("$@")
if [ ${#PORTS[@]} -eq 0 ]; then
  PORTS=("${CADDY_PORT:-8081}")
fi

fail=0

for port in "${PORTS[@]}"; do
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[FAIL] 端口 $port 已被占用（lsof）"
      fail=1
    else
      echo "[OK]   端口 $port 空闲"
    fi
  elif command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$port$"; then
      echo "[FAIL] 端口 $port 已被占用（ss）"
      fail=1
    else
      echo "[OK]   端口 $port 空闲"
    fi
  else
    echo "[SKIP] 无 lsof/ss 可用，端口 $port 未检（留现场清单）"
  fi
done

# compose 配置语法校验：无 docker 时如实跳过（不假报通过）。
# backend.env 是现场凭据文件、绝不入库，config 校验在临时目录放空占位，
# 不因凭据未就绪而误报配置失败。backend/ 空目录是 build 上下文占位：
# compose 现带 build: ../backend（Q07 可构建候选），config 校验要求路径存在。
if command -v docker >/dev/null 2>&1; then
  tmpdir="$(mktemp -d)"
  cp "$SCRIPT_DIR/docker-compose.yml" "$SCRIPT_DIR/Caddyfile" "$tmpdir/"
  : > "$tmpdir/backend.env"
  mkdir "$tmpdir/backend"
  if (cd "$tmpdir" && docker compose config --quiet); then
    echo "[OK]   docker compose config 校验通过"
  else
    echo "[FAIL] docker compose config 校验失败"
    fail=1
  fi
  rm -rf "$tmpdir"
else
  echo "[SKIP] 本机无 docker，compose config 校验跳过（合成环境已用解析器校验，现场补跑）"
fi

exit "$fail"
