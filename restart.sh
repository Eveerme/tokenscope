#!/usr/bin/env bash
# =============================================================
# TokenScope 重启脚本（Windows git-bash / Linux / macOS 通用）
#   用法: ./restart.sh
# =============================================================
set -euo pipefail
cd "$(dirname "$0")"

./stop.sh
echo
./start.sh
