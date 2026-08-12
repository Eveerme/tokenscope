#!/usr/bin/env bash
# =============================================================
# TokenScope 启动脚本（Windows git-bash / Linux / macOS 通用）
#   用法: ./start.sh                （可用 PORT / HOST 环境变量覆盖）
#   例:   PORT=9000 ./start.sh
# =============================================================
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="TokenScope"
PORT="${PORT:-8787}"
HOST="${HOST:-127.0.0.1}"
PID_FILE=".tokenscope.pid"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/tokenscope.log"

# ---------- Python 检测 ----------
PY=""
if command -v python >/dev/null 2>&1; then
    PY="python"
elif command -v py >/dev/null 2>&1; then
    PY="py -3"
else
    echo "错误: 未找到 Python，请先安装 Python 3.8+ 并加入 PATH"
    exit 1
fi

# ---------- 已在运行？ ----------
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "$APP_NAME 已在运行 (PID $OLD_PID)，访问 http://127.0.0.1:$PORT/"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# ---------- 端口占用检测 ----------
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
    :
else
    echo "错误: 端口 $PORT 已被占用。换端口: PORT=xxxx ./start.sh；或先 ./stop.sh 停止旧实例"
    exit 1
fi

# ---------- 启动 ----------
mkdir -p "$LOG_DIR"
nohup "$PY" server.py --host "$HOST" --port "$PORT" --no-browser >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# ---------- 健康检查（最长 15 秒） ----------
echo "正在启动 $APP_NAME (端口 $PORT)..."
OK=0
for _ in $(seq 1 30); do
    if "$PY" - "$PORT" <<'EOF'
import socket, sys
try:
    socket.create_connection(("127.0.0.1", int(sys.argv[1])), 0.5).close()
    sys.exit(0)
except OSError:
    sys.exit(1)
EOF
    then
        OK=1
        break
    fi
    sleep 0.5
done

if [ "$OK" = "1" ]; then
    echo "✔ $APP_NAME 已启动: http://127.0.0.1:$PORT/"
    echo "  PID: $(cat "$PID_FILE")    日志: $LOG_FILE"
else
    echo "⚠ 启动超时（15 秒），请查看日志: $LOG_FILE"
    exit 1
fi
