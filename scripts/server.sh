#!/bin/bash
# launchd 전용 — 이 스크립트는 종료되지 않고 계속 실행됨
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="$PROJECT_DIR/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
LLAMA_LOG="$PROJECT_DIR/llama-server.log"
APP_LOG="$PROJECT_DIR/app.log"
ENV_FILE="$PROJECT_DIR/.env"

# 외부 노출은 Tailscale Funnel이 담당 (별도 LaunchAgent com.antiegg.tailscaled).
# APP_BASE_URL은 .env에 ts.net URL로 고정 — 이 스크립트는 덮어쓰지 않는다.

cleanup() {
  kill $LLAMA_PID $APP_PID $LLAMA_WD_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

# 포트 정리 (full path — launchd PATH에 /usr/sbin 없음)
/usr/sbin/lsof -ti:8000 | xargs kill -9 2>/dev/null || true
/usr/sbin/lsof -ti:8080 | xargs kill -9 2>/dev/null || true

start_llama() {
  llama-server \
    -m "$MODEL" \
    --host 127.0.0.1 --port 8080 \
    --ctx-size 4096 \
    --n-gpu-layers 0 \
    --threads 8 \
    --log-disable \
    >> "$LLAMA_LOG" 2>&1 &
  LLAMA_PID=$!
}

# /health가 200 뜰 때까지 최대 300초 폴링 (콜드 로딩 ~45초)
wait_llama_ready() {
  for i in $(seq 1 60); do
    sleep 5
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health 2>/dev/null | grep -q "200"; then
      return 0
    fi
  done
  return 1
}

echo "[1/4] llama-server 시작 중..." >> "$APP_LOG"
start_llama

echo "[2/4] llama-server 준비 대기 중..." >> "$APP_LOG"
if wait_llama_ready; then
  echo "  준비 완료" >> "$APP_LOG"
else
  echo "  오류: llama-server 시작 실패" >> "$APP_LOG"
  exit 1
fi

echo "[3/3] FastAPI 시작 중..." >> "$APP_LOG"
cd "$PROJECT_DIR"
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 >> "$APP_LOG" 2>&1 &
APP_PID=$!

# llama-server watchdog — 프로세스가 죽으면 재시작 (CPU 모드 크래시 대응)
# 헬스 200은 죽음 판정에 안 씀: CPU 추론이 요청당 3~5분이라 busy를 죽음으로 오인할 수 있어 kill -0(프로세스 생존)만 신호로 사용
llama_watchdog() {
  while sleep 60; do
    if ! kill -0 $LLAMA_PID 2>/dev/null; then
      echo "[$(date '+%H:%M:%S')] [watchdog] llama-server 죽음 → 재시작" >> "$APP_LOG"
      start_llama
      if wait_llama_ready; then
        echo "[$(date '+%H:%M:%S')] [watchdog] llama-server 복구 완료" >> "$APP_LOG"
      else
        echo "[$(date '+%H:%M:%S')] [watchdog] llama-server 복구 실패 — 다음 주기 재시도" >> "$APP_LOG"
      fi
    fi
  done
}
llama_watchdog &
LLAMA_WD_PID=$!

echo "서버 실행 중 (launchd 관리, llama wd $LLAMA_WD_PID, 외부노출=Tailscale Funnel)" >> "$APP_LOG"

# launchd가 이 스크립트 생명주기를 관리 — uvicorn 종료 시 launchd가 재시작
wait $APP_PID
