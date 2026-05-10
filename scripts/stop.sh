#!/bin/bash
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$PROJECT_DIR/.pids"

if [ -f "$PID_FILE" ]; then
  echo "서버 종료 중..."
  while read -r pid; do
    kill "$pid" 2>/dev/null && echo "  killed $pid" || true
  done < "$PID_FILE"
  rm -f "$PID_FILE"
else
  echo "실행 중인 서버 없음 (.pids 파일 없음)"
fi

lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:4040 | xargs kill -9 2>/dev/null || true
pkill -f "caffeinate" 2>/dev/null || true

echo "완료"
