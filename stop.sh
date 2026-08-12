#!/usr/bin/env bash
# =============================================================
# TokenScope 停止脚本（Windows git-bash / Linux / macOS 通用）
#   用法: ./stop.sh
# =============================================================
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="TokenScope"
PORT="${PORT:-8787}"
PID_FILE=".tokenscope.pid"

IS_WIN=0
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) IS_WIN=1 ;;
esac

PY=""
if command -v python >/dev/null 2>&1; then
    PY="python"
elif command -v py >/dev/null 2>&1; then
    PY="py -3"
fi

# ---------- 按 PID 文件优雅停止 ----------
STOPPED=0
if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null; then
        echo "正在停止 $APP_NAME (PID $PID)..."
        kill "$PID" 2>/dev/null || true
        for _ in $(seq 1 20); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$PID" 2>/dev/null; then
            echo "进程未响应，强制结束..."
            kill -9 "$PID" 2>/dev/null || true
            sleep 1
        fi
        STOPPED=1
    fi
    rm -f "$PID_FILE"
else
    echo "未找到 PID 文件（服务可能未通过脚本启动）"
fi

# ---------- 端口残留兜底 ----------
if [ -n "$PY" ]; then
    if "$PY" - "$PORT" <<'EOF'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
EOF
    then
        echo "✔ 端口 $PORT 已释放"
    else
        echo "⚠ 端口 $PORT 仍被占用"
        if [ "$IS_WIN" = "1" ] && command -v netstat >/dev/null 2>&1; then
            RESIDUAL_PID="$(netstat -ano 2>/dev/null | grep ":$PORT" | grep -i listening | awk '{print $NF}' | head -1)"
            if [ -n "$RESIDUAL_PID" ]; then
                echo "  残留进程 PID=$RESIDUAL_PID，尝试强制结束..."
                MSYS_NO_PATHCONV=1 taskkill /F /PID "$RESIDUAL_PID" /T >/dev/null 2>&1 || \
                    taskkill //F //PID "$RESIDUAL_PID" //T >/dev/null 2>&1 || true
                sleep 1
            fi
        fi
    fi
fi

if [ "$STOPPED" = "1" ]; then
    echo "✔ $APP_NAME 已停止"
else
    echo "✔ 端口 $PORT 无服务在运行"
fi
