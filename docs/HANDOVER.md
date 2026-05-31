# ANTIEGG B2B 어드민 — 인수인계 문서

이 문서는 ANTIEGG B2B 어드민의 운영을 누군가에게 넘길 때 필요한 전체 그림이다. 시스템이 무엇이고, 어떻게 가동되고, 무엇이 고장날 수 있고, 어떻게 복구하는지를 다룬다.

비밀번호·토큰·앱 비밀번호 값은 이 문서에 적지 않는다. 위치만 표기한다 (`.env`, 1Password 등).

---

## 1. 시스템 개요

- **목적**: B2B 문의 접수 → AI 회신 초안 → 견적/계약 미리보기 → 전자서명까지의 운영 흐름을 1인 디렉터가 처리하도록 자동화
- **원칙**: 판단은 사람, 실행은 AI. **메일 발송은 항상 사람이 Gmail에서 확인하고 누른다** — 어드민은 Gmail 임시보관에 초안만 저장
- **규모**: 월 5건 기준, 1인 운영
- **운영 위치**: 운영자 본인의 Mac에서 launchd로 상시 가동, **Tailscale Funnel**로 외부 공개
- **공개 주소**: `https://antiegg-b2b.tail4297cb.ts.net`
- **데이터**: 2026-05 노션 "B2B 대시보드 상세" 실데이터 243건을 임포트해 운영 중 (테스트 데이터 아님)

---

## 2. 아키텍처

```
                 [ Tailscale Funnel ]
            antiegg-b2b.tail4297cb.ts.net (HTTPS 자동)
                          │
                          ▼
                  ┌───────────────┐
  Gmail IMAP ───▶ │  FastAPI :8000 │ ───▶ SQLite (b2b.db, WAL)
   (수신/Draft)   │  (uvicorn)     │
                  └───────┬───────┘
                          │ HTTP
                          ▼
                  ┌───────────────┐
                  │ llama-server  │  Qwen2.5-7B Q4_K_M (CPU)
                  │ :8080         │
                  └───────────────┘

  LaunchAgent com.antiegg.b2b ──▶ scripts/server.sh
                  ├─ llama-server  (+ llama_watchdog 60s)
                  └─ uvicorn (FastAPI)

  LaunchAgent com.antiegg.tailscaled ──▶ tailscaled (userspace)
                  └─ Funnel: 공개 HTTPS → 127.0.0.1:8000

  APScheduler (FastAPI 내부) ── 14개 잡, 새벽 03:00~03:50 + 5분/1시간 폴링
```

