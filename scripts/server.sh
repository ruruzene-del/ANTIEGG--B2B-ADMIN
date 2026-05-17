#!/bin/bash
# launchd 전용 — 이 스크립트는 종료되지 않고 계속 실행됨
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"
NGROK_LOG="$PROJECT_DIR/ngrok.log"
ENV_FILE="$PROJECT_DIR/.env"

# .env에서 NGROK_DOMAIN 읽기 (정적 도메인 1개 — 재시작 후 동일 URL)
NGROK_DOMAIN=$(grep "^NGROK_DOMAIN=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")

cleanup() {
  kill $LLAMA_PID $APP_PID $NGROK_PID $WATCHDOG_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

# 포트 정리 (full path — launchd PATH에 /usr/sbin 없음)
/usr/sbin/lsof -ti:8000 | xargs kill -9 2>/dev/null || true
/usr/sbin/lsof -ti:8080 | xargs kill -9 2>/dev/null || true
/usr/sbin/lsof -ti:4040 | xargs kill -9 2>/dev/null || true

echo "[1/4] llama-server 시작 중..." >> "$APP_LOG"
llama-server \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 4096 \
  --n-gpu-layers 0 \
  --threads 8 \
  --log-disable \
  >> "$LLAMA_LOG" 2>&1 &
LLAMA_PID=$!

echo "[2/4] llama-server 준비 대기 중..." >> "$APP_LOG"
for i in $(seq 1 60); do
  sleep 5
  if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health 2>/dev/null | grep -q "200"; then
    echo "  준비 완료 ($((i*5))초)" >> "$APP_LOG"
    break
  fi
  if [ $i -eq 60 ]; then
    echo "  오류: llama-server 시작 실패" >> "$APP_LOG"
    exit 1
  fi
done

echo "[3/4] FastAPI 시작 중..." >> "$APP_LOG"
cd "$PROJECT_DIR"
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 >> "$APP_LOG" 2>&1 &
APP_PID=$!

start_ngrok() {
  > "$NGROK_LOG"
  if [ -n "$NGROK_DOMAIN" ]; then
    ngrok http "--url=$NGROK_DOMAIN" 8000 --log=stdout >> "$NGROK_LOG" 2>&1 &
  else
    ngrok http 8000 --log=stdout >> "$NGROK_LOG" 2>&1 &
  fi
  NGROK_PID=$!
}

echo "[4/4] ngrok 시작 중..." >> "$APP_LOG"
start_ngrok

# 터널 URL 결정 — 고정 도메인이면 즉시, 아니면 30초 폴링
if [ -n "$NGROK_DOMAIN" ]; then
  NGROK_URL="https://$NGROK_DOMAIN"
  sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=$NGROK_URL|" "$ENV_FILE"
  echo "  터널 URL (고정): $NGROK_URL" >> "$APP_LOG"
else
  for i in $(seq 1 30); do
    sleep 1
    NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o 'https://[a-zA-Z0-9-]*\.ngrok[a-z.-]*' | head -1)
    if [ -n "$NGROK_URL" ]; then
      if grep -q "^APP_BASE_URL=" "$ENV_FILE"; then
        sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=$NGROK_URL|" "$ENV_FILE"
      else
        echo "APP_BASE_URL=$NGROK_URL" >> "$ENV_FILE"
      fi
      echo "  터널 URL (random): $NGROK_URL" >> "$APP_LOG"
      break
    fi
  done
fi

# ngrok watchdog — 프로세스 죽거나 4040 API 응답 안 하면 재시작
ngrok_watchdog() {
  while sleep 60; do
    if ! kill -0 $NGROK_PID 2>/dev/null; then
      echo "[$(date '+%H:%M:%S')] [watchdog] ngrok 프로세스 죽음 → 재시작" >> "$APP_LOG"
      start_ngrok
    elif ! curl -sf -o /dev/null http://127.0.0.1:4040/api/tunnels 2>/dev/null; then
      echo "[$(date '+%H:%M:%S')] [watchdog] ngrok API 응답 없음 → 재시작" >> "$APP_LOG"
      kill $NGROK_PID 2>/dev/null
      sleep 2
      start_ngrok
    fi
  done
}
ngrok_watchdog &
WATCHDOG_PID=$!

echo "서버 실행 중 (launchd 관리, ngrok watchdog PID $WATCHDOG_PID)" >> "$APP_LOG"

# launchd가 이 스크립트 생명주기를 관리 — uvicorn 종료 시 launchd가 재시작
wait $APP_PID
