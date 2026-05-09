#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"

echo "[1/3] llama-server 시작 중..."
llama-server \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 2048 \
  --n-gpu-layers 0 \
  --threads 8 \
  --log-disable \
  >> "$LLAMA_LOG" 2>&1 &
LLAMA_PID=$!
echo "  PID: $LLAMA_PID"

echo "[2/3] llama-server 준비 대기 중..."
for i in $(seq 1 24); do
  sleep 5
  if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health 2>/dev/null | grep -q "200"; then
    echo "  준비 완료 ($((i*5))초)"
    break
  fi
  if [ $i -eq 24 ]; then
    echo "  오류: llama-server 시작 실패. $LLAMA_LOG 확인"
    exit 1
  fi
done

echo "[3/3] FastAPI 서버 시작 중..."
cd "$PROJECT_DIR"
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload >> "$APP_LOG" 2>&1 &
APP_PID=$!
echo "  PID: $APP_PID"

echo ""
echo "서버 실행 중"
echo "  어드민:      http://127.0.0.1:8000"
echo "  llama-server: http://127.0.0.1:8080"
echo ""
echo "종료: kill $LLAMA_PID $APP_PID"
echo ""

# 두 프로세스 중 하나라도 종료되면 같이 종료
wait $APP_PID
kill $LLAMA_PID 2>/dev/null