**구성 컴포넌트**
- FastAPI + Jinja2 + HTMX — 어드민 UI/라우트 (`main.py`, `templates/`)
- llama.cpp (llama-server) — Qwen2.5-7B 4bit, CPU 모드(`--n-gpu-layers 0 --threads 8`). Metal GPU 크래시 이력으로 CPU 전용
- SQLite (WAL) — 단일 DB 파일 `b2b.db`. `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000ms`
- APScheduler — FastAPI lifespan으로 기동, 14개 잡 (메일 폴링, 트리거 처리, 백업, 로그 회전 등)
- Gmail IMAP — 수신 + Draft 저장 (SMTP 미사용, 앱 비밀번호 방식)
- **Tailscale Funnel** — 외부 접근. userspace 모드 tailscaled를 별도 LaunchAgent로 띄우고, Funnel로 :8000을 공개 HTTPS로 노출. 무료·상시 연결(ngrok과 달리 2시간 끊김 없음). 인증서 자동(Let's Encrypt)

> 외부 노출은 2026-05 ngrok에서 Tailscale Funnel로 전환됨. ngrok 관련 코드·watchdog은 `server.sh`에서 제거됨.

---

## 3. 빠른 시작 (새 머신에 처음 띄울 때)

```bash
git clone https://github.com/ruruzene-del/ANTIEGG--B2B-ADMIN
cd ANTIEGG--B2B-ADMIN

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# .env 채우기 (아래 4번 항목 참고)

# llama-server (Homebrew) + 모델 파일
brew install llama.cpp
mkdir -p models
# Qwen2.5-7B-Instruct Q4_K_M (분할 2개)를 models/에 배치 (약 4.3GB)

# 앱 상시 가동 (launchd)
cp scripts/com.antiegg.b2b.plist.example ~/Library/LaunchAgents/com.antiegg.b2b.plist
launchctl load ~/Library/LaunchAgents/com.antiegg.b2b.plist
```

### 외부 노출 (Tailscale Funnel) 셋업

```bash
brew install tailscale

# userspace 데몬용 LaunchAgent (root 불필요 — TUN 안 만들고 Funnel만)
cp ~/Library/LaunchAgents/com.antiegg.tailscaled.plist ...  # 기존 plist 참고
launchctl load ~/Library/LaunchAgents/com.antiegg.tailscaled.plist

SOCK=/opt/homebrew/var/run/tailscaled.socket
tailscale --socket=$SOCK up --operator=$USER --hostname=antiegg-b2b  # 브라우저 로그인
tailscale --socket=$SOCK set --accept-dns=false                      # 서버 노드: OS DNS 안 건드림
tailscale --socket=$SOCK funnel --bg 8000                            # 최초 1회 admin에서 Funnel 활성화 링크 클릭 필요
```

기동 후 `http://127.0.0.1:8000`(로컬) 또는 `https://antiegg-b2b.tail4297cb.ts.net`(공개)로 접근.

> tailscaled plist: `--tun=userspace-networking --socket=/opt/homebrew/var/run/tailscaled.socket --statedir=/opt/homebrew/var/lib/tailscale`. Funnel 설정은 statedir에 영구 저장돼 데몬 재시작 시 자동 복구된다.

---

## 4. 자격증명 / 외부 서비스

값은 **이 문서에 적지 않는다**. 1Password / 운영자 보관함에서 받아 `.env`에 채운다.

| 항목 | 위치 | 발급/관리 |
|---|---|---|
| Gmail 앱 비밀번호 | `.env` `GMAIL_APP_PASSWORD` | Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 |
| Gmail 수신 라벨 | `.env` `B2B_LABEL` (기본 `B2B_INQUIRY`) | Gmail 필터로 B2B 문의를 해당 라벨로 자동 분류 |
| Notion API | `.env` `NOTION_TOKEN` / `NOTION_DB_ID` | notion.so/my-integrations 내부 통합 토큰. 임포트용(6번 참고) |
| Tailscale 계정 | (계정) | nmwc.ai@ 개인 계정에 머신 `antiegg-b2b` 등록. Funnel 활성화는 tailnet당 1회 |
| 회사/디렉터 정보 | `.env` fallback + `/settings` DB | settings 테이블 우선, .env 폴백 |
| Slack Webhook | `.env` `SLACK_WEBHOOK` | **보류** — 워크스페이스 앱 한도 이슈 (8번 참고) |
| 앱 launchd plist | `~/Library/LaunchAgents/com.antiegg.b2b.plist` | git 비추적. 절대 경로 들어있음 — 머신 이전 시 수정 필요 |
| tailscaled plist | `~/Library/LaunchAgents/com.antiegg.tailscaled.plist` | git 비추적. userspace 데몬 |
| 모델 파일 | `models/qwen2.5-7b-instruct-q4_k_m-*.gguf` | Hugging Face에서 다운로드, git 비추적 |

---

## 5. 데이터 흐름

### 5.1 Stage 전이 (전부 수동 — 자동 전환 없음)

```
REVIEWING ──▶ REPLIED ──▶ NEGOTIATING ──▶ QUOTED ──▶ CONTRACTING ──▶ SIGNED ──▶ CLOSED_WON
                │                          │
                ▼ (7일 무응답)              ▼ (7일 무응답)
          KNOCK_REPLY               KNOCK_QUOTE
                │                          │
                ▼ (추가 7일 무응답)         ▼
                       CLOSED_LOST
```

Stage 변경은 항상 어드민 UI에서 수동. 자동 전환은 KNOCK → CLOSED_LOST 한 줄만 (scheduler.check_closed_lost).

### 5.2 트리거 상태머신 (5분 폴링)

각 트리거(`reply_send`, `knock_send`, `quote_gen`, `contract_gen`, `contract_send`)는 다음 상태를 가진다:

```
IDLE ──▶ PENDING ──▶ PROCESSING ──▶ DONE | DRAFT | ERROR
       (사람이 누름)  (5분 잡이 픽업)  (성공/실패)
```

- `*_send` 류는 성공 시 **DRAFT** (Gmail 임시보관 저장). 발송은 사람이 Gmail에서.
- `*_gen` 류는 성공 시 **DONE** (HTML 미리보기 준비됨).
- 트리거가 PROCESSING에서 멈춰있으면 7번(트러블슈팅) 참고.

> 데이터 임포트 시엔 모든 트리거를 IDLE로 두어야 한다. PENDING으로 남으면 5분 잡이 픽업해 의도치 않은 초안/서명토큰을 만든다. (`notion_import.py`는 IDLE로 적재)

### 5.3 APScheduler 잡 (총 14개)

| 시각 / 주기 | 잡 | 동작 |
|---|---|---|
| 5분 | process_reply_send / knock_send / quote_gen / contract_gen / contract_send | PENDING 트리거 처리 |
| 1시간 | poll_inbox | Gmail IMAP에서 새 B2B 메일 수신 → AI 파싱 → 딜 생성 |
| 매일 03:00 | ingest_sent_examples | Gmail SENT에서 ANTIEGG 회신을 few-shot 사례로 흡수 |
| 매일 03:30 | daily_backup | SQLite + ai_context → iCloud Drive |
| 매일 03:45 | purge_old_errors | 30일 지난 에러 로그 삭제 |
| 매일 03:50 | daily_log_rotate | app/llama 로그 회전 (1MB 이상만, 14일 보관) |
| 매일 09:00 / 18:00 | daily_reminder | 미발송 회신 초안 재알림 — **Slack 보류라 현재 no-op** |
| 매일 09:00 | check_no_response / check_closed_lost | KNOCK 또는 CLOSED_LOST 전환 |

> `log_rotate.py`의 회전 대상에 `ngrok.log`가 잔재로 남아있으나 ngrok 제거 후 더는 안 쌓여 무해(스킵됨). Funnel 로그는 `/opt/homebrew/var/log/tailscaled.log`(프로젝트 밖).

### 5.4 DB 스키마 요약

- **deals** — 딜 1건. company/contact/email/stage/summary + reply/knock draft + cond_* (계약 조건) + trigger_* (트리거 상태) + sign_token (전자서명 1회용) + **notion_page_id** (노션 임포트/재동기화 키)
- **activities** — 딜별 이벤트 로그. type + payload(JSON)
- **errors** — DB 적재 에러 로그. install_db_handler가 모든 logger.error를 픽업
- **settings** — `/settings`에서 편집한 회사/디렉터 정보 (.env 폴백)

---

## 6. 일상 운영 절차

**매일 (아침에)**
- 인박스(`/`) — "지금 해야 함" 그룹 확인. 새 문의는 자동으로 들어와 있어야 함
- 인박스 상단 위젯이 "최근 24h 에러 N건" 표시하면 `/errors` 클릭
- 회신/노크 초안이 떠 있으면 Gmail 임시보관에서 확인 → 수정 후 발송 → 어드민에서 Stage 수동 변경

**주 1회**
- iCloud의 백업 폴더에 어제 새벽 03:30 백업 파일이 있는지 확인
- 공개 URL이 살아있는지 외부망(LTE 등)에서 `https://antiegg-b2b.tail4297cb.ts.net` 접속 한 번

**화면 구성**
- 사이드바: 인박스 · 파이프라인 · 회사 · 세팅
- 파이프라인: 활성(진행중)만 기본 표시, 완료·종료는 "완료·종료 표시" 토글
- 회사: 진행중 회사 우선 + 검색 + 아카이브 접기
- 딜 패널 하단 "이 딜 삭제" 버튼으로 영구 삭제 가능(확인 후)

**수동 작업 (UI 메뉴 없음 — URL/라우트 직접)**
- 백업 즉시 실행: `/settings` "지금 백업" 버튼 (또는 `POST /admin/backup`)
- few-shot 사례 관리: `/examples` 직접 접속 (사이드바 메뉴는 제거 — 답장 톤 큐레이터 전용)
- 사례 즉시 수집: `POST /admin/ingest-sent?limit=N` — **CPU 추론이라 3~5분 끊김 주의** (매일 03:00 자동 수집되므로 평소 불필요)

---

## 7. 트러블슈팅

### 메일이 안 들어옴
1. `/errors`에서 `poll_inbox IMAP 실패` 있는지 확인 — 있으면 Gmail 앱 비밀번호 만료/회수 가능성
2. Gmail에서 `B2B_INQUIRY` 라벨에 메일이 들어와 있는지 — 라벨 필터 자체가 깨졌을 수 있음
3. `app.log`에 `[poll_inbox]` 로그가 매 시간 찍히는지

### AI 응답이 이상함
1. llama-server 살아있나: `curl http://127.0.0.1:8080/health` → 200이어야 함
2. 죽었으면 watchdog가 60초 안에 살림. 안 살아나면 `app.log`의 `[watchdog]` 로그 확인
3. 응답 품질이 떨어지면 `/examples`에서 사례를 점검. 잘못된 사례는 삭제, 좋은 회신을 수동 추가

### 트리거가 PROCESSING에서 멈춤
1. APScheduler가 살아있나: 마지막 `process_*` 로그가 5분 안에 찍혔어야 함
2. 멈췄으면 launchd 재시작 (8번 참고). uvicorn이 살아있어도 scheduler thread가 죽었을 수 있음
3. PROCESSING은 자동 회복 안 됨 — 수동으로 PENDING 또는 IDLE로 되돌려야 재시도

### 외부 URL(Tailscale Funnel)이 안 열림
1. 데몬 살아있나: `pgrep -fl tailscaled` — 없으면 `launchctl load ~/Library/LaunchAgents/com.antiegg.tailscaled.plist`
2. Funnel 켜져 있나: `tailscale --socket=/opt/homebrew/var/run/tailscaled.socket funnel status` → `Funnel on` + `proxy http://127.0.0.1:8000`
3. **이 맥에서 직접 curl은 로컬 DNS 특성상 실패할 수 있다(정상).** 외부 도달 확인은 다른 망(LTE)이나 `curl --resolve antiegg-b2b.tail4297cb.ts.net:443:<ingress-ip>` 로
4. tailnet에서 Funnel이 꺼졌으면 admin console에서 재활성화

### 백업이 안 됨
1. `/settings` 백업 카드의 "마지막 백업"이 24시간 넘으면 launchd 잡이 안 돌고 있음
2. launchd 재시작 (8번)
3. iCloud 동기화 문제일 수도 — Finder에서 백업 폴더 직접 확인

### 견적/계약 미리보기가 깨짐
1. 딜의 `cond_*` 필드가 비어있나 — 패널에서 계약 조건 채우기
2. 견적은 `trigger_quote_gen=DONE`이어야 미리보기 버튼 노출

---

## 8. 변경 시 주의사항

### 라우트 변경 후 launchctl reload 필수
```bash
launchctl unload ~/Library/LaunchAgents/com.antiegg.b2b.plist
launchctl load ~/Library/LaunchAgents/com.antiegg.b2b.plist
```
`main.py`의 라우트는 프로세스 시작 시점에 픽스된다. Jinja 템플릿은 디스크에서 hot-reload 되지만, 신규 라우트는 reload 없이는 404로 나오고 새 템플릿만 보이는 mixed-signal 상태가 된다. llama 콜드 로딩 ~1분.

> 앱 reload는 tailscaled(별도 LaunchAgent)를 건드리지 않으므로 Funnel은 끊기지 않는다.

### 모델 교체
- `models/`에 새 GGUF 파일 배치
- `scripts/server.sh`의 `MODEL` 변수 수정
- launchd 재시작

### 공개 주소(도메인) 변경
- 앱은 공개 주소를 `.env`의 `APP_BASE_URL` 한 곳으로만 참조 → 값 변경 + reload면 끝
- 커스텀 도메인(b2b.antiegg.kr)은 Tailscale Funnel로는 불가(ts.net 전용). 필요 시 Cloudflare Tunnel(antiegg.kr 전체 이전) 또는 유료 터널(CNAME 한 줄)로 전환
- 이미 발송한 서명 링크는 발급 시점의 URL이 박히므로, 도메인 바꿔도 기존 ts.net 터널은 한동안 유지

### 외부 서비스 추가 (Slack 등)
- `.env`에 키 추가 → `.env.example`에도 동기화
- 코드에서 키 누락 시 no-op 되도록 작성 (현재 Slack도 미설정 시 조용히 패스)

### Slack 활성화 (현재 보류 중)
ANTIEGG 워크스페이스 무료 플랜이 앱 10개 한도라 Incoming Webhook 추가 불가. 4가지 대안:
1. **Bot Token 방식** — 기존 앱의 OAuth & Permissions에서 `xoxb-` 발급 → `chat.postMessage` API (한도 영향 없음, 봇 `/invite` 필요)
2. ANTIEGG에서 안 쓰는 앱 1개 제거
3. NMWC 워크스페이스 활용
4. 워크스페이스 업그레이드

결정되면 `app/integrations/slack.py`의 통합 함수에 연결. 활성화하면 daily_reminder 재알림도 동작한다.

---

## 9. 백업·복구

### 백업 위치
- 기본: `~/Library/Mobile Documents/com~apple~CloudDocs/antiegg-b2b-backups/` (iCloud Drive)
- 변경: `.env`의 `BACKUP_DIR`
- 회전: 30일 (`BACKUP_RETENTION_DAYS`)
- 대상: `b2b.db` (sqlite3 `.backup` 동시 쓰기 안전), `ai_context/reply_examples.json`, `ai_context/antiegg_style_guide.md`

### 복구 절차
```bash
# 1. 서비스 중지
launchctl unload ~/Library/LaunchAgents/com.antiegg.b2b.plist

# 2. DB 교체
cp ~/Library/Mobile\ Documents/com~apple~CloudDocs/antiegg-b2b-backups/b2b_YYYYMMDD_HHMMSS.db b2b.db
# WAL 사이드카는 삭제 (백업본은 이미 통합 스냅샷)
rm -f b2b.db-wal b2b.db-shm

# 3. ai_context 교체
cp .../reply_examples_YYYYMMDD_*.json ai_context/reply_examples.json
cp .../antiegg_style_guide_YYYYMMDD_*.md ai_context/antiegg_style_guide.md

# 4. 무결성 확인
sqlite3 b2b.db "PRAGMA integrity_check;"   # → ok

# 5. 재기동
launchctl load ~/Library/LaunchAgents/com.antiegg.b2b.plist
```

---

## 10. 데이터 임포트 (노션)

`scripts/notion_import.py` — 노션 "B2B 대시보드 상세" DB를 deals로 적재. urllib만 사용(추가 의존성 없음).

```bash
python3 scripts/notion_import.py          # 진단(읽기 전용): 행 수·현황 분포·매핑 미리보기
python3 scripts/notion_import.py --apply  # 기존 deals/activities wipe 후 적재
```

- `.env`의 `NOTION_TOKEN`(내부 통합) + `NOTION_DB_ID` 사용. 노션 DB를 통합에 Connections로 공유해야 API가 읽는다.
- 현황(select) → stage 매핑: 진행완료→CLOSED_WON, 제안거절·미지정→CLOSED_LOST, 의견조율→NEGOTIATING, 제휴진행중→CONTRACTING.
- 칼럼 없는 노션 필드(입금/정산/세금계산서/EDITOR·PM·BD/견적·계약 URL)는 `summary`에 텍스트로 보존.
- `notion_page_id`로 재동기화 대비. **--apply는 기존 딜을 전부 지우므로** 실행 전 백업(9번) 필수.

---

## 11. 참고

- 소스: https://github.com/ruruzene-del/ANTIEGG--B2B-ADMIN
- 운영 환경: macOS 13 Ventura + M2, Python 3.9, Homebrew llama.cpp / tailscale
- 메인 의존성: `requirements.txt`
- 백엔드 로직 진입점: `main.py`, `app/services/scheduler.py`
