#!/bin/bash
# launchd 전용 — 이 스크립트는 종료되지 않고 계속 실행됨
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"
NGROK_LOG="$PROJECT_DIR/ngrok.log"
ENV_FILE="$PROJECT_DIR/.env"

cleanup() {
  kill $LLAMA_PID $APP_PID $NGROK_PID 2>/dev/null
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

echo "[4/4] ngrok 시작 중..." >> "$APP_LOG"
> "$NGROK_LOG"
ngrok http 8000 --log=stdout >> "$NGROK_LOG" 2>&1 &
NGROK_PID=$!

# 터널 URL 추출
for i in $(seq 1 30); do
  sleep 1
  NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o 'https://[a-zA-Z0-9-]*\.ngrok[a-z.-]*' | head -1)
  if [ -n "$NGROK_URL" ]; then
    if grep -q "^APP_BASE_URL=" "$ENV_FILE"; then
      sed -i '' "s|^APP_BASE_URL=.*|APP_BASE_URL=$NGROK_URL|" "$ENV_FILE"
    else
      echo "APP_BASE_URL=$NGROK_URL" >> "$ENV_FILE"
    fi
    echo "  터널 URL: $NGROK_URL" >> "$APP_LOG"
    break
  fi
done

echo "서버 실행 중 (launchd 관리)" >> "$APP_LOG"

# launchd가 이 스크립트 생명주기를 관리 — uvicorn 종료 시 launchd가 재시작
wait $APP_PID
