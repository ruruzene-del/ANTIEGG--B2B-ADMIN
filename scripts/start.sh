#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"
NGROK_LOG="$PROJECT_DIR/ngrok.log"
PID_FILE="$PROJECT_DIR/.pids"
ENV_FILE="$PROJECT_DIR/.env"

# 기존 프로세스 정리
if [ -f "$PID_FILE" ]; then
  echo "기존 프로세스 정리 중..."
  while read -r pid; do
    kill "$pid" 2>/dev/null || true
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  sleep 1
fi
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:4040 | xargs kill -9 2>/dev/null || true

echo "[1/4] llama-server 시작 중..."
nohup llama-server \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 4096 \
  --n-gpu-layers 0 \
  --threads 8 \
  --log-disable \
  >> "$LLAMA_LOG" 2>&1 &
LLAMA_PID=$!
disown $LLAMA_PID
echo "  PID: $LLAMA_PID"

echo "[2/4] llama-server 준비 대기 중..."
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

echo "[3/4] FastAPI 서버 시작 중..."
cd "$PROJECT_DIR"
nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 >> "$APP_LOG" 2>&1 &
APP_PID=$!
disown $APP_PID
echo "  PID: $APP_PID"

echo "[4/4] ngrok 터널 시작 중..."
pkill -f ngrok 2>/dev/null || true
sleep 1
> "$NGROK_LOG"
nohup ngrok http 8000 --log=stdout >> "$NGROK_LOG" 2>&1 &
NGROK_PID=$!
disown $NGROK_PID
echo "  PID: $NGROK_PID"

# PID 파일 저장
echo "$LLAMA_PID" > "$PID_FILE"
echo "$APP_PID" >> "$PID_FILE"
echo "$NGROK_PID" >> "$PID_FILE"

# 터널 URL 추출 대기 (최대 30초)
NGROK_URL=""
for i in $(seq 1 30); do
  sleep 1
  NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o 'https://[a-zA-Z0-9-]*\.ngrok[a-z.-]*' | head -1)
  if [ -n "$NGROK_URL" ]; then
    echo "  터널 URL: $NGROK_URL"
    break
  fi
done

if [ -n "$NGROK_URL" ]; then
  if grep -q "^APP_BASE_URL=" "$ENV_FILE"; then
    sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=$NGROK_URL|" "$ENV_FILE"
  else
    echo "APP_BASE_URL=$NGROK_URL" >> "$ENV_FILE"
  fi
  echo "  .env APP_BASE_URL 업데이트 완료"
else
  echo "  경고: 터널 URL 감지 실패 — $NGROK_LOG 확인"
fi

# 절전 방지
caffeinate -i &
disown $!

echo ""
echo "✓ 서버 실행 중 (백그라운드 데몬)"
echo "  어드민 (로컬):  http://127.0.0.1:8000"
echo "  어드민 (외부):  ${NGROK_URL:-미확인 — ngrok.log 확인}"
echo "  llama-server:  http://127.0.0.1:8080"
echo ""
echo "종료: bash $PROJECT_DIR/stop.sh"
echo ""
