#!/bin/bash
# 获取最新生产快照并重启测试环境，与启动入口使用同一安全流程。
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/启动测试环境.command"
