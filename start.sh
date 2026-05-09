#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"
CF_LOG="$PROJECT_DIR/cloudflared.log"
ENV_FILE="$PROJECT_DIR/.env"

echo "[1/4] llama-server 시작 중..."
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
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload >> "$APP_LOG" 2>&1 &
APP_PID=$!
echo "  PID: $APP_PID"

echo "[4/4] Cloudflare Tunnel 시작 중..."
# 기존 로그 초기화
> "$CF_LOG"
cloudflared tunnel --url http://localhost:8000 --logfile "$CF_LOG" &
CF_PID=$!
echo "  PID: $CF_PID"

# 터널 URL 추출 대기 (최대 30초)
CF_URL=""
for i in $(seq 1 30); do
  sleep 1
  CF_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)
  if [ -n "$CF_URL" ]; then
    echo "  터널 URL: $CF_URL"
    break
  fi
done

if [ -n "$CF_URL" ]; then
  # .env의 APP_BASE_URL 업데이트
  if grep -q "^APP_BASE_URL=" "$ENV_FILE"; then
    sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=$CF_URL|" "$ENV_FILE"
  else
    echo "APP_BASE_URL=$CF_URL" >> "$ENV_FILE"
  fi
  echo "  .env APP_BASE_URL 업데이트 완료"
else
  echo "  경고: 터널 URL 감지 실패 — $CF_LOG 확인"
fi

echo ""
echo "서버 실행 중"
echo "  어드민 (로컬):  http://127.0.0.1:8000"
echo "  어드민 (외부):  ${CF_URL:-미확인 — cloudflared.log 확인}"
echo "  llama-server:  http://127.0.0.1:8080"
echo ""
echo "종료: kill $LLAMA_PID $APP_PID $CF_PID"
echo ""

# 절전 방지 (caffeinate는 macOS 내장)
caffeinate -i &
CAF_PID=$!

cleanup() {
  kill $APP_PID $CF_PID $CAF_PID 2>/dev/null
  kill $LLAMA_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

wait $APP_PID
