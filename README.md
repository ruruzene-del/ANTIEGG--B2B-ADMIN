# ANTIEGG B2B ADMIN

B2B 문의 접수 및 운영 자동화를 위한 AI 기반 시스템

## 기능
- 이메일 문의 수집
- AI 응답 초안 생성
- Slack 알림
- 계약서 생성
- 전자서명 연동

## Stack
- Python
- Gmail API
- Slack API
- Gemini API

## 운영

### 서버 재시작
```
launchctl unload ~/Library/LaunchAgents/com.antiegg.b2b.plist
launchctl load ~/Library/LaunchAgents/com.antiegg.b2b.plist
```
**`main.py` 라우트 변경을 머지한 뒤에는 반드시 reload 해야 한다** — Jinja 템플릿은 디스크에서 hot-reload 되지만 라우트는 프로세스 시작 시점에 픽스되어, reload 없이는 신규 라우트가 404로 나오고 새 템플릿만 보이는 mixed-signal 상태가 된다. llama 콜드 로딩 ~45초.

### 자동 운영 잡 (APScheduler)
- `03:00` Gmail SENT few-shot 자동 수집
- `03:30` SQLite + ai_context 백업 (iCloud) — 세팅 페이지에서 수동 트리거 가능
- `03:45` 30일 지난 에러 로그 정리
- `03:50` app/llama/ngrok 로그 회전 (copytruncate, 1MB↑만, 14일 보관)

### llama-server watchdog
`scripts/server.sh`의 `llama_watchdog`이 60초 주기로 `kill -0`로 프로세스 생존 확인. 죽으면 자동 재시작 (콜드 ~45초). 헬스 200은 죽음 판정에 안 씀 — CPU 추론 3~5분 busy를 죽음으로 오인 방지.
